# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from engine import body as B

T = datetime(2026, 10, 6, 20, 0)   # 火曜20時
n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


def at(b, t, state="ふつう", alarm=False):
    return B.tick(b, t, state, alarm, t.replace(hour=8, minute=0))


b = B.new(T); b["sleepy"] = 30
at(b, T + timedelta(hours=2))
ok(abs(b["sleepy"] - 39) < 0.01, "起きている間は毎時+4.5")
b = B.new(T); b["sleepy"] = 30
at(b, T + timedelta(hours=2), "バイト中")
ok(abs(b["sleepy"] - 40) < 0.01, "バイト中は毎時+5")

b = B.new(T.replace(hour=23, minute=29)); b["sleepy"] = 85
ok("寝た" not in at(b, T.replace(hour=23, minute=30)), "23時台は92まで粘る")
b = B.new(T); b["sleepy"] = 79; b["at"] = T.replace(hour=1).isoformat()
ev = at(b, T.replace(hour=1, minute=30))
ok("寝た" in ev and b["asleep"], "1時以降は80を超えたら寝る")
b = B.new(T); b["sleepy"] = 85
ok("寝た" not in at(b, T + timedelta(minutes=1), "バイト中"), "バイト中は寝ない")
b = B.new(T); b["sleepy"] = 85
ok("寝た" not in at(b, T.replace(hour=15)), "昼は100未満なら寝ない")

N = T.replace(hour=1); b = B.new(N); b["sleepy"] = 85; b["fun_until"] = (N + timedelta(minutes=10)).isoformat()
ok("寝た" not in at(b, N.replace(minute=5)), "楽しい間は夜でも寝ない")
ok("寝た" in at(b, N.replace(minute=20)), "楽しいのが終わるとまとめて来る")

# 起きる
b = B.new(T); b["asleep"] = True; b["sleepy"] = 60; b["at"] = T.replace(hour=3).isoformat()
ev = B.tick(b, T.replace(hour=8, minute=0), "ふつう", True, T.replace(hour=8, minute=0))
ok("起きた" in ev, "1限の日は目覚ましで起きる（眠いまま）")
b = B.new(T); b["asleep"] = True; b["sleepy"] = 90; b["at"] = T.replace(hour=3).isoformat()
ev = B.tick(b, T.replace(hour=9), "ふつう", False, None)
ok("起きた" not in ev, "目覚ましが無い日は眠気が抜けるまで寝る")
ev = B.tick(b, T.replace(hour=12), "ふつう", False, None)
ok("起きた" in ev, "それでも12時には起きる")

# 食べる
b = B.new(T); b["hunger"] = 50
at(b, T.replace(hour=12, minute=30))
ok(b["hunger"] == 0 and b["money"] == 6300, "昼を食べる")
b = B.new(T.replace(hour=12)); b["hunger"] = 50; b["money"] = 1500
at(b, T.replace(hour=12, minute=30))
ok(b["hunger"] > 50, "金欠なら昼を抜く")
b = B.new(T); b["hunger"] = 75; b["energy"] = 50
ev = at(b, T + timedelta(minutes=1))
ok("コンビニに行く" in ev and b["away_why"] == "コンビニ", "腹が減ったらコンビニ")
ok(B.felt_sleepy(b, T + timedelta(minutes=2)) >= 30 + B.POST_MEAL_SLEEPY - 0.01 and b["sleepy"] < 31, "食後は1時間だけ眠い（溜まる眠気には足さない）")
b = B.new(T); b["hunger"] = 75; b["energy"] = 10
ev = at(b, T + timedelta(minutes=1))
ok("コンビニに行く" not in ev and "落ちる" not in ev, "気力が低いと食べに行くのも面倒。まだ落ちはしない")
b = B.new(T); b["energy"] = 5
ev = at(b, T + timedelta(minutes=1))
ok("落ちる" in ev, "気力が尽きたら落ちる")
b["away_until"] = None; b["energy"] = 5; b["at"] = (T + timedelta(minutes=59)).isoformat()
ev = at(b, T + timedelta(hours=1))
ok("落ちる" not in ev, "落ちてから3時間は、また落ちない")
b["energy"] = 5; b["at"] = (T + timedelta(hours=3)).isoformat()
ev = at(b, T + timedelta(hours=3, minutes=1))
ok("落ちる" in ev, "3時間過ぎたらまた落ちうる")
b2 = B.new(T)
ok(B.after_baito(b2, T) and b2["hunger"] == 0, "まかないで空腹が戻る")

# 相互作用
b = B.new(T); b["hunger"] = 80; b["energy"] = 50
at(b, T + timedelta(hours=1))
ok(abs(b["energy"] - (50 + B.ENERGY_IDLE / 2)) < 0.01, "腹が減っていると気力の戻りが半分")
b = B.new(T); b["sleepy"] = 70
b["at"] = T.replace(hour=1).isoformat(); at(b, T.replace(hour=1) + timedelta(hours=1))
ok(abs(b["hunger"] - 42) < 0.01, "深夜に眠いと腹が減る")
b = B.new(T); e0 = b["energy"]; B.spend_talk(b, 30, T)
b2 = B.new(T); b2["sleepy"] = 80; B.spend_talk(b2, 30, T)
ok(e0 - b2["energy"] == 2 * (e0 - b["energy"]), "眠いと話す気力の減りが倍")

# お金
b = B.new(T.replace(day=25), money=1000)
at(b, T.replace(day=25, hour=20, minute=1))
ok(b["money"] == 86000, "給料日")
at(b, T.replace(day=27, hour=20))
ok(b["money"] == 16000, "引き落とし")
at(b, T.replace(day=28, hour=20))
ok(b["money"] == 16000, "月に1回だけ")

# 渡し方
b = B.new(T); b["sleepy"] = 90; b["hunger"] = 85
ok(B.as_feelings(b, T) == "まぶたが落ちそう、腹が減って力が出ない", "強い感覚を2つ")
ok(B.as_feelings(B.new(T), T) == "特に無し（ふつう）", "何も無ければふつう")
ok("眠気90/100" in B.as_numbers(b, T), "数値で渡す形")

b = B.new(T); b["asleep"] = True; b["sleepy"] = 99
ev = B.tick(b, T.replace(hour=22, minute=1), "ふつう", True, T.replace(hour=8))
ok("起きた" not in ev, "夜は目覚ましが効かない（その日の8時を過ぎていても）")
print(f"ok {n}")
