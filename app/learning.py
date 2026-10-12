"""呼び名と好感度を数える（数えるだけ。会話と態度にはまだ使わない）。"""
import json
import logging
from datetime import datetime

import discord

import persona
from app.jev import jev, jev_many
from app.llm import post
from app.store import now, save_state
from engine import affinity, decide, mood, names

log = logging.getLogger("minato")

# --- 呼び名を集める（集めるだけ。会話にはまだ使わない） ---------------------------
NAME_PICK = ("Discordのグループチャットの発言を1つ渡す。発言者が【相手】を呼んだり指したりしている呼び名"
             "（名前・あだ名）を、発言の中の表記のまま1つ抜き出す。呼んでいなければ null。"
             '出力はJSONで {"name": "呼び名"} か {"name": null}')


async def pick_name(text, display):
    """LLMが候補を抜き出し、本文に含まれるかをコードが、呼び名かどうかをJevが確かめる。"""
    raw = await post([{"role": "system", "content": NAME_PICK},
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
           "support": "発言者は【相手】を本心から庇っている、褒めている、または気にかけているか"
                      "（冗談の前振り・お世辞・皮肉は含めない）。",
           "joke": "発言者は【相手】に、冗談・ボケ・ノリで言っているか"
                   "（わざと怪しい話を持ちかける、罠にかける前振りも含む。その後の会話で冗談だと分かるものも含む）。",
           "belittle": "発言者は【相手】を見下したり、子ども扱い・バカ扱いしたりしているか"
                       "（ふざけた調子や「かわいい」を添えた言い方でも含む）。"}
LIKE_ME_Q = "発言者は、この会話の中でナギ（N4Gi）のことを本心から庇っている、褒めている、または気にかけているか。"
FLOW_GAP = 300   # 返信もメンションも無い発言でも、ナギの発言からこの秒数以内に続いたものはナギ宛てとみなす


class LearningMixin:
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

    def learn_like(self, m):
        """誰かに向けた発言を、好感度の判定待ちに積む（ナギ宛て・ナギが好きな相手宛ての両方）。
        **その場では読まない。**後に続く会話まで見てから flush_likes で読む（冗談かどうかは後の流れで分かる）。"""
        ref = m.reference.resolved if m.reference else None
        target = (ref.author if isinstance(ref, discord.Message) else
                  next((u for u in m.mentions if u != m.author), None))
        named = bool(persona.NAMES.search(m.content))
        recent = list(self.recent)
        prev = recent[-2] if len(recent) >= 2 else None
        if target is None and (named or (prev and prev["author_id"] == self.user.id and prev.get("at") and
                                         (now() - datetime.fromisoformat(prev["at"])).total_seconds() < FLOW_GAP)):
            target = self.user          # 名前を呼んだ、またはナギの発言に続けて言った
        if target is None or target == m.author or (target.bot and target != self.user):
            return
        me = target == self.user
        if not me and not named:
            v = self.s.get("likes", {}).get(str(target.id), {}).get("v", affinity.INITIAL)
            if affinity.relation(v) is None:
                return          # ナギが特に好きでない相手どうしのやりとりは見ない（Jevも呼ばない）
        self.like_queue.append({"m": m, "target": target, "me": me, "named": named, "at": now(),
                                "before": recent[-7:-1]})

    async def flush_likes(self):
        """判定待ちのうち、後ろに AFTER_N 件続いたか AFTER_WAIT 経ったものを読む。"""
        t = now()
        ids = [x["id"] for x in self.recent]
        ready, wait = [], []
        for q in self.like_queue:
            after = len(ids) - ids.index(q["m"].id) - 1 if q["m"].id in ids else affinity.AFTER_N
            (ready if affinity.ripe(after, t - q["at"]) else wait).append(q)
        self.like_queue = wait
        for q in ready:
            await self.judge_like(q)

    async def judge_like(self, q):
        try:
            m, target, me, named = q["m"], q["target"], q["me"], q["named"]
            t = now()
            store = self.s.setdefault("likes", {})
            recent = list(self.recent)
            ids = [x["id"] for x in recent]
            after = recent[ids.index(m.id) + 1:][:affinity.AFTER_N] if m.id in ids else []
            fmt = lambda xs: "\n".join(f"{x['author']}: {x['text']}" for x in xs) or "（無し）"
            who = "N4Gi（ナギ）" if me else target.display_name
            qs = dict(LIKE_QS)
            if named and not me:
                qs["me"] = LIKE_ME_Q
            p = await jev_many(f"【発言者】{m.author.display_name}\n【相手】{who}\n【直前の会話】\n{fmt(q['before'])}\n"
                               f"【判定する発言】\n{m.author.display_name}: {m.clean_content}\n"
                               f"【その後の会話】\n{fmt(after)}", qs)
            if not p:
                return
            author = affinity.get(store, m.author.id, m.author.display_name, t)
            quote = m.clean_content
            kind = affinity.judge(p.get("attack"), p.get("tease"), p.get("support"), p.get("joke"), p.get("belittle"))
            name = m.author.display_name
            if me:
                d = affinity.apply(author, affinity.DELTA[(kind, "me")], t, f"ナギに{kind}", quote) if kind else 0
                if kind:
                    self.mood_hit(mood.people_delta(kind, "me"), mood.people_reason(kind, name))
                else:
                    self.mood_hit(mood.chat_delta(author["v"]), f"{name}に話しかけられた")
                if not d and kind is None:
                    d = affinity.chat(author, t)
            else:
                rel = affinity.relation(affinity.get(store, target.id, target.display_name, t)["v"])
                d = 0
                if (kind, rel) in affinity.DELTA:
                    d = affinity.apply(author, affinity.DELTA[(kind, rel)], t, f"{target.display_name}に{kind}", quote)
                    self.mood_hit(mood.people_delta(kind, rel), mood.people_reason(kind, target.display_name, by=name))
                if p.get("me", 0) >= affinity.TRUE:
                    d += affinity.apply(author, affinity.DELTA[("support", "me")], t, "ナギを庇った", quote)
                    self.mood_hit(mood.people_delta("support", "me"), mood.people_reason("support", name))
            save_state(self.s)
            log.info("好感度: %s→%s %s 判定=%s（%s・後ろ%d件）→ %+g = %+.0f", name, who, quote[:30], kind,
                     " ".join(f"{k}{v:.2f}" for k, v in p.items()), len(after), d, author["v"])
        except Exception:
            log.exception("好感度の読み取りに失敗")
