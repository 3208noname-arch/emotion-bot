# -*- coding: utf-8 -*-
from engine import lessons as L

n = 0


def ok(cond, what):
    global n
    assert cond, what
    n += 1


items = []
ok(L.add(items, "ﾇﾙﾎﾟと言われたらｶﾞｯと返すのがお約束", ["ﾇﾙﾎﾟ", "ぬるぽ"], "ポン酢", "2026-10-27T16:56:00", "評価") == "added",
   "覚え書きを足す")
ok(L.add(items, "ﾇﾙﾎﾟと言われたら ｶﾞｯ と返すのがお約束", ["ガッ"], "かき", "2026-10-28T10:00:00", "会話") == "again"
   and items[0]["n"] == 2 and "ガッ" in items[0]["keywords"] and len(items) == 1, "同じ事実は足さずに回数を増やす")
L.add(items, "レジ袋は1枚3円くらい", ["レジ袋"], "ポン酢", "2026-10-27T17:02:00", "評価")
ok(L.add(items, "", ["x"], "x", "x", "会話") is None, "空の事実は覚えない")
ok([x["id"] for x in items] == [1, 2], "番号を振る（消す時に使う）")

for i in range(10):
    L.add(items, f"事実{i}", [f"語{i}"], "x", f"2026-10-29T10:{i:02d}:00", "会話")
facts = [x["fact"] for x in L.relevant(items, "なあ、ぬるぽ")]
ok(facts[0].startswith("ﾇﾙﾎﾟ") and len(facts) <= L.MAX_SHOW, "会話に言葉が出たものを先に、最大8件")
ok("レジ袋は1枚3円くらい" not in [x["fact"] for x in L.relevant(items, "今日なに食う")], "古くて関係の無いものは眠らせる")
ok(L.as_lines(items[:1]) == ["ﾇﾙﾎﾟと言われたらｶﾞｯと返すのがお約束（ポン酢に教わった）"], "誰に教わったかを添える")
print(f"ok {n}")
