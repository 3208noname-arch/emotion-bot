# -*- coding: utf-8 -*-
"""発言した時の内部の値の記録（純関数）。評価と突き合わせて「どの値の時に変だと言われたか」を調べるため。

項目名は人格に依存させない（別の人格の Bot でも同じ名前で使う）:
- activity: 今の様子（寝ている・講義中・ふつう など）
- body: 体の変数（眠気・空腹・気力・残高）と、LLM に渡した体の感じ
- relation: 返信先の人への好感度（無ければ None）
- mood: 気分（居心地。未実装の間は None）
- version: 喋った時に動いていた版
- decision: なぜ喋ったか（source: reply／late／pickup／start／announce、型、話しかけられたか、食いつく話題か）
"""

SOURCES = {"reply": "返事", "late": "遅れて返事", "pickup": "放置を拾う", "start": "自分から話題", "announce": "抜ける・戻る一言"}


def build(t, activity, body, feelings, relation=None, mood=None, decision=None, version=None):
    return {"at": t.isoformat(timespec="seconds"), "version": version, "activity": activity,
            "body": {k: round(body[k]) for k in ("sleepy", "hunger", "energy")} | {"money": body["money"]},
            "feelings": feelings, "relation": None if relation is None else round(relation, 1),
            "mood": mood, "decision": decision or {}}


def describe(snap):
    """評価の一覧で読む1行。"""
    if not snap:
        return ""
    d, b = snap.get("decision", {}), snap.get("body", {})
    parts = [SOURCES.get(d.get("source"), d.get("source", "?")), snap.get("activity", "?"),
             f"眠気{b.get('sleepy')} 空腹{b.get('hunger')} 気力{b.get('energy')}"]
    if snap.get("relation") is not None:
        parts.append(f"相手への好感度{snap['relation']:+g}")
    if snap.get("mood") is not None:
        parts.append(f"気分{snap['mood']}")
    if d.get("hot"):
        parts.append("食いつく話題")
    return "・".join(parts)


def find(recs, message_id):
    """記録の中から、その発言の時の値を探す（無ければ None）。"""
    for r in reversed(recs):
        if message_id in r.get("message_ids", []):
            return r
    return None
