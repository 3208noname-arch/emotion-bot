# -*- coding: utf-8 -*-
"""ナギの様子を見て、話題の好き嫌い（座標）を編集する Web UI。Bot とは別のサービスで動かす。

- state.json・ratings.jsonl は**読むだけ**（書くのは Bot。同時に書いて壊さないため）
- 書くのは topics.json だけ（話題の快・覚醒の座標。Bot は快を気分に使う。覚醒はまだ使わない）
- 外から届かないネットワークのアドレスで待ち受ける（認証は無い）
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from aiohttp import web

from engine import affinity, body, decide, mood, ratings, snapshot

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
TOPICS = os.path.join(HERE, "topics.json")
RATINGS = os.path.join(HERE, "ratings.jsonl")
LINKS = os.path.join(HERE, "links.json")   # 情報タブに出すリンクとメモ（見本は examples/links.example.json）
SERVICES = ("minato-bot", "nagi-ui")
JST = timezone(timedelta(hours=9))
HOST, PORT = (sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"), 8090


def now():
    return datetime.now(JST).replace(tzinfo=None)


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (IOError, ValueError):
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


async def state(_):
    s, t = load(STATE, {}), now()
    b = s.get("body")
    act = decide.activity(t, b) if b else {"state": "?", "label": "?", "device": "?"}
    likes = []
    for uid, p in s.get("likes", {}).items():
        likes.append({"id": uid, "display": p.get("display"), "v": round(p.get("v", 0), 1),
                      "band": affinity.band(p.get("v", 0)),
                      "events": [{**e, "when": affinity.when(datetime.fromisoformat(e["at"]), t)}
                                 for e in reversed(p.get("events", []))]})
    likes.sort(key=lambda x: -x["v"])
    md = None
    if b and s.get("mood"):
        m = json.loads(json.dumps(s["mood"]))   # 読むだけ。今の時刻まで戻した値を見せる
        base = mood.baseline(body.felt_sleepy(b, t), b["hunger"], b["energy"], b["money"] < body.BROKE)
        mood.settle(m, t, base)
        md = {"v": round(m["v"]), "base": round(base), "words": mood.as_words(m, base),
              "events": list(reversed(m["events"])), "hist": s.get("mood_hist", [])}
    c = s.get("counts", {})
    return web.json_response({
        "now": t.isoformat(timespec="seconds"), "stopped": s.get("stopped", False),
        "activity": act["label"], "state": act["state"], "device": act.get("device"),
        "body": {k: round(b[k]) for k in ("sleepy", "hunger", "energy")} | {"money": b["money"],
                 "felt_sleepy": round(body.felt_sleepy(b, t)), "feelings": body.as_feelings(b, t)} if b else None,
        "counts": {"total": c.get("total", 0), "max": decide.TOTAL_DAILY_MAX},
        "starts": s.get("starts", {}).get("n", 0) if s.get("starts", {}).get("day") == decide.day_key(t) else 0,
        "deferred": len(s.get("deferred", [])),
        "events": list(reversed(s.get("events", []))),
        "likes": likes,
        "mood": md,
        "names": s.get("names", {}),
    })


_timeline = {"at": None, "data": []}


async def timeline():
    """Bot の起動ログから [(起動した時刻, 版), ...] を作る（10分だけ覚えておく）。"""
    t = now()
    if _timeline["at"] and t - _timeline["at"] < timedelta(minutes=10):
        return _timeline["data"]
    p = await asyncio.create_subprocess_exec("journalctl", "-u", "minato-bot", "--no-pager", "-o", "cat",
                                             "-g", "起動。チャンネル", stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    data = []
    for ln in out.decode("utf-8", "replace").splitlines():
        m = re.match(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ INFO (\S+ \S+) 起動", ln)
        if m:
            data.append((datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), m.group(2)))
    _timeline.update(at=t, data=data)
    return data


def said_at(message_id):
    """Discord のメッセージIDから、送られた時刻（日本時間）を出す。"""
    ms = (int(message_id) >> 22) + 1420070400000
    return datetime.fromtimestamp(ms / 1000, JST).replace(tzinfo=None)


async def rating_list(_):
    rs = ratings.load(RATINGS)
    tl = await timeline()
    items = [{**r, "state_text": snapshot.describe(r.get("state")),
              "version": (r.get("state") or {}).get("version") or ratings.version_at(tl, said_at(r["message_id"]))}
             for r in reversed(rs)][:200]
    return web.json_response({"summary": ratings.format_summary(ratings.summary(rs)), "items": items})


async def log_tail(req):
    n = max(10, min(int(req.query.get("n", 200)), 1000))
    p = await asyncio.create_subprocess_exec("journalctl", "-u", "minato-bot", "-n", str(n), "--no-pager",
                                             "-o", "short-iso", stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    return web.json_response({"lines": out.decode("utf-8", "replace").splitlines()})


def check_topics(items):
    """座標の表を検める。おかしければ理由の文字列、良ければ整えた表を返す。"""
    if not isinstance(items, list) or len(items) > 2000:   # 覚えた話題は消さないので多めに
        return "表の形がおかしい"
    seen, out = set(), []
    for it in items:
        try:
            name = str(it["name"]).strip()
            v, a = float(it["valence"]), float(it["arousal"])
        except (KeyError, TypeError, ValueError):
            return "名前・快・覚醒のどれかが無い"
        if not name or len(name) > 20:
            return f"名前は1〜20文字: {name!r}"
        if name in seen:
            return f"名前が重複: {name}"
        if not (-1 <= v <= 1 and 0 <= a <= 1):
            return f"{name}: 快は-1〜1、覚醒は0〜1"
        seen.add(name)
        t = {"name": name, "valence": round(v, 2), "arousal": round(a, 2), "note": str(it.get("note", ""))[:80]}
        if it.get("source") == "learned":      # ナギが会話で覚えた話題（印と覚えた時刻を残す）
            t.update({"source": "learned", "at": str(it.get("at", ""))[:19]})
        out.append(t)
    return out


async def topics_get(_):
    return web.json_response(load(TOPICS, {"topics": [], "updated": None}))


async def topics_put(req):
    try:
        data = await req.json()
    except ValueError:
        return web.json_response({"error": "JSONではない"}, status=400)
    res = check_topics(data.get("topics"))
    if isinstance(res, str):
        return web.json_response({"error": res}, status=400)
    obj = {"topics": res, "updated": now().isoformat(timespec="seconds")}
    save(TOPICS, obj)
    return web.json_response(obj)


async def service(name):
    p = await asyncio.create_subprocess_exec("systemctl", "show", name, "-p", "ActiveState",
                                             "-p", "ActiveEnterTimestamp", stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate()
    kv = dict(ln.split("=", 1) for ln in out.decode().splitlines() if "=" in ln)
    return {"name": name, "state": kv.get("ActiveState", "?"), "since": kv.get("ActiveEnterTimestamp", "")}


async def info(_):
    tl = await timeline()
    return web.json_response({**load(LINKS, {"sections": [], "facts": []}),
                              "version": tl[-1][1] if tl else None,
                              "services": [await service(n) for n in SERVICES]})


# 仕組みの一覧（10-12 ユーザー「どこまで入っていて、どこがまだかを見られるUI」）。
# **仕組みを足したり効かせ方を変えたら、ここも直す**（説明文が古いまま残る事故が一度あった）。
# status: on（使っている）／count（数えるだけ。会話や行動にはまだ効かない）／todo（まだ作っていない）
MECHS = [
    {"name": "体（眠気・空腹・気力・残高）", "status": "on", "since": "0.5.0",
     "does": "寝る・起きる・食べる・抜けるを自分で選ぶ。体の感じを言葉でLLMに渡す", "value": "body"},
    {"name": "曜日と今日・明日の予定", "status": "on", "since": "0.9.6",
     "does": "LLMに日付・曜日・授業とバイトの予定を渡す（土日に1限の話をしない）", "value": "today"},
    {"name": "会話で言った行動", "status": "on", "since": "0.9.7",
     "does": "「今やった」と言ったことを出来事に残し、残高から引く", "value": "events"},
    {"name": "居心地（気分）", "status": "on", "since": "0.9.8",
     "does": "体・人・話題で動き、1時間で半分ずつ基準へ戻る。LLMに言葉で渡し、口調ににじませる。"
             "**返す・黙るの選び方にはまだ効かない**", "value": "mood"},
    {"name": "話題の快（好き嫌い）", "status": "on", "since": "0.9.8",
     "does": "話題が会話に出ると、快の向きに気分を動かす。好き嫌いをLLMに渡す（0.10.0〜）", "value": "topics"},
    {"name": "口にした好き嫌いを覚える", "status": "on", "since": "0.10.0",
     "does": "会話で言った好き嫌いを話題の図に足す（白抜きの点）。消さずに眠らせ、話題が出たら使う", "value": "learned"},
    {"name": "好感度", "status": "on", "since": "0.9.0",
     "does": "けなし・庇い・冗談・見下し（0.10.2〜）を前後6件の会話で判定して数える（0.9.9〜）。話しかけられた時の気分の動きに使う（0.9.8〜）。"
             "**態度・口調にはまだ効かない**", "value": "likes"},
    {"name": "呼び名", "status": "count", "since": "0.6.0",
     "does": "誰が誰をどう呼ぶかを集める。会話にはまだ使わない", "value": "names"},
    {"name": "話題の覚醒（乗りやすさ）", "status": "todo", "since": None,
     "does": "混ざるかどうか・反応の速さに効かせる予定。今は「大学生の身近な話題か」の判定で決めている", "value": None},
    {"name": "経験で話題の図を育てる", "status": "todo", "since": None,
     "does": "混ざったか素通りしたかで覚醒を、話題の後の気分で快を動かす（覚えた好き嫌いの2段目）", "value": None},
    {"name": "嫌いな話題は1回目は断る", "status": "todo", "since": None,
     "does": "嫌いな話題を振られたらまず断る。続けられたら好感度を下げる", "value": None},
    {"name": "好感度を「居心地の予想」にする", "status": "todo", "since": None,
     "does": "その人といると居心地がどうなるかの予想として、予想より良ければ上げる。親しいほど下がりにくい", "value": None},
    {"name": "行動を居心地の予想で選ぶ", "status": "todo", "since": None,
     "does": "返す・黙る・話題を変える・絡みに行く・抜けるを、居心地が上がりそうな方で選ぶ（今は手書きの確率表）",
     "value": None},
]


async def mechs(_):
    """仕組みの一覧と、それぞれの今の値。"""
    s, t = load(STATE, {}), now()
    b, tps = s.get("body"), load(TOPICS, {"topics": []})["topics"]
    learned = [x for x in tps if x.get("source") == "learned"]
    m = s.get("mood")
    if m and b:
        m = json.loads(json.dumps(m))
        base = mood.baseline(body.felt_sleepy(b, t), b["hunger"], b["energy"], b["money"] < body.BROKE)
        mood.settle(m, t, base)
    likes = s.get("likes", {})
    vals = {
        "body": b and body.as_feelings(b, t) + f"・残高{b['money']:,}円",
        "today": decide.day_text(t),
        "events": "／".join(e["what"] for e in s.get("events", [])[-3:]) or "無し",
        "mood": m and b and f"{mood.as_words(m, base)}（{round(m['v']):+d}）",
        "topics": f"{len(tps) - len(learned)}件（人が置いた分）",
        "learned": f"{len(learned)}件" + (f"（最近: {learned[-1]['name']}）" if learned else ""),
        "likes": f"{len(likes)}人・" + "、".join(f"{p['display']}{round(p['v']):+d}" for p in
                                                  sorted(likes.values(), key=lambda p: -p["v"])[:3]),
        "names": f"{len(s.get('names', {}))}人分",
    }
    return web.json_response({"items": [{**x, "now": vals.get(x["value"]) if x["value"] else None} for x in MECHS]})


async def index(_):
    return web.FileResponse(os.path.join(HERE, "web", "ui.html"))


def app():
    a = web.Application()
    a.add_routes([web.get("/", index), web.get("/api/state", state), web.get("/api/ratings", rating_list),
                  web.get("/api/log", log_tail), web.get("/api/info", info), web.get("/api/mechs", mechs), web.get("/api/topics", topics_get), web.put("/api/topics", topics_put)])
    return a


if __name__ == "__main__":
    web.run_app(app(), host=HOST, port=PORT, print=None)
