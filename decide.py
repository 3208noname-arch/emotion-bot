# -*- coding: utf-8 -*-
"""返すかどうか・どの型で・いつ返すかを決める規則（純関数）。**LLMには決めさせない。**

型: none（無反応）／reaction（絵文字だけ）／short（一言）／normal（通常）／defer（今は返せない。後で返す）
"""
from datetime import datetime, time, timedelta

USER_DAILY_MAX = 30     # 1人あたり、1日にナギが返す回数の上限
TOTAL_DAILY_MAX = 150   # 全体の上限。超えたら黙る
INTEREST_TRUE = 0.70    # Jev をこれ以上で真と読む。未満と失敗（None）は棄権
PENDING_TTL = timedelta(minutes=3)   # 書き始めてからこれ以上かかったら捨てる
DEFER_TTL = timedelta(hours=12)      # 後で返す呼びかけの賞味期限

# --- 1週間の予定（会話と無関係に先に決まる） ----------------------------------
# 私大文系2年。月水の1限は再履修の語学。曜日は 月=0
TIMETABLE = {
    0: [(time(9, 0), time(10, 40), "語学（再履修）"), (time(13, 0), time(14, 40), "講義")],
    1: [(time(10, 50), time(12, 30), "講義")],
    2: [(time(9, 0), time(10, 40), "語学（再履修）"), (time(14, 50), time(16, 30), "ゼミ")],
    3: [(time(13, 0), time(14, 40), "講義")],
    4: [(time(10, 50), time(12, 30), "講義")],
}
BAITO_DAYS = (1, 3, 4, 5)            # 火木金土
BAITO = (time(18, 0), time(23, 59))  # ラスト24時まで
SLEEP_AT = 3                         # 3時に寝る
WAKE_1GEN = time(8, 0)               # 1限がある日
WAKE = time(10, 30)                  # それ以外
GROGGY = timedelta(minutes=90)       # 起きてからしばらくは眠い


def _at(t, hm):
    return datetime.combine(t.date(), hm)


def wake_time(t):
    first = TIMETABLE.get(t.weekday(), [])
    return WAKE_1GEN if first and first[0][0] < time(10, 0) else WAKE


def schedule(t):
    """予定だけで決まる状態（講義中／バイト中／その他）。体の変数の進め方に使う。"""
    for start, end, _ in TIMETABLE.get(t.weekday(), []):
        if _at(t, start) <= t < _at(t, end):
            return "講義中"
    if t.weekday() in BAITO_DAYS and _at(t, BAITO[0]) <= t <= _at(t, BAITO[1]):
        return "バイト中"
    return "その他"


def alarm(t):
    """1限がある日だけ目覚ましをかける。(かけているか, 時刻)"""
    first = TIMETABLE.get(t.weekday(), [])
    on = bool(first and first[0][0] < time(10, 0))
    return on, _at(t, WAKE_1GEN)


AWAY_LABEL = {"コンビニ": "コンビニに行っている", "一人になりたい": "ちょっと落ちている（一人になりたい）"}


def activity(t, body=None):
    """今していること。{"state", "label", "device", "until"}。

    - state: 寝ている／離席中／講義中／バイト中／起きたて／深夜／ふつう
    - device: スマホ／PC。**切り替わる時点は定義できないので、状態ごとに決め打ち**（家の夜だけPC）
    - until: 忙しさが終わる時刻（分かる時だけ）
    - **body があれば、寝ている・離席・起きたては体の変数で決まる**（時刻では決めない）。
      無ければ従来どおり時刻で決める（テストと、体の変数が無い時の予備）
    """
    if body is not None:
        if body.get("asleep"):
            return {"state": "寝ている", "label": "寝ている", "device": "スマホ", "until": None}
        if body.get("away_until") and t < datetime.fromisoformat(body["away_until"]):
            return {"state": "離席中", "label": AWAY_LABEL.get(body.get("away_why"), "離席中"),
                    "device": "スマホ", "until": datetime.fromisoformat(body["away_until"])}
        woke = body.get("woke_at")
        groggy = bool(woke and t - datetime.fromisoformat(woke) < GROGGY)
    else:
        wake = _at(t, wake_time(t))
        if SLEEP_AT <= t.hour and t < wake:
            return {"state": "寝ている", "label": "寝ている", "device": "スマホ", "until": wake}
        groggy = wake <= t < wake + GROGGY
    for start, end, name in TIMETABLE.get(t.weekday(), []):
        if _at(t, start) <= t < _at(t, end):
            return {"state": "講義中", "label": f"{name}を受けている（スマホでこっそり見ている）",
                    "device": "スマホ", "until": _at(t, end)}
    if t.weekday() in BAITO_DAYS and _at(t, BAITO[0]) <= t <= _at(t, BAITO[1]):
        return {"state": "バイト中", "label": "居酒屋のバイト中", "device": "スマホ",
                "until": _at(t, BAITO[1]) + timedelta(minutes=1)}
    if groggy:
        return {"state": "起きたて", "label": "さっき起きたところ（眠いが、もう起きている）", "device": "スマホ", "until": None}
    if t.hour >= 23 or t.hour < 6:
        return {"state": "深夜", "label": "深夜（家でラジオを聞きながら）", "device": "PC", "until": None}
    return {"state": "ふつう", "label": "ふつう", "device": "PC" if t.hour >= 19 else "スマホ", "until": None}


