# -*- coding: utf-8 -*-
"""感情エンジンの複数人版。Discordの雑談チャンネル1つで、人格（persona.py）を持ったBotとして話す。

- 返すかどうか・型は decide.py の規則が決める。Jev は読み取り、LLM は文面書きだけ
- 発言は数秒溜めてから1回だけ判断する（全員に返信しない）
- **安全装置**: 1人あたり／全体の1日上限、オーナーの停止コマンド（!minato stop / start / status）
- 体の変数（body.py）が寝る・食べる・抜けるを決める。人格と呼ばれ方は persona.py に置く
"""
__version__ = "minato 0.9.0"

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

import affinity
import body
import decide
import names
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


JEV_INVITE = ("会話の最後の発言は、特定の相手ではなく、その場にいる誰かに話しかけて、"
              "話し相手や反応を求めているか（例: 誰か話そう、暇な人いる？、ねえ聞いて）。")


async def jev_interest(text):
    return await jev(text, JEV_INTEREST)


async def jev(text, question):
    """確率。**失敗は None（棄権）で、偽ではない。**"""
    return (await jev_many(text, {"q": question})).get("q")


async def jev_many(text, questions):
    """複数の問いを1回で聞く。{名前: 確率}。失敗した問いは入らない（棄権）。"""
    key = CONFIG.get("typesafe_key")
    if not key:
        return {}
    body = {"state": text, "model": "jev-latest",
            "questions": {k: {"type": "noul", "instructions": q} for k, q in questions.items()}}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
            async with sess.post(JEV_URL, json=body, headers={"Authorization": "Bearer " + key}) as r:
                if r.status != 200:
                    log.warning("Jevが %d を返した", r.status)
                    return {}
                ans = (await r.json())["answers"]
                return {k: float(ans[k]["noul"]) for k in questions if k in ans}
    except Exception as e:
        log.warning("Jev失敗: %s", e)
        return {}


# --- LLM: 文面書き -------------------------------------------------------------
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = ["qwen/qwen3.8-omni-flash", "qwen/qwen3.7-flash"]
TRIES = 3
# ため口の人格。敬語が出たら引き直す
POLITE = re.compile(r"(です|ます|ました|ません|でした|ください|でしょう)[ねよかがけど]?(?=[。！？!?…、~〜\s]|$)")


