"""コマンド（!minato）と、ステータス用チャンネルへの定期送信。"""
import logging
from datetime import datetime, timedelta

from app import __version__
from app.store import CONFIG, RATINGS_PATH, now, save_state
from engine import affinity, body, decide, names, ratings

log = logging.getLogger("minato")

STATUS_EVERY = 30   # 分。ステータス用チャンネル（config の status_channel_id）に定期的に送る


class StatusMixin:
    async def owner_command(self, m):
        cmd = m.content.split()[1] if len(m.content.split()) > 1 else "status"
        if cmd == "likes":
            await m.reply("-# " + affinity.format_summary(self.s.get("likes", {})).replace("\n", "\n-# "),
                          mention_author=False)
            return
        if cmd == "names":
            await m.reply("-# " + names.format_summary(self.s.get("names", {}), self.user.id)
                          .replace("\n", "\n-# "), mention_author=False)
            return
        if cmd == "ratings":
            await m.reply("-# " + ratings.format_summary(ratings.summary(ratings.load(RATINGS_PATH)))
                          .replace("\n", "\n-# "), mention_author=False)
            return
        if cmd in ("stop", "start"):
            self.s["stopped"] = cmd == "stop"
            save_state(self.s)
        await m.reply("-# " + self.status_line(now()), mention_author=False)

    def status_line(self, t):
        c = self.s.get("counts", {})
        st = self.s.get("starts", {})
        # 今の様子は体の変数込みで出す（予定だけで見ると、寝ているのに「ふつう」と出る）
        return (f"{__version__}: {'停止中' if self.s.get('stopped') else '稼働中'}"
                f"・{decide.activity(t, self.body)['label']}"
                f"・今日 {c.get('total', 0)}/{decide.TOTAL_DAILY_MAX} 回"
                f"・自分から {st.get('n', 0) if st.get('day') == decide.day_key(t) else 0}/{decide.STARTS_PER_DAY}"
                f"・後で返す {len(self.s.get('deferred', []))} 件"
                f"・{body.as_numbers(self.body, t)}")

    async def post_status(self, t):
        """STATUS_EVERY 分ごとに、ステータス用チャンネルへ今の様子と直近の出来事を送る。"""
        cid = CONFIG.get("status_channel_id")
        slot = t.replace(minute=t.minute - t.minute % STATUS_EVERY, second=0, microsecond=0).isoformat()
        if not cid or self.s.get("status_slot") == slot:
            return
        self.s["status_slot"] = slot
        ch = self.get_channel(int(cid))
        if ch is None:
            log.warning("ステータス用チャンネル %s が見えない", cid)
            return
        total = self.s.get("counts", {}).get("total", 0)
        sent = total - self.s.get("status_total", 0)
        self.s["status_total"] = total
        since = t - timedelta(minutes=STATUS_EVERY)
        ev = [f"{datetime.fromisoformat(e['at']):%H:%M} {e['what']}" for e in self.s.get("events", [])
              if datetime.fromisoformat(e["at"]) > since]
        text = (f"**{t:%H:%M}** {self.status_line(t)}\n"
                f"-# 直近{STATUS_EVERY}分: 返事 {sent if sent >= 0 else total} 回"
                + (f"・{'／'.join(ev)}" if ev else ""))
        await ch.send(text)
