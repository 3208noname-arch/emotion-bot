# -*- coding: utf-8 -*-
import os
import tempfile

from engine import ratings as R

p = os.path.join(tempfile.mkdtemp(), "r.jsonl")
R.append(p, {"rater_id": 1, "message_id": 10, "is_me": True, "label": "自然"})
R.append(p, {"rater_id": 1, "message_id": 10, "is_me": True, "label": "流れとズレてる"})  # 付け直し
R.append(p, {"rater_id": 2, "message_id": 10, "is_me": True, "label": "自然"})
R.append(p, {"rater_id": 1, "message_id": 11, "is_me": False, "label": "キャラじゃない"})
with open(p, "a", encoding="utf-8") as f:
    f.write("壊れた行\n")
s = R.summary(R.load(p))
assert s["bot"]["流れとズレてる"] == 1 and s["bot"]["自然"] == 1, s   # 付け直しは新しい方
assert s["human"]["キャラじゃない"] == 1, s
assert "ナギ: 2件" in R.format_summary(s), R.format_summary(s)
assert R.format_summary(R.summary([])).count("まだ評価なし") == 2
assert R.load(p + ".none") == []
print("ok 5")
