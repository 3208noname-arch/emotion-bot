"""居心地（気分）を進める・動かす・言葉にする。規則は engine/mood.py。"""
import json
import logging
import os
from datetime import datetime, timedelta

from app.jev import jev_many
from app.store import ROOT, now
from engine import body, mood, topics

log = logging.getLogger("minato")

TOPICS_PATH = os.path.join(ROOT, "topics.json")   # Web UI の「話題の好き嫌い」が書く
TOPIC_AGAIN = timedelta(minutes=30)               # 同じ話題で続けて動かさない（話題が続くたびに積み上がるため）


def load_topics_file():
    try:
        return json.load(open(TOPICS_PATH, encoding="utf-8"))
    except (IOError, ValueError):
        return {"topics": [], "updated": None}


def load_topics():
    return load_topics_file().get("topics", [])


class MoodMixin:
    @property
    def mood(self):
        return self.s.setdefault("mood", mood.new(now()))

    def mood_base(self, t):
        b = self.body
        return mood.baseline(body.felt_sleepy(b, t), b["hunger"], b["energy"], b["money"] < body.BROKE)

    def mood_settle(self, t):
        base = self.mood_base(t)
        mood.settle(self.mood, t, base)
        return base

    def mood_words(self, t):
        return mood.as_words(self.mood, self.mood_settle(t))

    def mood_hit(self, delta, why):
        if not delta:
            return
        t = now()
        self.mood_settle(t)
        d = mood.nudge(self.mood, delta, t, why)
        if d:
            log.info("気分: %s %+.1f → %.0f（%s）", why, d, self.mood["v"], mood.band(self.mood["v"]))

    def taste_words(self):
        return topics.as_words(load_topics())

    def taste(self, name, like):
        """返事の中で口にした好き嫌いを、話題の図に覚える（人が置いた話題は書き換えない）。"""
        obj = load_topics_file()
        done = topics.learn(obj["topics"], name, like, now().isoformat(timespec="seconds"))
        if done in ("added", "moved"):
            tmp = TOPICS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.replace(tmp, TOPICS_PATH)
        log.info("好き嫌いを口にした: %s %+.1f → %s", name, like, done)

    async def read_topic(self, convo):
        """会話の最後の発言がどの話題かをJevで読み、好き嫌い（快）で気分を動かす。"""
        tps = load_topics()
        if not tps:
            return
        qs = {tp["name"]: f"会話の最後の発言は「{tp['name']}」についての話か。" for tp in tps}
        hit = mood.topic_hit(await jev_many(convo, qs), tps)
        if not hit:
            return
        name, d = hit
        why = f"{name}の話になった"
        t = now()
        if any(e["why"] == why and t - datetime.fromisoformat(e["at"]) < TOPIC_AGAIN for e in self.mood["events"]):
            return
        self.mood_hit(d, why)
