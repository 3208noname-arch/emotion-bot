# -*- coding: utf-8 -*-
from engine import topics as P

n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


ts = [{"name": "まかない", "valence": 0.8, "arousal": 0.7, "note": ""},
      {"name": "虫", "valence": -0.8, "arousal": 0.9, "note": ""}]

ok(P.learn(ts, "カラオケ", 0.6, "2026-10-12T10:00:00") == "added", "新しい話題を足す")
k = ts[-1]
ok(k["source"] == "learned" and k["valence"] == 0.6 and k["arousal"] == 0.5, "覚えた印・言った向き・覚醒0.5")
ok(P.learn(ts, "カラオケ", -1.0, "2026-10-12T11:00:00") == "moved" and k["valence"] == 0.12, "言うたびに3割寄せる")
ok(P.learn(ts, "虫", 1.0, "2026-10-12T11:00:00") == "kept" and ts[1]["valence"] == -0.8, "人が置いた話題は書き換えない")
ok(P.learn(ts, "「ピーマン」", 5, "x") == "added" and ts[-1]["name"] == "ピーマン" and ts[-1]["valence"] == 1.0,
   "かぎ括弧を外し、快は-1〜1で切る")
ok(P.learn(ts, "", 0.5, "x") is None and P.learn(ts, "a" * 21, 0.5, "x") is None, "名前は1〜20文字")
ok(P.learn(ts, "銭湯", "好き", "x") is None, "快が数でなければ何もしない")

ts = []
for i in range(P.LEARNED_MAX + 2):
    P.learn(ts, f"話題{i}", 0.5, f"2026-10-12T10:{i:02d}:00")
ok(len(ts) == P.LEARNED_MAX and ts[0]["name"] == "話題2", "上限を超えたら古いものから消す")

ts = [{"name": "まかない", "valence": 0.8, "arousal": 0.7}, {"name": "政治", "valence": -0.1, "arousal": 0.1},
      {"name": "虫", "valence": -0.8, "arousal": 0.9}]
ok(P.as_words(ts) == "好き: まかない／嫌い: 虫", "どうでもいい話題は渡さない")
print(f"ok {n}")
