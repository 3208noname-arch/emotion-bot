# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import decide as D

MON = datetime(2026, 10, 5)   # 月曜（1限あり）
TUE = datetime(2026, 10, 6)   # 火曜（1限なし・バイト）
SUN = datetime(2026, 10, 11)
n = 0


def eq(got, want, what):
    global n
    assert got == want, f"{what}: {got!r} != {want!r}"
    n += 1


def st(t):
    return D.activity(t)["state"]


# 今していること
eq(st(MON.replace(hour=5)), "寝ている", "朝5時は寝ている")
eq(st(MON.replace(hour=7, minute=59)), "寝ている", "1限の日は8時まで寝ている")
eq(st(MON.replace(hour=8, minute=30)), "起きたて", "1限の日は8時起き")
eq(st(MON.replace(hour=9, minute=30)), "講義中", "月1限は語学")
eq(st(TUE.replace(hour=9, minute=30)), "寝ている", "1限が無い日は10時半まで寝ている")
eq(st(TUE.replace(hour=11)), "講義中", "火2限")
eq(st(TUE.replace(hour=20)), "バイト中", "火曜20時はバイト")
eq(st(TUE.replace(hour=23, minute=59)), "バイト中", "ラストまでバイト")
eq(st(MON.replace(hour=20)), "ふつう", "月曜20時はバイトなし")
eq(st(MON.replace(hour=1)), "深夜", "1時は深夜")
eq(st(SUN.replace(hour=11)), "起きたて", "日曜11時は起きたて")
eq(D.activity(MON.replace(hour=1))["device"], "PC", "深夜はPC")
eq(D.activity(TUE.replace(hour=11))["device"], "スマホ", "講義中はスマホ")
eq(D.activity(TUE.replace(hour=20))["until"], TUE.replace(hour=23, minute=59) + timedelta(minutes=1), "バイトは24時まで")

# 上限
c = {}
t = MON.replace(hour=20)
eq(D.budget_ok(c, 1, t), True, "初回はOK")
for _ in range(D.USER_DAILY_MAX):
    D.count(c, 1, t)
eq(D.budget_ok(c, 1, t), False, "1人の上限で止まる")
eq(D.budget_ok(c, 2, t), True, "別の人はまだOK")
eq(D.budget_ok(c, 1, t + timedelta(days=1)), True, "翌日は戻る")
eq(D.day_key(TUE.replace(hour=2)), "2026-10-05", "深夜2時は前日に数える")
c2 = {"day": D.day_key(t), "total": D.TOTAL_DAILY_MAX, "users": {}}
eq(D.budget_ok(c2, 9, t), False, "全体の上限で止まる")

# 発言シェア
me = "湊"
recent = [{"author": "A"}, {"author": me}, {"author": "B"}, {"author": me}]
eq(D.my_share(recent, me), (0.5, 2), "シェアと人数")
eq(D.my_share([{"author": "A"}, {"author": "X", "bot": True}], me)[1], 1, "Botは人数に数えない")

# 型の選択
eq(D.choose(True, None, 0.9, 3, "寝ている", 0.0), "defer", "寝ている間に呼ばれたら後で返す")
eq(D.choose(False, 0.99, 0.0, 3, "寝ている", 0.0), "none", "寝ている間の雑談は拾わない")
eq(D.choose(True, None, 0.0, 3, "バイト中", 0.9), "defer", "バイト中に呼ばれたら後で返す")
eq(D.choose(True, None, 0.0, 3, "講義中", 0.9), "short", "講義中は一言")
eq(D.choose(False, 0.99, 0.0, 3, "講義中", 0.5), "none", "講義中の雑談はほぼ拾わない")
eq(D.choose(True, None, 0.9, 3, "ふつう", 0.9), "normal", "話しかけられたらシェアに関係なく返す")
eq(D.choose(False, 0.99, 0.5, 2, "ふつう", 0.0), "none", "喋りすぎなら黙る（2人なら1/3超え）")
eq(D.choose(False, 0.99, 0.1, 2, "ふつう", 0.1), "normal", "食いつく話題は返す")
eq(D.choose(False, 0.99, 0.1, 2, "ふつう", 0.6), "reaction", "食いつく話題でもリアクションだけの時がある")
eq(D.choose(False, None, 0.1, 2, "ふつう", 0.1), "reaction", "棄権は食いつかない扱い")
eq(D.choose(False, 0.5, 0.1, 2, "ふつう", 0.5), "none", "関心が薄ければ大半は黙る")

