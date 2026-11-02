"""教わったことを覚え書きにする・思い出す。規則は engine/lessons.py。置き場は lessons.json（Web UI が消す）。"""
import json
import logging
import os

import discord

from app.jev import jev
from app.llm import post
from app.store import ROOT, now
from engine import lessons

log = logging.getLogger("minato")

LESSONS_PATH = os.path.join(ROOT, "lessons.json")
TEACH_TRUE = 0.70
# 評価コメントは短く（「ｶﾞｯ だろ。」）、Jev は 0.55〜0.59 と読んだ。ここは緩め、振る舞いへの指摘は書き起こし（EXTRACT）で弾く
RATING_TEACH_TRUE = 0.50
# 評価コメントを全部通したら、8件中7件が「ナギは所持金を過小評価しがち」「嘘をつくと矛盾を突かれるので注意」のような
# ナギの振る舞いへの反省メモになった（10-27）。覚えるのは世の中の事実・常識・お約束だけにする
EXTRACT = ("Discordの会話で、相手がナギ（N4Gi、私大2年の男子）に教えた、世の中の事実・常識・ネットのお約束（ネタの返し方など）を、"
           "ナギが覚えておく1行の覚え書き（40字以内）にする。後で同じ話題になった時に使える形で書く"
           "（例: 「ﾇﾙﾎﾟと言われたらｶﾞｯと返すのがネットのお約束」「コンビニのレジ袋は1枚3円くらい」）。"
           "思い出すための短い言葉（会話に出そうな語）を2〜5個付ける。"
           "ナギ自身の振る舞い・言い方・文脈の読み違いへの指摘や反省（「〜に注意する」「ナギは〜しがち」）は覚え書きにしない。"
           "教えていることが無い、ただの感想・悪口・流れの指摘なら null。"
           '出力はJSONで {"fact": "覚え書き", "keywords": ["語", ...]} か {"fact": null}')
RATING_TEACH_Q = ("【指摘】は、世の中の事実・常識・ネットのお約束（ネタの返し方など）を教えて、【ナギの発言】の誤りを正しているか。"
                  "ナギの振る舞い・言い方・文脈の読み違い・時刻や所持金の勘違いへの指摘、"
                  "「流れがおかしい」「意味が分からない」のような感想は当てはまらない。")


def load():
    try:
        return json.load(open(LESSONS_PATH, encoding="utf-8")).get("items", [])
    except (IOError, ValueError):
        return []


def save(items):
    tmp = LESSONS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LESSONS_PATH)


class LessonsMixin:
    def lesson_lines(self, text):
        return lessons.as_lines(lessons.relevant(load(), text))

    async def learn_lesson(self, context, by, source):
        """教わったことを LLM に1行にさせ、覚え書きに足す。"""
        raw = await post([{"role": "system", "content": EXTRACT}, {"role": "user", "content": context}], temperature=0)
        try:
            d = json.loads(raw or "{}")
        except ValueError:
            return
        if not isinstance(d, dict) or not d.get("fact"):
            return
        items = load()
        done = lessons.add(items, d["fact"], d.get("keywords"), by, now().isoformat(timespec="seconds"), source)
        if done:
            save(items)
            self.used("lessons")
            log.info("教わった（%s・%s）: %s %s", by, done, d["fact"], d.get("keywords"))

    async def context_before(self, message_id, n=6):
        """評価に会話が残っていない時（発言から時間が経っている）、Discord から直前の会話を取る。
        「ぬる」への「ｶﾞｯ だろ」は、前の「ぬるぽ」が見えないと読めなかった（10-27）。"""
        try:
            msgs = [m async for m in self.ch.history(limit=n, before=discord.Object(id=message_id))]
        except discord.HTTPException:
            return ""
        return "\n".join(f"{m.author.display_name}: {m.clean_content}" for m in reversed(msgs))

    async def learn_from_rating(self, rec, rater):
        """評価のコメントが、ナギの発言の事実・お約束の誤りを正していたら覚える。"""
        if not rec.get("is_me") or not rec.get("comment"):
            return
        ctx = "\n".join(rec.get("context", [])[-4:]) or await self.context_before(rec["message_id"])
        state = f"【会話】\n{ctx}\n【ナギの発言】{rec['text']}\n【指摘】{rec['comment']}"
        p = await jev(state, RATING_TEACH_Q)
        log.info("評価のコメントは事実の訂正か: %s（%s）", None if p is None else round(p, 2), rec["comment"][:40])
        if p is not None and p >= RATING_TEACH_TRUE:
            await self.learn_lesson(state, f"{rater}（評価）", "評価")
