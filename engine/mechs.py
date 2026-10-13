# -*- coding: utf-8 -*-
"""仕組みが実際に動いた記録と、Web UI の仕組みタブの状態（純関数）。

**状態は手で書かない。**Bot が仕組みを実際に効かせた時に used() で記録し、状態はその記録から決める
（10-13 ユーザー「実際の値を見て反映してほしい」。手書きだと説明が古いまま残る事故が一度あった）。
説明文（does）だけは人が書く。仕組みを変えたら、ここも一緒に直す。
effect=False は「数えるだけ」（記録しているが、会話や行動には効かせていない）。
"""
from datetime import datetime, timedelta

RECENT = timedelta(hours=24)

CATALOG = [
    {"key": "body", "name": "体（眠気・空腹・気力・残高）", "effect": True,
     "does": "寝る・起きる・食べる・抜けるを自分で選ぶ。体の感じを言葉でLLMに渡す"},
    {"key": "today", "name": "曜日と今日・明日の予定", "effect": True,
     "does": "LLMに日付・曜日・授業とバイトの予定を渡す（土日に1限の話をしない）"},
    {"key": "did", "name": "会話で言った行動", "effect": True,
     "does": "「今やった」と言ったことを出来事に残し、残高から引く"},
    {"key": "mood", "name": "居心地（気分）", "effect": True,
     "does": "体・人・話題で動き、1時間で半分ずつ基準へ戻る。LLMに言葉で渡し、口調ににじませる"},
    {"key": "topic_valence", "name": "話題の快（好き嫌い）", "effect": True,
     "does": "話題が会話に出ると、快の向きに気分を動かす"},
    {"key": "learned", "name": "口にした好き嫌いを覚える", "effect": True,
     "does": "会話で言った好き嫌いを話題の図に足す（白抜きの点）。消さずに眠らせ、話題が出たら使う"},
    {"key": "likes", "name": "好感度", "effect": True,
     "does": "けなし・庇い・冗談・見下しを前後6件の会話で判定して数え、気分も動かす。**態度・口調にはまだ効かない**"},
    {"key": "names", "name": "呼び名", "effect": False,
     "does": "誰が誰をどう呼ぶかを集める。会話にはまだ使わない"},
    {"key": "policy", "name": "混ざり方を居心地の予想で選ぶ", "effect": True,
     "does": "話しかけられていない時の 黙る・リアクション・一言・普通 を、話題の熱（覚醒）と快・気分・予想から選ぶ。"
             "**話しかけられた時・話題を変える・絡みに行く・抜けるは、まだ決まりごと**"},
    {"key": "arousal", "name": "話題の覚醒（乗りやすさ）", "effect": True,
     "does": "話題の図に当たった時、覚醒0.4〜0.8で混ざる熱が0→1。**反応の速さにはまだ効かない**"},
    {"key": "policy_learn", "name": "予想を反応で直す", "effect": True,
     "does": "混ざった後10分の人の反応による気分の動きへ、その型の予想を1割寄せる"},
    {"key": "experience", "name": "経験で話題の図を育てる", "effect": True,
     "does": "話題が出てから10分で、混ざったら覚醒+0.03・素通りなら−0.01、人の反応で快±0.03。人が置いた点は±0.2まで"},
]

# まだコードに無いもの（設計だけ）。作ったら CATALOG へ移し、used() を呼ぶ
PLANNED = [
    {"name": "嫌いな話題は1回目は断る", "does": "嫌いな話題を振られたらまず断る。続けられたら好感度を下げる"},
    {"name": "好感度を「居心地の予想」にする", "does": "その人といると居心地がどうなるかの予想にし、予想より良ければ上げる。親しいほど下がりにくい"},
    {"name": "話しかけられた時・抜ける・絡みに行くも居心地で選ぶ", "does": "今の「混ざり方」の選び方を、ほかの行動にも広げる"},
]


def used(reg, key, t, version):
    """仕組みが実際に効いた。回数・最後の時刻・初めて動いた時刻と版を残す。"""
    r = reg.setdefault(key, {"n": 0, "first": t.isoformat(timespec="seconds"), "first_ver": version})
    r["n"] += 1
    r["last"] = t.isoformat(timespec="seconds")
    return r


def status(item, rec, t):
    """記録から状態を決める。on（使用中）／count（数えるだけ）／idle（しばらく動いていない）／never（まだ動いていない）。"""
    if not rec or not rec.get("n"):
        return "never"
    if t - datetime.fromisoformat(rec["last"]) > RECENT:
        return "idle"
    return "on" if item["effect"] else "count"
