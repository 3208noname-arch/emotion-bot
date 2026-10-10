# -*- coding: utf-8 -*-
"""評価の記録と集計。評価は右クリックメニュー「評価」から、本人にしか見えない形で付けてもらう。

- 1行1件の JSON Lines。**同じ人が同じ発言を評価し直したら、新しい方を採る**
- 評価の無い発言は「自然」ではなく「不明」。だから集計は「評価された発言のうちの割合」で、
  湊と人間の発言を比べる（人間の発言の割合が基準になる）
"""
import json
import os

# 「感情がズレてる」は、反応の強さや向き（怒る・喜ぶ・照れる…）が場面に合わない時（2026-10-10 追加）
LABELS = ["自然", "流れとズレてる", "キャラじゃない", "感情がズレてる", "バグ"]
# 2段目で中身を選ばせるもの。コメント欄だと書かれないことが多く（22件中8件が空）、何を直せばよいか決まらないため
DETAILS = {"感情がズレてる": ["強すぎ", "弱すぎ", "向きが違う"]}


def label_text(r):
    """「感情がズレてる（強すぎ）」のように、2段目まで含めた名前。"""
    return f"{r['label']}（{r['detail']}）" if r.get("detail") else r["label"]


def append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def version_at(timeline, t):
    """その時刻に動いていた版。timeline は [(起動した時刻, 版), ...]（古い順）。分からなければ None。"""
    v = None
    for at, ver in timeline:
        if at > t:
            break
        v = ver
    return v


def latest(recs):
    """(評価者, 発言) ごとに最後の1件だけ残す。"""
    d = {}
    for r in recs:
        d[(r["rater_id"], r["message_id"])] = r
    return list(d.values())


def summary(recs):
    """{"bot": {ラベル: 件数}, "human": {...}, "details": {"bot": {ラベル: {中身: 件数}}, "human": {...}}}"""
    out = {"bot": {k: 0 for k in LABELS}, "human": {k: 0 for k in LABELS},
           "details": {"bot": {}, "human": {}}}
    for r in latest(recs):
        if r["label"] in LABELS:
            who = "bot" if r["is_me"] else "human"
            out[who][r["label"]] += 1
            if r.get("detail"):
                d = out["details"][who].setdefault(r["label"], {})
                d[r["detail"]] = d.get(r["detail"], 0) + 1
    return out


def format_summary(s):
    lines = []
    for who, name in (("bot", "ナギ"), ("human", "人間")):
        n = sum(s[who].values())
        if not n:
            lines.append(f"{name}: まだ評価なし")
            continue
        det = s.get("details", {}).get(who, {})
        sub = {k: " " + "・".join(f"{dk}{dv}" for dk, dv in det[k].items()) if k in det else "" for k in s[who]}
        parts = "・".join(f"{k} {v}（{v * 100 // n}%{sub[k]}）" for k, v in s[who].items() if v)
        lines.append(f"{name}: {n}件 — {parts}")
    return "\n".join(lines)
