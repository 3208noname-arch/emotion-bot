# -*- coding: utf-8 -*-
"""話題の好き嫌い（快・覚醒の座標）を、ナギが会話で口にした好き嫌いから育てる（純関数）。

- 新しい話題は「自分で覚えた」印（source: learned）を付けて足す。快は言った向き、覚醒は 0.5 から
- 既にある覚えた話題は、言うたびに快を少し寄せる（1回で全部は動かさない）
- **人が置いた話題（source が無い）は書き換えない**
- 覚えた話題は LEARNED_MAX 件まで。超えたら一番古く言ったものから消す（話題が増えるほどJevへの問いが増えるため）
"""

LEARNED_MAX = 20
PULL = 0.3            # 既にある覚えた話題を、言った向きへ寄せる割合
NEW_AROUSAL = 0.5
SHOW_FROM = 0.2       # 快がこれ以上離れている話題だけ、LLMに「好き・嫌い」として渡す


def clean(name):
    name = str(name or "").strip().strip("「」『』\"'")
    return name if 1 <= len(name) <= 20 else None


def learn(topics, name, like, at):
    """topics（topics.json の並び）を直して、何をしたか（"added"／"moved"／"kept"／None）を返す。"""
    name = clean(name)
    try:
        like = max(-1.0, min(1.0, float(like)))
    except (TypeError, ValueError):
        return None
    if not name:
        return None
    same = next((t for t in topics if t["name"].casefold() == name.casefold()), None)
    if same is not None:
        if same.get("source") != "learned":
            return "kept"
        same["valence"] = round(same["valence"] + (like - same["valence"]) * PULL, 2)
        same["at"] = at
        return "moved"
    topics.append({"name": name, "valence": round(like, 2), "arousal": NEW_AROUSAL,
                   "note": "会話で言った", "source": "learned", "at": at})
    learned = sorted((t for t in topics if t.get("source") == "learned"), key=lambda t: t["at"])
    for old in learned[:max(0, len(learned) - LEARNED_MAX)]:
        topics.remove(old)
    return "added"


def as_words(topics):
    """LLMに渡す自分の好き嫌い（言ったことと食い違わないように）。"""
    likes = [t["name"] for t in sorted(topics, key=lambda t: -t["valence"]) if t["valence"] >= SHOW_FROM]
    hates = [t["name"] for t in sorted(topics, key=lambda t: t["valence"]) if t["valence"] <= -SHOW_FROM]
    return f"好き: {'、'.join(likes) or '特に無し'}／嫌い: {'、'.join(hates) or '特に無し'}"