async def _post(messages, temperature=1.0):
    payload = {"messages": messages, "temperature": temperature, "reasoning": {"enabled": False},
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


# 突き放すだけの一言（「うるさい」「別に」「やめろ」）。塩対応が続いたので、これだけの返事は引き直す
CURT = re.compile(r"^(うる(さい|せ[えー]?)|うっせ[えー]?|別に|やめろ|知らん|は[?？]?)[。、!！?？wｗ…\s]*$")


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
    if not msgs or any(POLITE.search(m) or SELF_NAME.search(m) for m in msgs) or all(CURT.match(m) for m in msgs):
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


# --- 呼び名を集める（集めるだけ。会話にはまだ使わない） ---------------------------
NAME_PICK = ("Discordのグループチャットの発言を1つ渡す。発言者が【相手】を呼んだり指したりしている呼び名"
             "（名前・あだ名）を、発言の中の表記のまま1つ抜き出す。呼んでいなければ null。"
             '出力はJSONで {"name": "呼び名"} か {"name": null}')


async def pick_name(text, display):
    """LLMが候補を抜き出し、本文に含まれるかをコードが、呼び名かどうかをJevが確かめる。"""
    raw = await _post([{"role": "system", "content": NAME_PICK},
                       {"role": "user", "content": f"【相手】{display}\n【発言】{text}"}], temperature=0)
    try:
        form = names.clean(json.loads(raw or "{}").get("name"))
    except (ValueError, AttributeError):
        return None
    if not names.valid(form, text):
        return None
    p = await jev(f"【相手】{display}（この発言の返信先・メンション先）\n【発言】{text}",
                  f"発言者は【相手】のことを「{form}」という呼び名で呼んだり指したりしているか。")
    return form if p is not None and p >= decide.INTEREST_TRUE else None


# --- 好感度を数える（数えるだけ。態度にはまだ効かせない） ---------------------------
LIKE_QS = {"attack": "発言者は【相手】を本気でけなしている、または喧嘩を売っているか（冗談やノリの軽いいじりは含めない）。",
           "tease": "発言者は【相手】を冗談やノリで軽くいじっているか。",
           "support": "発言者は【相手】を庇っている、褒めている、または気にかけているか。"}
LIKE_ME_Q = "発言者は、この会話の中でナギ（N4Gi）のことを庇っている、褒めている、または気にかけているか。"


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
STATUS_EVERY = 30   # 分。ステータス用チャンネル（config の status_channel_id）に定期的に送る
START_NOTE = ("チャンネルがしばらく静か。誰かへの返事ではなく、自分から話題を1つ振る。"
              "ネタは【最近の出来事】【体の状態】か、自分の趣味・好き嫌いから選ぶ。"
              "軽い報告か質問で、相手が返しやすい形にする。前置きや挨拶はしない")
SCHED_END = {"講義中": "講義が終わった", "バイト中": "バイトが終わった"}
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
        if cmd == "likes":
            await m.reply("-# " + affinity.format_summary(self.s.get("likes", {})).replace("\n", "\n-# "),
                          mention_author=False)
            return
        if cmd == "names":
            await m.reply("-# " + names.format_summary(self.s.get("names", {}), self.user.id)
                          .replace("\n", "\n-# "), mention_author=False)
            return
        if cmd == "ratings":
            await m.reply("-# " + ratings.format_summary(ratings.summary(ratings.load(RATINGS_PATH)))
                          .replace("\n", "\n-# "), mention_author=False)
            return
        if cmd in ("stop", "start"):
            self.s["stopped"] = cmd == "stop"
            save_state(self.s)
        await m.reply("-# " + self.status_line(now()), mention_author=False)

    def status_line(self, t):
        c = self.s.get("counts", {})
        st = self.s.get("starts", {})
        # 今の様子は体の変数込みで出す（予定だけで見ると、寝ているのに「ふつう」と出る）
        return (f"{__version__}: {'停止中' if self.s.get('stopped') else '稼働中'}"
                f"・{decide.activity(t, self.body)['label']}"
                f"・今日 {c.get('total', 0)}/{decide.TOTAL_DAILY_MAX} 回"
                f"・自分から {st.get('n', 0) if st.get('day') == decide.day_key(t) else 0}/{decide.STARTS_PER_DAY}"
                f"・後で返す {len(self.s.get('deferred', []))} 件"
                f"・{body.as_numbers(self.body, t)}")

    async def post_status(self, t):
        """STATUS_EVERY 分ごとに、ステータス用チャンネルへ今の様子と直近の出来事を送る。"""
        cid = CONFIG.get("status_channel_id")
        slot = t.replace(minute=t.minute - t.minute % STATUS_EVERY, second=0, microsecond=0).isoformat()
        if not cid or self.s.get("status_slot") == slot:
            return
        self.s["status_slot"] = slot
        ch = self.get_channel(int(cid))
        if ch is None:
            log.warning("ステータス用チャンネル %s が見えない", cid)
            return
        total = self.s.get("counts", {}).get("total", 0)
        sent = total - self.s.get("status_total", 0)
        self.s["status_total"] = total
        since = t - timedelta(minutes=STATUS_EVERY)
        ev = [f"{datetime.fromisoformat(e['at']):%H:%M} {e['what']}" for e in self.s.get("events", [])
              if datetime.fromisoformat(e["at"]) > since]
        text = (f"**{t:%H:%M}** {self.status_line(t)}\n"
                f"-# 直近{STATUS_EVERY}分: 返事 {sent if sent >= 0 else total} 回"
                + (f"・{'／'.join(ev)}" if ev else ""))
        await ch.send(text)

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

    async def learn_name(self, m):
        """誰かを呼んでいる発言から、その人の呼び名を拾う（人間どうし・ナギへの両方）。"""
        try:
            ref = m.reference.resolved if m.reference else None
            target = (ref.author if isinstance(ref, discord.Message) else
                      next((u for u in m.mentions if u != m.author), None))
            hit = persona.NAMES.search(m.content)
            if hit and target in (None, self.user):
                form = names.clean(hit.group())          # ナギの名前は正規表現で確実に取れる
                target = self.user
            elif target is None or target == m.author or (target.bot and target != self.user):
                return
            else:
                display = getattr(target, "display_name", target.name)
                form = await pick_name(m.clean_content, display)
            if not form:
                return
            display = self.ch.guild.me.display_name if target == self.user else target.display_name
            names.record(self.s.setdefault("names", {}), target.id, display, form, m.author.id, now())
            save_state(self.s)
            log.info("呼び名: %s → %s「%s」", m.author.display_name, display, form)
        except Exception:
            log.exception("呼び名の読み取りに失敗")

    async def learn_like(self, m):
        """誰かに向けた発言から、ナギの発言者への好感度を数える（ナギ宛て・ナギが好きな相手宛ての両方）。"""
        try:
            ref = m.reference.resolved if m.reference else None
            target = (ref.author if isinstance(ref, discord.Message) else
                      next((u for u in m.mentions if u != m.author), None))
            named = bool(persona.NAMES.search(m.content))
            if target is None and named:
                target = self.user
            if target is None or target == m.author or (target.bot and target != self.user):
                return
            t = now()
            store = self.s.setdefault("likes", {})
            me = target == self.user
            if not me and not named:
                rel = affinity.relation(store.get(str(target.id), {}).get("v", affinity.INITIAL))
                if rel is None:
                    return          # ナギが特に好きでない相手どうしのやりとりは見ない（Jevも呼ばない）
            who = "N4Gi（ナギ）" if me else target.display_name
            convo = "\n".join(f"{x['author']}: {x['text']}" for x in list(self.recent)[-6:])
            qs = dict(LIKE_QS)
            if named and not me:
                qs["me"] = LIKE_ME_Q
            p = await jev_many(f"【発言者】{m.author.display_name}\n【相手】{who}\n【直前の会話】\n{convo}", qs)
            if not p:
                return
            author = affinity.get(store, m.author.id, m.author.display_name, t)
            quote = m.clean_content
            kind = affinity.judge(p.get("attack"), p.get("tease"), p.get("support"))
            if me:
                d = affinity.apply(author, affinity.DELTA[(kind, "me")], t, f"ナギに{kind}", quote) if kind else 0
                if not d and kind is None:
                    d = affinity.chat(author, t)
            else:
                rel = affinity.relation(affinity.get(store, target.id, target.display_name, t)["v"])
                d = 0
                if kind and rel:
                    d = affinity.apply(author, affinity.DELTA[(kind, rel)], t, f"{target.display_name}に{kind}", quote)
                if p.get("me", 0) >= affinity.TRUE:
                    d += affinity.apply(author, affinity.DELTA[("support", "me")], t, "ナギを庇った", quote)
            save_state(self.s)
            log.info("好感度: %s→%s %s 判定=%s（%s）→ %+g = %+.0f", m.author.display_name, who, quote[:30], kind,
                     " ".join(f"{k}{v:.2f}" for k, v in p.items()), d, author["v"])
        except Exception:
            log.exception("好感度の読み取りに失敗")

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
                                      "act": act, "at": t}, note)
            except Exception:
                log.exception("後で返す処理に失敗")

    async def pick_up(self, t, act):
        """誰も反応しないまま放置された発言を拾いに行く（1つの発言につき1回だけ抽選）。"""
        if self.s.get("stopped"):
            return
        recent = [{**m, "author": m["author_id"]} for m in self.recent]
        hit = decide.pickup(recent, self.user.id, t, act["state"], self.body["energy"])
        if not hit or self.s.get("picked") == hit["id"]:
            return
        self.s["picked"] = hit["id"]
        target = next(m for m in self.recent if m["id"] == hit["id"])
        if random.random() >= decide.PICKUP_RATE or not decide.budget_ok(self.s.setdefault("counts", {}),
                                                                         target["author_id"], t):
            return
        log.info("放置された発言を拾う: %s", target["text"][:40])
        await self.reply({"target": target, "addressed": True, "kind": "normal", "act": act, "at": t},
                         "この発言には誰も反応していない。友達として拾って、話を広げる（質問を返す・自分の話をする）")

    async def start_topic(self, t, act):
        """チャンネルが静かな時に、自分の生活から話題を出す（1日2回まで）。"""
        if self.s.get("stopped") or not self.recent:
            return
        st = self.s.setdefault("starts", {})
        if st.get("day") != decide.day_key(t):
            st.update({"day": decide.day_key(t), "n": 0})
        humans = [m for m in self.recent if not m["bot"]]
        last_at = datetime.fromisoformat(self.recent[-1]["at"]) if self.recent[-1].get("at") else None
        human_at = datetime.fromisoformat(humans[-1]["at"]) if humans and humans[-1].get("at") else None
        if not decide.start_due(t, last_at, human_at, act["state"], self.body["energy"], st["n"], random.random()):
            return
        lines = [f"［{decide.ago((t - datetime.fromisoformat(e['at'])).total_seconds())}］{e['what']}"
                 for e in self.s.get("events", [])]
        note = START_NOTE + ("\n【最近の出来事】" + "／".join(lines) if lines else "")
        out = await write(list(self.recent), None, "normal", act["label"], note, body_text=body.as_feelings(self.body, t))
        if not out:
            return
        st["n"] += 1
        save_state(self.s)
        log.info("自分から話題を出す → %s", " ｜ ".join(out))
        for text in out:
            async with self.ch.typing():
                await asyncio.sleep(decide.typing_sec(text, act["device"]))
            await self.ch.send(text)
        self.last_sent_at = now()
        body.spend_talk(self.body, sum(map(len, out)), t)

    def note_events(self, t, ev):
        """話題のネタにする最近の出来事（12時間・8件まで）。"""
        keep = [e for e in self.s.get("events", []) if t - datetime.fromisoformat(e["at"]) < timedelta(hours=12)]
        keep += [{"at": t.isoformat(timespec="seconds"), "what": w} for w in ev]
        self.s["events"] = keep[-8:]

    async def live(self, t):
        """体の変数を1分進める。抜ける・戻る時は、会話が動いていれば一言添える。"""
        b = self.body
        sched = decide.schedule(t)
        on, at = decide.alarm(t)
        ev = body.tick(b, t, sched, on, at)
        if self.s.get("last_sched") == "バイト中" and sched != "バイト中":
            ev += body.after_baito(b, t)
        if self.s.get("last_sched") in SCHED_END and sched != self.s["last_sched"]:
            self.note_events(t, [SCHED_END[self.s["last_sched"]]])
        self.s["last_sched"] = sched
        self.note_events(t, [e for e in ev if e not in ("戻った",)])
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
