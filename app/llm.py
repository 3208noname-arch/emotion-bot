"""LLM（OpenRouter の Qwen）: 文面を書くだけ。型と口調はコードが検める。"""
import json
import logging
import re
from datetime import datetime

import aiohttp

import persona
from app.store import CONFIG, now
from engine import decide

log = logging.getLogger("minato")

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODELS = ["qwen/qwen3.8-omni-flash", "qwen/qwen3.7-flash"]
TRIES = 3
# ため口の人格。敬語が出たら引き直す
POLITE = re.compile(r"(です|ます|ました|ません|でした|ください|でしょう)[ねよかがけど]?(?=[。！？!?…、~〜\s]|$)")


async def post(messages, temperature=1.0):
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
        text = await post(messages)
        if not text:
            return None
        out = parse(text, kind, target_text)
        if out:
            return out
        log.info("型・口調が合わない。引き直す: %r", text[:80])
    return None   # 全滅なら黙る（定型で埋めない。黙っても不自然ではない）
