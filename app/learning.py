"""呼び名と好感度を数える（数えるだけ。会話と態度にはまだ使わない）。"""
import json
import logging

import discord

import persona
from app.jev import jev, jev_many
from app.llm import post
from app.store import now, save_state
from engine import affinity, decide, names

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
           "support": "発言者は【相手】を庇っている、褒めている、または気にかけているか。"}
LIKE_ME_Q = "発言者は、この会話の中でナギ（N4Gi）のことを庇っている、褒めている、または気にかけているか。"


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
