"""発言した時の内部の値を残す（評価と突き合わせるため）。項目は engine/snapshot.py。"""
import logging
from datetime import datetime, timedelta

from app import __version__
from app.store import SNAPSHOTS_PATH
from engine import body, decide, ratings, snapshot

log = logging.getLogger("minato")


MINE_KEEP = timedelta(hours=6)   # 自分の発言は、会話ログとは別にこれだけ覚えておく（会話が流れても消えない）
MINE_N = 15


class SnapshotMixin:
    def my_lines(self, t):
        """LLMに渡す「あなたが最近言ったこと」（古い順・経過はざっくり）。"""
        return [f"［{decide.rough_ago((t - datetime.fromisoformat(x['at'])).total_seconds())}］{x['text']}"
                for x in self.s.get("my_lines", []) if t - datetime.fromisoformat(x["at"]) <= MINE_KEEP]

    def remember(self, sent, t, act, decision, target_id=None):
        """送ったメッセージ（discord.Message のリスト）と、その時の値を1行で残す。"""
        ids = [m.id for m in sent if m is not None]
        if not ids:
            return
        mine = [x for x in self.s.get("my_lines", []) if t - datetime.fromisoformat(x["at"]) <= MINE_KEEP]
        mine += [{"at": t.isoformat(timespec="seconds"), "text": m.content[:200]} for m in sent if m is not None]
        self.s["my_lines"] = mine[-MINE_N:]
        self.mark_spoke()
        self.used("today")      # LLMに日付と予定を渡して書いた発言
        self.used("account")    # 送る前の立場チェックを通った発言
        self.used("mood")       # 同じく気分を渡して書いた発言
        rel = self.s.get("likes", {}).get(str(target_id), {}).get("v") if target_id else None
        self.mood_settle(t)
        rec = snapshot.build(t, act["state"], self.body, body.as_feelings(self.body, t), relation=rel,
                             mood=round(self.mood["v"]), decision=decision, version=__version__)
        ratings.append(SNAPSHOTS_PATH, {**rec, "message_ids": ids})

    @staticmethod
    def recall(message_id):
        return snapshot.find(ratings.load(SNAPSHOTS_PATH)[-2000:], message_id)
