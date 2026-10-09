# -*- coding: utf-8 -*-
"""感情エンジンの複数人版。Discordの雑談チャンネル1つで、人格（persona.py）を持ったBotとして話す。

- 返すかどうか・型は decide.py の規則が決める。Jev は読み取り、LLM は文面書きだけ
- 発言は数秒溜めてから1回だけ判断する（全員に返信しない）
- **安全装置**: 1人あたり／全体の1日上限、オーナーの停止コマンド（!minato stop / start / status）
- 体の変数（body.py）が寝る・食べる・抜けるを決める。人格と呼ばれ方は persona.py に置く
"""
__version__ = "minato 0.5.3"

import asyncio
import json
import logging
import os
import random
import re
from collections import deque
from datetime import datetime, timedelta, timezone

import aiohttp
import discord

import body
import decide
import persona
import ratings

HERE = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
CONFIG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
STATE_PATH = os.path.join(HERE, "state.json")
RECENT = 20          # 覚えておく直近の発言数（LLMに渡す）
SHARE_WINDOW = 12    # 発言シェアを数える範囲
QUIET_SEC = 6        # 最後の発言からこれだけ静かになったら判断する
MAX_WAIT_SEC = 20    # 会話が続いていても、これだけ溜めたら判断する

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("minato")


def now():
    return datetime.now(JST).replace(tzinfo=None)


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except (IOError, ValueError):
        return {"counts": {}, "stopped": False}


def save_state(s):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


# --- Jev: 話題の読み取り（返すかどうかは決めない） -------------------------------
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_INTEREST = ("会話の最後の話題は、睡眠・夜更かし・課題・授業・バイト・お金・ゲーム・食べ物・ラジオ・銭湯・"
                "好きなキャラや推し・好みのタイプなど、"
                "大学生が日常で愚痴ったり盛り上がったりする身近な話題か。")


async def jev_interest(text):
    """確率。**失敗は None（棄権）で、偽ではない。**"""
    key = CONFIG.get("typesafe_key")
    if not key:
        return None
    body = {"state": text, "model": "jev-latest",
            "questions": {"interest": {"type": "noul", "instructions": JEV_INTEREST}}}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
            async with sess.post(JEV_URL, json=body, headers={"Authorization": "Bearer " + key}) as r:
                if r.status != 200:
                    log.warning("Jevが %d を返した", r.status)
                    return None
                return float((await r.json())["answers"]["interest"]["noul"])
    except Exception as e:
        log.warning("Jev失敗: %s", e)
        return None


# --- LLM: 文面書き -------------------------------------------------------------
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = ["qwen/qwen3.8-omni-flash", "qwen/qwen3.7-flash"]
TRIES = 3
# ため口の人格。敬語が出たら引き直す
POLITE = re.compile(r"(です|ます|ました|ません|でした|ください|でしょう)[ねよかがけど]?(?=[。！？!?…、~〜\s]|$)")


