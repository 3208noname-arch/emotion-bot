"""暮らし: 体の変数を進める、抜ける・戻る一言、放置された発言を拾う、自分から話題を出す。"""
import asyncio
import logging
import random
from datetime import datetime, timedelta

import discord

from app.llm import write
from app.store import JST, now, save_state
from engine import body, decide

log = logging.getLogger("minato")

ANNOUNCE = {"寝た": "眠気が限界なので、今から寝る。会話から抜ける一言だけ",
            "落ちる": "人と話すのがしんどくなったので、少し会話から抜ける。重くならない一言だけ",
            "コンビニに行く": "腹が減ったので、今からコンビニに行く。一言だけ",
            "戻った": "コンビニから戻った／少し休んで戻った。一言だけ"}
START_NOTE = ("チャンネルがしばらく静か。誰かへの返事ではなく、自分から話題を1つ振る。"
              "ネタは【最近の出来事】【体の状態】か、自分の趣味・好き嫌いから選ぶ。"
              "軽い報告か質問で、相手が返しやすい形にする。前置きや挨拶はしない")
SCHED_END = {"講義中": "講義が終わった", "バイト中": "バイトが終わった"}
ACTIVE_WINDOW = timedelta(minutes=20)   # この間に人が話していれば、抜ける・戻る時に一言添える
ANNOUNCE_GAP = timedelta(minutes=3)     # 自分がこの間に喋っていたら、抜ける・戻る一言は省く（直前の返事をなぞるため）


class LifeMixin:
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
