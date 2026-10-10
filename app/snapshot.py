"""発言した時の内部の値を残す（評価と突き合わせるため）。項目は engine/snapshot.py。"""
import logging

from app import __version__
from app.store import SNAPSHOTS_PATH
from engine import body, ratings, snapshot

log = logging.getLogger("minato")


class SnapshotMixin:
    def remember(self, sent, t, act, decision, target_id=None):
        """送ったメッセージ（discord.Message のリスト）と、その時の値を1行で残す。"""
        ids = [m.id for m in sent if m is not None]
        if not ids:
            return
        rel = self.s.get("likes", {}).get(str(target_id), {}).get("v") if target_id else None
        rec = snapshot.build(t, act["state"], self.body, body.as_feelings(self.body, t), relation=rel,
                             mood=None, decision=decision, version=__version__)
        ratings.append(SNAPSHOTS_PATH, {**rec, "message_ids": ids})

    @staticmethod
    def recall(message_id):
        return snapshot.find(ratings.load(SNAPSHOTS_PATH)[-2000:], message_id)
