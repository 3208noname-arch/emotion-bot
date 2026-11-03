# -*- coding: utf-8 -*-
"""ナギの様子を見て、話題の好き嫌い（座標）を編集する Web UI。Bot とは別のサービスで動かす。

- state.json・ratings.jsonl は**読むだけ**（書くのは Bot。同時に書いて壊さないため）
- 書くのは topics.json・lessons.json（消すだけ）・changelog.json（お知らせの下書き）（話題の快・覚醒の座標。Bot は快を気分に使う。覚醒はまだ使わない）
- 外から届かないネットワークのアドレスで待ち受ける（認証は無い）
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from aiohttp import web

from deploy import changelog
from engine import affinity, anger, body, decide, mechs as mech, mood, ratings, snapshot

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
        "anger": anger_list(s, t),
    })


def anger_list(s, t):
    """人ごとの怒り（今の時刻まで冷ました値。読むだけ）。"""
    out = []
    for uid, r in json.loads(json.dumps(s.get("anger", {}))).items():
        close = s.get("likes", {}).get(uid, {}).get("v", 0) >= anger.CLOSE_FROM
        v = anger.cool(r, t, close)
        if v >= anger.FORGET:
            out.append({"display": r.get("display"), "v": round(v), "band": anger.band(v), "why": r.get("why"),
                        "kind": r.get("kind")})
    return sorted(out, key=lambda x: -x["v"])


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
        elif isinstance(it.get("anchor"), dict):   # 人が置いた場所（経験で ±0.2 まで動く。その錨）
            try:
                t["anchor"] = {"valence": float(it["anchor"]["valence"]), "arousal": float(it["anchor"]["arousal"])}
            except (KeyError, TypeError, ValueError):
                pass
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
        "did": "／".join(e["what"] for e in s.get("events", [])[-3:]) or "無し",
        "mood": m and b and f"{mood.as_words(m, base)}（{round(m['v']):+d}）",
        "topic_valence": f"{len(tps) - len(learned)}件（人が置いた分）",
        "learned": f"{len(learned)}件" + (f"（最近: {learned[-1]['name']}）" if learned else ""),
        "likes": f"{len(likes)}人・" + "、".join(f"{p['display']}{round(p['v']):+d}" for p in
                                                  sorted(likes.values(), key=lambda p: -p["v"])[:3]),
        "names": f"{len(s.get('names', {}))}人分",
        "lessons": f"{len(load(LESSONS, {'items': []})['items'])}件",
        "anger": "、".join(f"{r.get('display')}{round(r['v'])}" for r in s.get("anger", {}).values()) or "誰にも怒っていない",
        "experience": f"見ている話題 {len(s.get('episodes', []))}件",
        "policy_learn": "予想 " + ("、".join(f"{k}{v:+.1f}" for k, v in s.get("policy", {}).get("q", {}).items()) or "まだ無し"),
    }
    reg = s.get("mechs", {})
    items = [{"name": x["name"], "does": x["does"], "status": mech.status(x, reg.get(x["key"]), t),
              "since": (reg.get(x["key"]) or {}).get("first_ver"), "first": (reg.get(x["key"]) or {}).get("first"),
              "last": (reg.get(x["key"]) or {}).get("last"), "n": (reg.get(x["key"]) or {}).get("n", 0),
              "now": vals.get(x["key"])} for x in mech.CATALOG]
    items += [{**x, "status": "todo"} for x in mech.PLANNED]
    return web.json_response({"items": items, "since": min((r["first"] for r in reg.values()), default=None)})


LESSONS = os.path.join(HERE, "lessons.json")


async def lessons_get(_):
    return web.json_response(load(LESSONS, {"items": []}))


async def lessons_delete(req):
    """間違って覚えたものを消す。"""
    lid = int(req.match_info["id"])
    obj = load(LESSONS, {"items": []})
    obj["items"] = [x for x in obj["items"] if x.get("id") != lid]
    save(LESSONS, obj)
    return web.json_response(obj)


CHANGELOG = os.path.join(HERE, "changelog.json")   # お知らせの下書きと流した記録（GitHubに入れない）
post_lock = asyncio.Lock()


def find_note(obj, nid):
    return next((x for x in obj["items"] if x.get("id") == nid), None)


async def notes_get(_):
    return web.json_response(load(CHANGELOG, {"items": []}))


async def notes_new(req):
    """新しい下書き。版は空でもよい。"""
    body = await req.json()
    obj = load(CHANGELOG, {"items": []})
    nid = max([x["id"] for x in obj["items"]], default=0) + 1
    obj["items"].append({"id": nid, "version": str(body.get("version", "")).strip(), "text": str(body.get("text", "")),
                         "status": "draft", "at": now().isoformat(timespec="minutes")})
    save(CHANGELOG, obj)
    return web.json_response(obj)


async def notes_put(req):
    """下書きを書き換える。流した分は書き換えない。"""
    body = await req.json()
    obj = load(CHANGELOG, {"items": []})
    x = find_note(obj, int(req.match_info["id"]))
    if not x or x["status"] != "draft":
        return web.json_response({"error": "下書きが見つからない（流した分は書き換えられない）"}, status=409)
    x["version"], x["text"] = str(body.get("version", x["version"])).strip(), str(body.get("text", x["text"]))
    save(CHANGELOG, obj)
    return web.json_response(obj)


async def notes_delete(req):
    obj = load(CHANGELOG, {"items": []})
    x = find_note(obj, int(req.match_info["id"]))
    if x and x["status"] == "draft":
        obj["items"].remove(x)
        save(CHANGELOG, obj)
    return web.json_response(obj)


async def notes_post(req):
    """Webhook に流す。同じ下書きを二度流さない（連打しても1回）。"""
    async with post_lock:
        obj = load(CHANGELOG, {"items": []})
        x = find_note(obj, int(req.match_info["id"]))
        if not x or x["status"] != "draft":
            return web.json_response({"error": "流せる下書きが無い（もう流した？）"}, status=409)
        try:
            parts = await asyncio.get_running_loop().run_in_executor(None, changelog.post, x["text"])
        except Exception as e:
            return web.json_response({"error": f"流せなかった: {e}"}, status=502)
        x.update(status="posted", posted_at=now().isoformat(timespec="minutes"), parts=parts)
        save(CHANGELOG, obj)
        print("お知らせを流した:", x["version"], len(x["text"].strip()), "字", flush=True)
        return web.json_response(obj)


async def index(_):
    return web.FileResponse(os.path.join(HERE, "web", "ui.html"))


def app():
    a = web.Application()
    a.add_routes([web.get("/", index), web.get("/api/state", state), web.get("/api/ratings", rating_list),
                  web.get("/api/log", log_tail), web.get("/api/info", info), web.get("/api/mechs", mechs), web.get("/api/topics", topics_get),
                  web.get("/api/lessons", lessons_get), web.delete("/api/lessons/{id}", lessons_delete), web.put("/api/topics", topics_put),
                  web.get("/api/notes", notes_get), web.post("/api/notes", notes_new), web.put("/api/notes/{id}", notes_put),
                  web.delete("/api/notes/{id}", notes_delete), web.post("/api/notes/{id}/post", notes_post)])
    return a


if __name__ == "__main__":
    web.run_app(app(), host=HOST, port=PORT, print=None)
