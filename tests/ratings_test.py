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
from datetime import datetime
tl = [(datetime(2026, 10, 8, 2), "minato 0.1.0"), (datetime(2026, 10, 9, 14), "minato 0.9.0")]
assert R.version_at(tl, datetime(2026, 10, 9, 12)) == "minato 0.1.0"
assert R.version_at(tl, datetime(2026, 10, 9, 15)) == "minato 0.9.0"
assert R.version_at(tl, datetime(2026, 10, 7)) is None, "起動より前は分からない"
p2 = os.path.join(tempfile.mkdtemp(), "r.jsonl")
R.append(p2, {"rater_id": 1, "message_id": 20, "is_me": True, "label": "感情がズレてる", "detail": "強すぎ"})
R.append(p2, {"rater_id": 2, "message_id": 20, "is_me": True, "label": "感情がズレてる", "detail": "強すぎ"})
R.append(p2, {"rater_id": 1, "message_id": 21, "is_me": True, "label": "感情がズレてる", "detail": "感情の種類が違う"})
s2 = R.summary(R.load(p2))
assert s2["details"]["bot"]["感情がズレてる"] == {"強すぎ": 2, "感情の種類が違う": 1}, s2
assert "感情がズレてる 3（100% 強すぎ2・感情の種類が違う1）" in R.format_summary(s2), R.format_summary(s2)
assert R.label_text({"label": "感情がズレてる", "detail": "弱すぎ"}) == "感情がズレてる（弱すぎ）"
print("ok 11")
