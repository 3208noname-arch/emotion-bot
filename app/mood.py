"""居心地（気分）を進める・動かす・言葉にする。規則は engine/mood.py。"""
import json
import logging
import os
from datetime import datetime, timedelta

from app.jev import jev_many
from app.store import ROOT, now
from app import __version__
from engine import affinity, body, mechs, mood, policy, topics

log = logging.getLogger("minato")

TOPICS_PATH = os.path.join(ROOT, "topics.json")   # Web UI の「話題の好き嫌い」が書く
TOPIC_AGAIN = timedelta(minutes=30)               # 同じ話題で続けて動かさない（話題が続くたびに積み上がるため）
PEOPLE_KEEP = timedelta(hours=2)                   # 人の反応による気分の動きの記録（経験と予想の答えに使う）
SETTLE = affinity.AFTER_WAIT                       # 枠（10分）が終わってから、遅れて届く判定を待つ時間


def load_topics_file():
    try:
        return json.load(open(TOPICS_PATH, encoding="utf-8"))
    except (IOError, ValueError):
        return {"topics": [], "updated": None}


def load_topics():
    return load_topics_file().get("topics", [])


class MoodMixin:
    def used(self, key):
        """仕組みが実際に効いた（Web UI の仕組みタブの状態は、この記録から決める）。"""
        mechs.used(self.s.setdefault("mechs", {}), key, now(), __version__)

    @property
    def mood(self):
        return self.s.setdefault("mood", mood.new(now()))

    def mood_base(self, t):
        b = self.body
        return mood.baseline(body.felt_sleepy(b, t), b["hunger"], b["energy"], b["money"] < body.BROKE)

    def mood_settle(self, t):
        base = self.mood_base(t)
        mood.settle(self.mood, t, base)
        self.s["mood_hist"] = mood.record(self.s.get("mood_hist", []), t, self.mood["v"])
        return base

    def mood_words(self, t):
        return mood.as_words(self.mood, self.mood_settle(t))

    def mood_hit(self, delta, why, people=True, said_at=None):
        """気分を動かす。people は人の言動によるものか（話題・体ではない）。人の分は、経験と予想の答えに使う。
        said_at はその言動の発言時刻。**判定した時刻で残すと、10分の枠の外に出て答えがほぼ0になっていた**
        （好感度は後ろの会話を待ってから読むため。10-24）。"""
        if not delta:
            return
        t = now()
        self.mood_settle(t)
        d = mood.nudge(self.mood, delta, t, why)
        if d:
            log.info("気分: %s %+.1f → %.0f（%s）", why, d, self.mood["v"], mood.band(self.mood["v"]))
            if people:
                log_ = [x for x in self.s.get("people_log", []) if t - datetime.fromisoformat(x[0]) <= PEOPLE_KEEP]
                self.s["people_log"] = log_ + [[(said_at or t).isoformat(timespec="seconds"), round(d, 1)]]

    def people_felt(self, start, minutes):
        """start から minutes 分の間に、人の反応で気分がどれだけ動いたか。"""
        end = start + timedelta(minutes=minutes)
        return round(sum(d for at, d in self.s.get("people_log", []) if start <= datetime.fromisoformat(at) <= end), 1)

    # --- 経験で話題の図を育てる（2段目） ---
    def open_episode(self, name, t):
        eps = self.s.setdefault("episodes", [])
        if not any(e["name"] == name for e in eps):
            eps.append({"name": name, "at": t.isoformat(timespec="seconds"), "joined": False})

    def mark_spoke(self):
        """ナギが何か送った。開いている話題に「混ざった」と付ける。"""
        for e in self.s.get("episodes", []):
            e["joined"] = True

    def close_episodes(self, t):
        """話題が出てから EPISODE_MIN 分経ったものを閉じ、経験で座標を動かす。"""
        eps = self.s.get("episodes", [])
        done = [e for e in eps if t - datetime.fromisoformat(e["at"]) >= timedelta(minutes=topics.EPISODE_MIN) + SETTLE]
        if not done:
            return
        self.s["episodes"] = [e for e in eps if e not in done]
        obj = load_topics_file()
        for e in done:
            tp = next((x for x in obj["topics"] if x["name"] == e["name"]), None)
            if tp is None:
                continue
            felt = self.people_felt(datetime.fromisoformat(e["at"]), topics.EPISODE_MIN)
            dv, da = topics.experience(tp, e["joined"], felt)
            self.used("experience")
            log.info("経験: %s（%s・人の反応%+.1f）→ 快%+.2f 覚醒%+.2f = (%.2f, %.2f)", e["name"],
                     "混ざった" if e["joined"] else "素通り", felt, dv, da, tp["valence"], tp["arousal"])
        tmp = TOPICS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, TOPICS_PATH)

    # --- 混ざるかを居心地の予想で選ぶ ---
    @property
    def policy_q(self):
        return self.s.setdefault("policy", {}).setdefault("q", {})

    def choose_by_comfort(self, hit, interest, r):
        """話題の熱と快・今の気分・型ごとの予想から、混ざり方を選ぶ。(型, 確率) を返す。"""
        self.used("policy")
        if hit:
            self.used("arousal")
            heat, valence = policy.heat_of(hit["arousal"]), hit["valence"]
        else:   # 話題の図に当たらなければ、Jev の「身近な話題か」で熱を決める
            heat, valence = (1.0 if interest is not None and interest >= 0.70 else 0.0), 0.0
        self.mood_settle(now())
        lg = policy.logits(heat, valence, self.mood["v"], self.policy_q)
        return policy.pick(lg, r), {k: round(v, 2) for k, v in policy.probs(lg).items()}

    def note_joined(self, kind, t):
        """居心地の予想で混ざった。WINDOW_MIN 分後に、人の反応で予想を直す。"""
        self.s.setdefault("policy", {}).setdefault("pending", []).append({"kind": kind, "at": t.isoformat(timespec="seconds")})

    def close_policy(self, t):
        pol = self.s.setdefault("policy", {})
        pend = pol.get("pending", [])
        done = [p for p in pend if t - datetime.fromisoformat(p["at"]) >= timedelta(minutes=policy.WINDOW_MIN) + SETTLE]
        if not done:
            return
        pol["pending"] = [p for p in pend if p not in done]
        for p in done:
            felt = self.people_felt(datetime.fromisoformat(p["at"]), policy.WINDOW_MIN)
            q = policy.update(self.policy_q, p["kind"], felt)
            self.used("policy_learn")
            log.info("予想: %sで混ざった後の人の反応%+.1f → 予想%+.2f", p["kind"], felt, q)

    def recent_text(self):
        return "\n".join(m["text"] for m in list(self.recent)[-6:])

    def taste_words(self):
        return topics.as_words(topics.active(load_topics(), self.recent_text()))

    def taste(self, name, like):
        """返事の中で口にした好き嫌いを、話題の図に覚える（人が置いた話題は書き換えない）。"""
        obj = load_topics_file()
        done = topics.learn(obj["topics"], name, like, now().isoformat(timespec="seconds"))
        if done in ("added", "moved"):
            self.used("learned")
            tmp = TOPICS_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.replace(tmp, TOPICS_PATH)
        log.info("好き嫌いを口にした: %s %+.1f → %s", name, like, done)

    async def read_topic(self, convo):
        """会話の最後の発言がどの話題かをJevで読み、好き嫌い（快）で気分を動かす。当たった話題を返す（無ければ None）。"""
        tps = topics.active(load_topics(), convo)
        if not tps:
            return None
        # 「害虫は害虫としかつるめない」（人のたとえ）で「虫の話」と読んでいた（10-12）
        qs = {tp["name"]: f"会話の最後の発言は「{tp['name']}」そのものについての話か"
                          f"（人や物事を{tp['name']}にたとえる比喩は含めない）。" for tp in tps}
        hit = mood.topic_hit(await jev_many(convo, qs), tps)
        if not hit:
            return None
        name, d = hit
        tp = next(x for x in tps if x["name"] == name)
        t = now()
        self.open_episode(name, t)
        why = f"{name}の話になった"
        if not any(e["why"] == why and t - datetime.fromisoformat(e["at"]) < TOPIC_AGAIN for e in self.mood["events"]):
            self.mood_hit(d, why, people=False)
            self.used("topic_valence")
        return tp
