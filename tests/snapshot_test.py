# -*- coding: utf-8 -*-
from datetime import datetime

from engine import snapshot as S

T = datetime(2026, 10, 10, 20, 0)
b = {"sleepy": 33.4, "hunger": 18.9, "energy": 72.6, "money": 5900}
s = S.build(T, "ふつう", b, "特に無し", relation=21.25, decision={"source": "reply", "kind": "short", "hot": True})
assert s["body"] == {"sleepy": 33, "hunger": 19, "energy": 73, "money": 5900} and s["relation"] == 21.2, s
assert s["mood"] is None, "気分は未実装の間は None"
d = S.describe(s)
assert d.startswith("返事・ふつう・眠気33") and "好感度+21.2" in d and "食いつく話題" in d, d
assert S.describe(None) == ""
recs = [{**s, "message_ids": [1, 2]}, {**s, "message_ids": [3], "activity": "深夜"}]
assert S.find(recs, 2)["activity"] == "ふつう" and S.find(recs, 3)["activity"] == "深夜" and S.find(recs, 9) is None
print("ok 5")
