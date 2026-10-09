# -*- coding: utf-8 -*-
from datetime import datetime

from engine import names as N

n = 0


def ok(cond, msg):
    global n
    assert cond, msg
    n += 1


T = datetime(2026, 10, 9, 20, 0)
ok(N.clean("ポン酢さん") == "ポン酢", "敬称は外す")
ok(N.clean("「なぎ」") == "なぎ", "括弧は外す")
ok(N.clean("あっちゃん") == "あっちゃん", "ちゃんはあだ名の一部")
ok(N.valid("ぽんず", "ぽんずさぁ、それ違う"), "本文にあれば受け付ける")
ok(not N.valid("ポン酢", "ぽんずさぁ"), "本文に無い言い換えは受け付けない")
ok(not N.valid("@N4Gi", "@N4Gi 起きてる？"), "メンションは呼び名ではない")

s = {}
N.record(s, 1, "ポン酢", "ぽんず", 10, T)
N.record(s, 1, "ポン酢", "ぽんず", 10, T)
N.record(s, 1, "ポン酢", "ぽんず", 10, T)
ok(N.adopted(s, 1) == [], "1人の癖だけでは採用しない")
N.record(s, 1, "ポン酢", "ぽんず", 11, T)
ok(N.adopted(s, 1) == ["ぽんず"], "2人以上・3回以上で採用")
N.record(s, 99, "N4Gi", "なぎ", 10, T)
ok(N.format_summary(s, 99).startswith("ナギ: なぎ 1回/1人"), "ナギへの呼び名を先に出す")

print(f"ok {n}")
