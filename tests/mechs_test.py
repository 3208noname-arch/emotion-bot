# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from engine import mechs as M

T = datetime(2026, 10, 13, 18, 0)
n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


reg = {}
M.used(reg, "mood", T, "minato 0.11.1")
M.used(reg, "mood", T + timedelta(minutes=5), "minato 0.11.2")
r = reg["mood"]
ok(r["n"] == 2 and r["first_ver"] == "minato 0.11.1" and r["last"] == "2026-10-13T18:05:00", "回数・最後・初めての版")
mood = next(x for x in M.CATALOG if x["key"] == "mood")
names = next(x for x in M.CATALOG if x["key"] == "names")
ok(M.status(mood, r, T + timedelta(hours=1)) == "on", "24時間以内に効いたら使用中")
ok(M.status(mood, r, T + timedelta(hours=30)) == "idle", "それより前ならしばらく動いていない")
ok(M.status(mood, None, T) == "never", "一度も動いていない")
ok(M.status(names, {"n": 1, "last": T.isoformat()}, T) == "count", "効かせないものは数えるだけ")
ok(len({x["key"] for x in M.CATALOG}) == len(M.CATALOG), "キーは重ならない")
print(f"ok {n}")
