"""人ごとの怒りを持つ・冷ます・言葉にする。規則は engine/anger.py。"""
import logging

from app.store import now
from engine import anger

log = logging.getLogger("minato")


class AngerMixin:
    @property
    def anger_store(self):
        return self.s.setdefault("anger", {})

    def is_close(self, uid):
        return self.s.get("likes", {}).get(str(uid), {}).get("v", 0) >= anger.CLOSE_FROM

    def anger_of(self, uid):
        return anger.level(self.anger_store, uid, now(), self.is_close(uid)) if uid else 0.0

    def anger_words(self, uid):
        v = self.anger_of(uid)
        return anger.as_words(self.anger_store[str(uid)], v) if v else None

    def angered(self, uid, display, kind, quote):
        add = anger.hit(self.anger_store, uid, display, kind, quote, now(), self.is_close(uid))
        if add:
            self.used("anger")
            log.info("怒り: %s %+d → %.0f（「%s」）", display, add, self.anger_store[str(uid)]["v"], quote[:30])

    def apologized(self, uid, display, p_apology, p_light, said_at):
        cut = anger.apologize(self.anger_store, uid, p_apology, p_light, now(), self.is_close(uid))
        if cut:
            self.used("apology")
            left = self.anger_store.get(str(uid), {}).get("v", 0)
            log.info("謝られた: %s（軽さ%.2f）→ 怒り−%.0f = %.0f", display, p_light or 0, cut, left)
            self.mood_hit(cut / 8, f"{display}に謝られた", said_at=said_at)
