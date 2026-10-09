# -*- coding: utf-8 -*-
"""呼び名を集める（純関数）。**今は集めるだけで、会話にはまだ使わない。**

- 人間どうしの呼び方と、ナギへの呼び方の両方を、呼ばれた人のユーザーIDごとに数える
- 1人の癖で決まらないよう、採用は「2人以上から、合わせて3回以上」
- 抜き出した呼び名は、発言の本文に実際に含まれているものだけ受け付ける（LLMの言い換えを入れない）
"""
import re

ADOPT_TIMES = 3
ADOPT_CALLERS = 2
MAX_LEN = 12
HONORIFIC = re.compile(r"(さん|くん|君|氏|様)$")   # 「ちゃん」はあだ名の一部（あっちゃん）なので外さない


def clean(form):
    """呼び名の形を揃える。敬称は外す（「ポン酢さん」も「ポン酢」も同じ呼び名）。"""
    form = (form or "").strip().strip("「」『』\"'、。！？!?…~〜 ")
    return HONORIFIC.sub("", form)


def valid(form, text):
    return bool(form) and len(form) <= MAX_LEN and not form.startswith("@") and form in text


def record(store, target_id, display, form, caller_id, t):
    p = store.setdefault(str(target_id), {"display": display, "forms": {}})
    p["display"] = display
    f = p["forms"].setdefault(form, {"n": 0, "by": [], "last": None})
    f["n"] += 1
    if caller_id not in f["by"]:
        f["by"].append(caller_id)
    f["last"] = t.isoformat(timespec="seconds")


def adopted(store, target_id):
    """採用された呼び名を、多い順に。"""
    forms = store.get(str(target_id), {}).get("forms", {})
    ok = [(k, v["n"]) for k, v in forms.items() if v["n"] >= ADOPT_TIMES and len(v["by"]) >= ADOPT_CALLERS]
    return [k for k, _ in sorted(ok, key=lambda x: -x[1])]


def format_summary(store, me_id):
    if not store:
        return "呼び名: まだ無し"
    lines = []
    for uid, p in sorted(store.items(), key=lambda x: x[0] != str(me_id)):
        who = "ナギ" if uid == str(me_id) else p["display"]
        forms = sorted(p["forms"].items(), key=lambda x: -x[1]["n"])
        lines.append(f"{who}: " + "・".join(f"{k} {v['n']}回/{len(v['by'])}人" for k, v in forms[:6]))
    return "\n".join(lines)