def presence(t):
    return activity(t)["label"]


def day_key(t):
    """1日の区切りは朝4時（深夜の会話を前日に数える）。"""
    return (t - timedelta(hours=4)).date().isoformat()


def budget_ok(counts, user_id, t):
    """上限の内側か。counts = {"day": "...", "total": n, "users": {id: n}}"""
    if counts.get("day") != day_key(t):
        return True
    if counts.get("total", 0) >= TOTAL_DAILY_MAX:
        return False
    return counts.get("users", {}).get(str(user_id), 0) < USER_DAILY_MAX


def count(counts, user_id, t):
    """返した1回を数える（counts を書き換える）。"""
    if counts.get("day") != day_key(t):
        counts.clear()
        counts.update({"day": day_key(t), "total": 0, "users": {}})
    counts["total"] += 1
    u = counts["users"]
    u[str(user_id)] = u.get(str(user_id), 0) + 1


def my_share(recent, me):
    """直近の発言のうち、ナギの割合と、人間の参加人数。"""
    if not recent:
        return 0.0, 0
    mine = sum(1 for m in recent if m["author"] == me)
    humans = {m["author"] for m in recent if m["author"] != me and not m.get("bot")}
    return mine / len(recent), len(humans)


def choose(addressed, interest, share, humans, state, r, energy=70):
    """返答の型を決める。r は 0〜1 の乱数（テストで固定する）。

    - 寝ている・バイト中: 返せない。話しかけられたものだけ defer（終わってから返す）
    - 講義中: 話しかけられたら一言だけ。それ以外はほぼ黙る
    - 話しかけられていなければ、**発言シェアが「参加人数分の1」を超えたら黙る**
    - Jev の「食いつく話題か」が真なら返す確率を上げる。棄権（None）は「食いつかない」扱い
    - **気力が低いと口数が減る**（25未満: 呼ばれても一言、雑談はリアクションだけ）。LLMに任せると無視された（2026-10-08 比較）
    """
    if state in ("寝ている", "バイト中", "離席中"):
        return "defer" if addressed else "none"
    tired = energy < 25
    if addressed:
        if state == "講義中" or tired:
            return "short"
        return "short" if r < 0.35 else "normal"
    if humans and share > 1.0 / (humans + 1):
        return "none"
    hot = interest is not None and interest >= INTEREST_TRUE
    if tired:
        return "reaction" if hot and r < 0.3 else "none"
    if state == "講義中":
        return "reaction" if hot and r < 0.10 else "none"
    if hot:
        if r < 0.30:
            return "normal"
        if r < 0.55:
            return "short"
        if r < 0.75:
            return "reaction"
        return "none"
    if r < 0.05:
        return "short"
    if r < 0.15:
        return "reaction"
    return "none"


def notice_sec(state, r):
    """メッセージに気付いて読み始めるまでの秒数。**いつも即答しない**（画面を見ていない時間がある）。"""
    if state == "講義中":
        return 180 + r * 720                      # 3〜15分
    if r < 0.70:
        return 5 + (r / 0.70) * 35                # 5〜40秒
    if r < 0.95:
        return 60 + ((r - 0.70) / 0.25) * 240     # 1〜5分
    return 300 + ((r - 0.95) / 0.05) * 600        # 5〜15分


def pending_alive(created, t):
    """書き始めてから送るまでが長すぎないか。"""
    return t - created <= PENDING_TTL


def defer_due(deferred, t, state):
    """後で返す分のうち、今返すべきもの（最新の1件）。期限切れは捨てる。"""
    alive = [d for d in deferred if t - datetime.fromisoformat(d["at"]) <= DEFER_TTL]
    if state in ("寝ている", "バイト中", "講義中", "離席中") or not alive:
        return alive, None
    return [], alive[-1]


def typing_sec(text, device="PC"):
    """打っている時間。**スマホはPCより遅い**（フリック入力）。"""
    if device == "スマホ":
        return max(2.0, min(15.0, len(text) * 0.4))
    return max(1.0, min(6.0, len(text) * 0.15))