async def _post(messages):
    payload = {"messages": messages, "temperature": 1.0, "reasoning": {"enabled": False},
               "response_format": {"type": "json_object"}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as sess:
        for model in OR_MODELS:
            try:
                async with sess.post(OR_URL, json={**payload, "model": model},
                                     headers={"Authorization": "Bearer " + CONFIG["openrouter_key"]}) as r:
                    res = await r.json()
                text = res["choices"][0]["message"]["content"]
                if text and text.strip():
                    return text.strip().removeprefix("```json").removesuffix("```").strip()
            except Exception as e:
                log.warning("%s 失敗: %s", model, e)
    return None


AI_Q = re.compile(r"AI|ＡＩ|bot|ボット|人間|ロボ", re.I)
HUMAN_CLAIM = re.compile(r"人間(だ|です|やで)|普通にいる|AIじゃない|ＡＩじゃない|botじゃない|ボットじゃない|^は[？?]\s*別に$",
                         re.I)


# 自分を名前で呼ぶ（「凪のこと」「ナギは」）。三人称はキャラに合わない
SELF_NAME = re.compile(f"(?:{persona.NAMES.pattern})(?:のこと|は|が|も|的に)", re.I)


def asks_ai(text):
    return bool(AI_Q.search(text))


def parse(text, kind, target_text=""):
    """型に合った中身を取り出す。合わなければ None。"""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if kind == "reaction":
        e = obj.get("reaction")
        return e if e in persona.REACTIONS else None
    msgs = [m.strip() for m in obj.get("messages") or [] if isinstance(m, str) and m.strip()]
    if not msgs or any(POLITE.search(m) or SELF_NAME.search(m) for m in msgs):
        return None
    if asks_ai(target_text) and any(HUMAN_CLAIM.search(m) for m in msgs):
        return None   # 人間だと偽らない（このサーバーではAIが混ざっていると告知している）
    return msgs[:1] if kind == "short" else msgs[:2]


KIND_LABEL = {"reaction": "リアクション", "short": "一言", "normal": "通常"}


def line(m, t):
    """会話の1行。いつの発言かを先頭に付ける（時刻が無いと、昨日の話と今の話の区別が付かない）。"""
    sec = (t - datetime.fromisoformat(m["at"])).total_seconds() if m.get("at") else 0
    return f"［{decide.ago(sec)}］{m['author']}: {m['text']}"


async def write(recent, target, kind, pres, note=None, body_text=None, guide=""):
    """target が None なら誰かへの返事ではない（抜ける・戻る時の独り言）。"""
    t = now()
    log_lines = "\n".join(line(m, t) for m in recent)
    to = (f"【返信先】\n{line(target, t)}" if target else
          "【返信先】なし。誰かの発言への返事ではない。直近の会話に答えたり、自分の発言を繰り返したりしない")
    turn = (f"【今の時刻】{t:%H:%M}\n【直近の会話】\n{log_lines}\n\n{to}\n\n"
            f"【型】{KIND_LABEL[kind]}\n【今の様子】{pres}\n"
            f"【呼ばれ方】このサーバーでのあなたの表示名は {persona.NICK}。会話の中でそう呼ばれたら自分のこと")
    if body_text:
        turn += "\n【体の状態】" + body_text
    if note:
        turn += "\n【補足】" + note
    if kind == "reaction":
        turn += "\n【絵文字の候補】" + " ".join(persona.REACTIONS)
    target_text = target["text"] if target else ""
    if asks_ai(target_text):
        turn += ("\n【注意】返信先は、あなたがAIかどうかを聞いている。**人間だと言ってはいけない**（嘘になる）。"
                 "「どうだろね」「当ててみ」のようにはぐらかすか、軽く認める。話をそらして否定もしない")
    messages = [{"role": "system", "content": persona.PERSONA + persona.INSTR + guide},
                {"role": "user", "content": turn}]
    for _ in range(TRIES):
        text = await _post(messages)
        if not text:
            return None
        out = parse(text, kind, target_text)
        if out:
            return out
        log.info("型・口調が合わない。引き直す: %r", text[:80])
    return None   # 全滅なら黙る（定型で埋めない。黙っても不自然ではない）


# --- 評価（右クリックメニュー。本人にしか見えない） -------------------------------
RATINGS_PATH = os.path.join(HERE, "ratings.jsonl")


class CommentModal(discord.ui.Modal, title="ひと言（空欄でもOK）"):
    comment = discord.ui.TextInput(label="どこが気になったか", style=discord.TextStyle.paragraph,
                                   required=False, max_length=300)

    def __init__(self, rec):
        super().__init__()
        self.rec = rec

    async def on_submit(self, interaction):
        self.rec["comment"] = self.comment.value.strip()
        ratings.append(RATINGS_PATH, self.rec)
        log.info("評価: %s「%s」%s", self.rec["label"], self.rec["text"][:40],
                 f"— {self.rec['comment']}" if self.rec["comment"] else "")
        await interaction.response.send_message(f"「{self.rec['label']}」で記録した。ありがとう", ephemeral=True)


class RateView(discord.ui.View):
    def __init__(self, rec):
        super().__init__(timeout=300)
        for label in ratings.LABELS:
            b = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            b.callback = self.make_cb(label)
            self.add_item(b)
        self.rec = rec

    def make_cb(self, label):
        async def cb(interaction):
            await interaction.response.send_modal(CommentModal({**self.rec, "label": label}))
        return cb


ANNOUNCE = {"寝た": "眠気が限界なので、今から寝る。会話から抜ける一言だけ",
            "落ちる": "人と話すのがしんどくなったので、少し会話から抜ける。重くならない一言だけ",
            "コンビニに行く": "腹が減ったので、今からコンビニに行く。一言だけ",
            "戻った": "コンビニから戻った／少し休んで戻った。一言だけ"}
ACTIVE_WINDOW = timedelta(minutes=20)   # この間に人が話していれば、抜ける・戻る時に一言添える
ANNOUNCE_GAP = timedelta(minutes=3)     # 自分がこの間に喋っていたら、抜ける・戻る一言は省く（直前の返事をなぞるため）


# --- Discord -----------------------------------------------------------------
class Minato(discord.Client):
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

    async def rate(self, interaction, message: discord.Message):
        if self.ch is None or message.channel.id != self.ch.id:
            await interaction.response.send_message(f"評価は #{self.ch} の発言だけ", ephemeral=True)
            return
        before = [m for m in self.recent if m["id"] < message.id][-6:]
        rec = {"at": now().isoformat(timespec="seconds"), "rater_id": interaction.user.id,
               "message_id": message.id, "author": message.author.display_name,
               "is_me": message.author == self.user, "text": self.describe(message),
               "context": [f"{m['author']}: {m['text']}" for m in before]}
        await interaction.response.send_message(
            f"> {rec['text'][:80]}\nこの発言はどうだった？（あなたにしか見えません）",
            view=RateView(rec), ephemeral=True)

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

    async def owner_command(self, m):
        cmd = m.content.split()[1] if len(m.content.split()) > 1 else "status"
        if cmd == "ratings":
            await m.reply("-# " + ratings.format_summary(ratings.summary(ratings.load(RATINGS_PATH)))
                          .replace("\n", "\n-# "), mention_author=False)
            return
        if cmd in ("stop", "start"):
            self.s["stopped"] = cmd == "stop"
            save_state(self.s)
        c = self.s.get("counts", {})
        await m.reply(f"-# {__version__}: {'停止中' if self.s.get('stopped') else '稼働中'}"
                      f"・今日 {c.get('total', 0)}/{decide.TOTAL_DAILY_MAX} 回・{decide.presence(now())}"
                      f"・後で返す {len(self.s.get('deferred', []))} 件"
                      f"・{body.as_numbers(self.body, now())}",
                      mention_author=False)

    async def on_message(self, m):
        if self.ch is None or m.channel.id != self.ch.id:
            return
        if m.content.startswith("!minato"):
            if str(m.author.id) == str(CONFIG["owner_id"]):
                await self.owner_command(m)
            return
        if m.flags.ephemeral or m.interaction_metadata:   # 評価の確認など、本人にしか見えない応答は会話ではない
            return
        e = self.entry(m)
        self.recent.append(e)
        if m.author.bot:          # 自分と他のBotには反応しない
            return
        e["mentions_me"] = self.user in m.mentions or bool(persona.NAMES.search(m.content))
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
            interest = await jev_interest("\n".join(f"{m['author']}: {m['text']}" for m in recent[-6:]))
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
        for i, text in enumerate(out):
            async with self.ch.typing():
                await asyncio.sleep(decide.typing_sec(text, act["device"]))
            if i == 0 and (moved_on or plan["addressed"]):
                await msg.reply(text, mention_author=False)
            else:
                await self.ch.send(text)

    async def defer_loop(self):
        """1分ごとに体の変数を進める（寝る・食べる・落ちるを自分で選ぶ）。
        寝ている・バイト中・離席中に呼ばれた分は、終わってから返す（最新の1件にまとめて）。"""
        while not self.is_closed():
            await asyncio.sleep(60)
            try:
                async with self.lock:
                    if self.s.get("stopped"):
                        continue
                    t = now()
                    await self.live(t)
                    act = decide.activity(t, self.body)
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
                                      "act": act, "at": t}, note)
            except Exception:
                log.exception("後で返す処理に失敗")

    async def live(self, t):
        """体の変数を1分進める。抜ける・戻る時は、会話が動いていれば一言添える。"""
        b = self.body
        sched = decide.schedule(t)
        on, at = decide.alarm(t)
        ev = body.tick(b, t, sched, on, at)
        if self.s.get("last_sched") == "バイト中" and sched != "バイト中":
            ev += body.after_baito(b, t)
        self.s["last_sched"] = sched
        if ev:
            log.info("体: %s（%s）", "・".join(ev), body.as_numbers(b, t))
        save_state(self.s)
        say = [e for e in ev if e in ANNOUNCE]
        humans = [m for m in self.recent if not m["bot"]]
        if not say or not humans or self.s.get("stopped"):
            return
        last = discord.utils.snowflake_time(humans[-1]["id"]).astimezone(JST).replace(tzinfo=None)
        if t - last > ACTIVE_WINDOW:
            return
        if self.last_sent_at and t - self.last_sent_at < ANNOUNCE_GAP:
            log.info("体の都合（%s）: 直前に喋ったので一言は省く", say[0])
            return
        out = await write(list(self.recent), None, "short", decide.activity(t, b)["label"],
                          ANNOUNCE[say[0]], body_text=body.as_feelings(b, t))
        if out:
            log.info("体の都合で一言（%s）→ %s", say[0], out[0])
            async with self.ch.typing():
                await asyncio.sleep(decide.typing_sec(out[0], "スマホ"))
            await self.ch.send(out[0])
            self.last_sent_at = now()


if __name__ == "__main__":
    Minato().run(CONFIG["token"], log_handler=None)
