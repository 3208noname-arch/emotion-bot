# -*- coding: utf-8 -*-
"""ナギの様子を見て、話題の好き嫌い（座標）を編集する Web UI。Bot とは別のサービスで動かす。

- state.json・ratings.jsonl は**読むだけ**（書くのは Bot。同時に書いて壊さないため）
- 書くのは topics.json だけ（話題の快・覚醒の座標。Bot は数日後に読み始める）
- 外から届かないネットワークのアドレスで待ち受ける（認証は無い）
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from aiohttp import web

from engine import affinity, body, decide, ratings, snapshot

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
    if not isinstance(items, list) or len(items) > 200:
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
        out.append({"name": name, "valence": round(v, 2), "arousal": round(a, 2),
                    "note": str(it.get("note", ""))[:80]})
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


async def index(_):
    return web.FileResponse(os.path.join(HERE, "web", "ui.html"))


def app():
    a = web.Application()
    a.add_routes([web.get("/", index), web.get("/api/state", state), web.get("/api/ratings", rating_list),
                  web.get("/api/log", log_tail), web.get("/api/info", info), web.get("/api/topics", topics_get), web.put("/api/topics", topics_put)])
    return a


if __name__ == "__main__":
    web.run_app(app(), host=HOST, port=PORT, print=None)
