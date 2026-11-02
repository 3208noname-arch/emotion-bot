# -*- coding: utf-8 -*-
"""教わったこと（純関数）。会話で教わった知識・お約束・訂正と、評価コメントの事実の訂正を覚え書きにする
（10-27 ユーザー「我々が教えたことを参照して覚えてるといい」。ﾇﾙﾎﾟにｶﾞｯ、レジ袋は1枚3円、など）。

- 覚え書きは1行の事実と、思い出すための言葉（keywords）。誰に・いつ・どこで（会話／評価）教わったかも残す
- **消さない**（覚えた話題と同じ）。使うのは、今の会話に言葉が出たものと、最近教わった RECENT_N 件だけ
- 同じ事実をもう一度教わったら、新しく足さずに回数と時刻を更新する
- 間違って覚えたものは Web UI から消す（lessons.json を書き換える）
"""
RECENT_N = 5
MAX_SHOW = 8


def norm(s):
    return "".join(str(s or "").split()).casefold()


def add(items, fact, keywords, by, at, source):
    """覚え書きを足す。同じ事実が既にあれば回数と時刻を更新する。"added"／"again"／None を返す。"""
    fact = str(fact or "").strip()[:120]
    kws = [k for k in (str(x).strip()[:20] for x in (keywords or [])) if k][:6]
    if not fact:
        return None
    same = next((x for x in items if norm(x["fact"]) == norm(fact)), None)
    if same:
        same["n"] = same.get("n", 1) + 1
        same["at"] = at
        same["keywords"] = sorted(set(same.get("keywords", [])) | set(kws))
        return "again"
    nid = max((x.get("id", 0) for x in items), default=0) + 1
    items.append({"id": nid, "fact": fact, "keywords": kws, "by": by, "at": at, "source": source, "n": 1})
    return "added"


def relevant(items, text):
    """今使う覚え書き。会話に言葉が出たもの（新しい順）＋最近教わった RECENT_N 件。最大 MAX_SHOW 件。"""
    t = norm(text)
    hit = [x for x in items if any(norm(k) and norm(k) in t for k in x.get("keywords", []))]
    recent = sorted(items, key=lambda x: x["at"], reverse=True)[:RECENT_N]
    out = []
    for x in sorted(hit, key=lambda x: x["at"], reverse=True) + recent:
        if x not in out:
            out.append(x)
    return out[:MAX_SHOW]


def as_lines(items):
    return [f"{x['fact']}（{x['by']}に教わった）" for x in items]