# 気付くまで・打つ速さ
eq(D.notice_sec("ふつう", 0.0), 5.0, "最短5秒")
eq(round(D.notice_sec("ふつう", 0.69)), 40, "7割は40秒以内")
eq(D.notice_sec("ふつう", 0.80) > 60, True, "たまに数分")
eq(D.notice_sec("講義中", 0.0), 180.0, "講義中は3分以上")
eq(D.typing_sec("あ" * 20, "スマホ") > D.typing_sec("あ" * 20, "PC"), True, "スマホは遅い")
eq(D.typing_sec("あ" * 100), 6.0, "PCの待ちは6秒まで")

# 後で返す分
d = [{"at": TUE.replace(hour=19).isoformat(), "id": 1}, {"at": TUE.replace(hour=20).isoformat(), "id": 2}]
eq(D.defer_due(d, TUE.replace(hour=21), "バイト中"), (d, None), "バイト中はまだ返さない")
eq(D.defer_due(d, TUE.replace(hour=23, minute=59) + timedelta(minutes=5), "深夜"), ([], d[1]), "終わったら最新の1件に返す")
eq(D.defer_due(d, TUE + timedelta(days=1, hours=10), "ふつう"), ([], None), "12時間過ぎたら捨てる")

# 体の変数がある時
b = {"asleep": True}
eq(st_b := D.activity(TUE.replace(hour=14), b)["state"], "寝ている", "体が寝ていれば昼でも寝ている（講義を寝過ごす）")
b = {"asleep": False, "away_until": TUE.replace(hour=20, minute=10).isoformat(), "away_why": "コンビニ"}
eq(D.activity(MON.replace(hour=20), {**b, "away_until": MON.replace(hour=20, minute=10).isoformat()})["state"], "離席中", "コンビニ中は離席")
eq(D.activity(MON.replace(hour=3), {"asleep": False})["state"], "深夜", "体が起きていれば3時でも深夜")
eq(D.activity(MON.replace(hour=13, minute=30), {"asleep": False, "woke_at": MON.replace(hour=13).isoformat()})["state"], "講義中", "講義は起きたてより先")
eq(D.activity(SUN.replace(hour=13, minute=30), {"asleep": False, "woke_at": SUN.replace(hour=13).isoformat()})["state"], "起きたて", "起きて90分は起きたて")
eq(D.choose(True, None, 0.0, 3, "離席中", 0.0), "defer", "離席中に呼ばれたら後で返す")
eq(D.choose(True, None, 0.0, 3, "ふつう", 0.9, energy=20), "short", "気力が低いと呼ばれても一言")
eq(D.choose(False, 0.99, 0.0, 3, "ふつう", 0.1, energy=20), "reaction", "気力が低いと雑談はリアクションだけ")
eq(D.choose(False, 0.99, 0.0, 3, "ふつう", 0.5, energy=20), "none", "気力が低いと大半は黙る")
eq(D.schedule(TUE.replace(hour=20)), "バイト中", "予定だけの判定")
eq(D.alarm(MON)[0], True, "月曜は目覚まし")
eq(D.alarm(TUE)[0], False, "火曜は目覚ましなし")

# 経過時間
eq(D.ago(30), "今", "1分未満は今")
eq(D.ago(5 * 60 + 59), "5分前", "分は切り捨て")
eq(D.ago(3 * 3600 + 120), "3時間前", "1時間以上は時間")
eq(D.ago(50 * 3600), "2日前", "1日以上は日")

print(f"ok {n}")
