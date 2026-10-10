"""Discord クライアント本体: 発言を溜めて判断し、返す。機能ごとの振る舞いは各 Mixin にある。"""
import asyncio
import logging
import random
from collections import deque
from datetime import datetime

import discord

import persona
from app import __version__
from app.jev import JEV_INVITE, jev, jev_interest
from app.learning import LearningMixin
from app.life import LifeMixin
from app.llm import write
from app.rating import RatingMixin
from app.snapshot import SnapshotMixin
from app.status import StatusMixin
from app.store import CONFIG, JST, load_state, now, save_state
from engine import body, decide

log = logging.getLogger("minato")

RECENT = 20          # 覚えておく直近の発言数（LLMに渡す）
SHARE_WINDOW = 12    # 発言シェアを数える範囲
QUIET_SEC = 6        # 最後の発言からこれだけ静かになったら判断する
MAX_WAIT_SEC = 20    # 会話が続いていても、これだけ溜めたら判断する


class Minato(RatingMixin, LearningMixin, StatusMixin, LifeMixin, SnapshotMixin, discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.s = load_state()
        self.body = self.s.setdefault("body", body.new(now()))   # 体の変数。state.json に一緒に残す
        self.ch = None
        self.recent = deque(maxlen=RECENT)
        self.batch = []          # 判断待ちの人間の発言
        self.batch_task = None
        self.last_at = None
        self.lock = asyncio.Lock()
        self.last_sent_at = None   # 最後に送った時刻。気付くまでの間に別の返事を送っていたら、雑談の割り込みは捨てる
        self.tree = discord.app_commands.CommandTree(self)
        self.tree.add_command(discord.app_commands.ContextMenu(name="評価", callback=self.rate))
        self.synced = False

    @staticmethod
    def describe(m):
        """本文＋スタンプ・添付の説明。**スタンプや画像だけの発言は本文が空になる**ので、
        何が送られたかを言葉で渡す（空のまま渡すと、何にでも合う返事しか書けない）。"""
        parts = []
        ref = m.reference.resolved if m.reference else None
        if isinstance(ref, discord.Message):
            parts.append(f"（{ref.author.display_name}への返信）")
        if m.clean_content:
            parts.append(m.clean_content)
        parts += [f"［スタンプ「{s.name}」］" for s in m.stickers]
        for a in m.attachments:
            kind = (a.content_type or "").split("/")[0]
            parts.append({"image": "［画像］", "video": "［動画］", "audio": "［音声］"}.get(kind, "［ファイル］"))
        return " ".join(parts)[:400]

    def entry(self, m):
        return {"id": m.id, "at": m.created_at.astimezone(JST).replace(tzinfo=None).isoformat(timespec="seconds"),
                "author": m.author.display_name, "author_id": m.author.id,
                "bot": m.author.bot, "text": self.describe(m),
                "to_me": bool(m.reference and m.reference.resolved
                              and getattr(m.reference.resolved, "author", None) == self.user)}

    async def on_ready(self):
        self.ch = self.get_channel(int(CONFIG["channel_id"]))
        if self.ch and not self.recent:
            async for m in self.ch.history(limit=RECENT, oldest_first=False):
                self.recent.appendleft(self.entry(m))
        if self.ch and not self.synced:
            self.tree.copy_global_to(guild=self.ch.guild)
            try:
                await self.tree.sync(guild=self.ch.guild)
                self.synced = True
            except discord.HTTPException as e:
                log.error("右クリックメニューを登録できない: %s", e)
        if not hasattr(self, "_defer_loop"):
            self._defer_loop = asyncio.create_task(self.defer_loop())
        log.info("%s 起動。チャンネル=%s（%s）", __version__, self.ch,
                 "停止中" if self.s.get("stopped") else "稼働")

    async def on_message(self, m):
        if self.ch is None:
            return
        # ステータス用チャンネルではコマンドだけ受け付ける（会話には反応しない）
        in_status = str(m.channel.id) == str(CONFIG.get("status_channel_id"))
        if m.channel.id != self.ch.id and not (in_status and m.content.startswith("!minato")):
            return
        if m.content.startswith("!minato"):
            # 見るだけのコマンドは全員。止める・動かすは安全装置なのでオーナーだけ
            parts = m.content.split()
            owner_only = len(parts) > 1 and parts[1] in ("stop", "start")
            if not owner_only or str(m.author.id) == str(CONFIG["owner_id"]):
                await self.owner_command(m)
            return
        if m.flags.ephemeral or m.interaction_metadata:   # 評価の確認など、本人にしか見えない応答は会話ではない
            return
        e = self.entry(m)
        self.recent.append(e)
        if m.author.bot:          # 自分と他のBotには反応しない
            return
        e["mentions_me"] = self.user in m.mentions or bool(persona.NAMES.search(m.content))
        asyncio.create_task(self.learn_name(m))
        asyncio.create_task(self.learn_like(m))
        self.batch.append(e)
        self.last_at = now()
        if self.batch_task is None or self.batch_task.done():
            self.batch_task = asyncio.create_task(self.wait_and_decide(now()))

    async def wait_and_decide(self, started):
        """最後の発言から QUIET_SEC 静かになるか、MAX_WAIT_SEC 経ったら、1回だけ判断する。
        返すと決めたら、**気付くまでの時間**を置いてから書く（いつも即答しない）。"""
        while True:
            await asyncio.sleep(1)
            t = now()
            if (t - self.last_at).total_seconds() >= QUIET_SEC or (t - started).total_seconds() >= MAX_WAIT_SEC:
                break
        batch, self.batch = self.batch, []
        try:
            async with self.lock:
                plan = await self.plan(batch)
            if not plan:
                return
            wait = decide.notice_sec(plan["act"]["state"], random.random())
            log.info("%.0f秒後に読む", wait)
            await asyncio.sleep(wait)
            async with self.lock:
                await self.reply(plan)
        except Exception:
            log.exception("判断に失敗")

    async def plan(self, batch):
        """返すかどうかと型を決める。返さないなら None。返せない時の呼びかけは後で返す分に積む。"""
        if self.s.get("stopped") or not batch:
            return None
        t = now()
        target = next((e for e in reversed(batch) if e["mentions_me"] or e["to_me"]), batch[-1])
        addressed = target["mentions_me"] or target["to_me"]
        if not decide.budget_ok(self.s.setdefault("counts", {}), target["author_id"], t):
            log.info("上限に達したので黙る")
            return None
        act = decide.activity(t, self.body)
        recent = list(self.recent)
        # 名前ではなくIDで数える（サーバーでの表示名と、Botアカウントの名前が違うことがあるため）
        share, humans = decide.my_share([{"author": m["author_id"], "bot": m["bot"]}
                                         for m in recent[-SHARE_WINDOW:]], self.user.id)
        interest = None
        if not addressed and act["state"] not in ("寝ている", "バイト中", "離席中"):
            convo = "\n".join(f"{m['author']}: {m['text']}" for m in recent[-6:])
            interest, invite = await asyncio.gather(jev_interest(convo), jev(convo, JEV_INVITE))
            if invite is not None and invite >= decide.INTEREST_TRUE:
                addressed = True       # 「誰か話そう」は自分にも向いている
                log.info("その場の誰かへの呼びかけ（%.2f）→ 話しかけられた扱い", invite)
        kind = decide.choose(addressed, interest, share, humans, act["state"], random.random(),
                             self.body["energy"])
        log.info("判断: 宛先=%s 関心=%s シェア=%.2f 人数=%d 様子=%s → %s", addressed,
                 None if interest is None else round(interest, 2), share, humans, act["state"], kind)
        if kind == "defer":
            self.s.setdefault("deferred", []).append({**target, "at": t.isoformat(), "was": act["state"],
                                                       "why": self.body.get("away_why")})
            self.s["deferred"] = self.s["deferred"][-5:]
            save_state(self.s)
            return None
        if kind == "none":
            return None
        hot = interest is not None and interest >= decide.INTEREST_TRUE
        return {"target": target, "addressed": addressed, "kind": kind, "act": act, "at": t, "hot": hot}

    async def reply(self, plan, note=None):
        target, kind, act = plan["target"], plan["kind"], decide.activity(now(), self.body)
        if not plan["addressed"] and self.last_sent_at and self.last_sent_at > plan["at"]:
            log.info("気付くまでの間に別の返事を送ったので、割り込みは捨てる")
            return
        t = now()
        out = await write(list(self.recent), target, kind, act["label"], note,
                          body_text=body.as_feelings(self.body, now()))
        if not out:
            return
        log.info("返信先「%s」→ %s", target["text"][:60], out if kind == "reaction" else " ｜ ".join(out))
        if not decide.pending_alive(t, now()):
            log.info("書くのに時間がかかりすぎたので捨てる")
            return
        decide.count(self.s.setdefault("counts", {}), target["author_id"], t)
        body.spend_talk(self.body, 0 if kind == "reaction" else sum(map(len, out)), t, hot=plan.get("hot"))
        self.last_sent_at = now()
        save_state(self.s)
        msg = await self.ch.fetch_message(target["id"])
        if kind == "reaction":
            await msg.add_reaction(out)
            return
        # 会話が先に進んでいる時、または話しかけられた時は「返信」で宛先を示す
        moved_on = self.recent and self.recent[-1]["id"] != target["id"]
        sent = []
        for i, text in enumerate(out):
            async with self.ch.typing():
                await asyncio.sleep(decide.typing_sec(text, act["device"]))
            if i == 0 and (moved_on or plan["addressed"]):
                sent.append(await msg.reply(text, mention_author=False))
            else:
                sent.append(await self.ch.send(text))
        self.remember(sent, t, act, {"source": plan.get("source", "reply"), "kind": kind,
                                     "addressed": plan["addressed"], "hot": bool(plan.get("hot"))},
                      target["author_id"])

    async def defer_loop(self):
        """1分ごとに体の変数を進める（寝る・食べる・落ちるを自分で選ぶ）。
        寝ている・バイト中・離席中に呼ばれた分は、終わってから返す（最新の1件にまとめて）。"""
        while not self.is_closed():
            await asyncio.sleep(60)
            try:
                async with self.lock:
                    await self.post_status(now())       # 止めている間も様子は送る
                    if self.s.get("stopped"):
                        continue
                    t = now()
                    await self.live(t)
                    act = decide.activity(t, self.body)
                    await self.pick_up(t, act)
                    await self.start_topic(t, act)
                    old = self.s.get("deferred", [])
                    rest, due = decide.defer_due(old, t, act["state"])
                    if rest != old:
                        self.s["deferred"] = rest
                        save_state(self.s)
                    if not due:
                        continue
                    mins = int((t - datetime.fromisoformat(due["at"])).total_seconds() // 60)
                    note = decide.late_note(mins, due["was"], due.get("why"))
                    log.info("後で返す: %s（%d分前・%s）", due["text"][:40], mins, due.get("why") or due["was"])
                    await self.reply({"target": due, "addressed": True, "kind": "normal",
                                      "act": act, "at": t, "source": "late"}, note)
            except Exception:
                log.exception("後で返す処理に失敗")
