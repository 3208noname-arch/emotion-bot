"""Jev（TypeSafe）: 会話の読み取り。返すかどうかは決めない。"""
import logging

import aiohttp

from app.store import CONFIG

log = logging.getLogger("minato")

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_INTEREST = ("会話の最後の話題は、睡眠・夜更かし・課題・授業・バイト・お金・ゲーム・食べ物・ラジオ・銭湯・"
                "好きなキャラや推し・好みのタイプなど、"
                "大学生が日常で愚痴ったり盛り上がったりする身近な話題か。")


JEV_INVITE = ("会話の最後の発言は、特定の相手ではなく、その場にいる誰かに話しかけて、"
              "話し相手や反応を求めているか（例: 誰か話そう、暇な人いる？、ねえ聞いて）。")


async def jev_interest(text):
    return await jev(text, JEV_INTEREST)


async def jev(text, question):
    """確率。**失敗は None（棄権）で、偽ではない。**"""
    return (await jev_many(text, {"q": question})).get("q")


async def jev_many(text, questions):
    """複数の問いを1回で聞く。{名前: 確率}。失敗した問いは入らない（棄権）。"""
    key = CONFIG.get("typesafe_key")
    if not key:
        return {}
    body = {"state": text, "model": "jev-latest",
            "questions": {k: {"type": "noul", "instructions": q} for k, q in questions.items()}}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
            async with sess.post(JEV_URL, json=body, headers={"Authorization": "Bearer " + key}) as r:
                if r.status != 200:
                    log.warning("Jevが %d を返した", r.status)
                    return {}
                ans = (await r.json())["answers"]
                return {k: float(ans[k]["noul"]) for k in questions if k in ans}
    except Exception as e:
        log.warning("Jev失敗: %s", e)
        return {}
