"""評価: メッセージの右クリックメニュー「評価」。本人にしか見えない。"""
import logging

import discord

from app.store import RATINGS_PATH, now
from engine import ratings

log = logging.getLogger("minato")


class CommentModal(discord.ui.Modal, title="ひと言（空欄でもOK）"):
    comment = discord.ui.TextInput(label="どこが気になったか", style=discord.TextStyle.paragraph,
                                   required=False, max_length=300)

    def __init__(self, rec):
        super().__init__()
        self.rec = rec
        self.comment.placeholder = ratings.HINTS.get(rec["label"])

    async def on_submit(self, interaction):
        self.rec["comment"] = self.comment.value.strip()
        ratings.append(RATINGS_PATH, self.rec)
        log.info("評価: %s「%s」%s", self.rec["label"], self.rec["text"][:40],
                 f"— {self.rec['comment']}" if self.rec["comment"] else "")
        await interaction.response.send_message(f"「{self.rec['label']}」で記録した。ありがとう", ephemeral=True)


class RateView(discord.ui.View):
    def __init__(self, rec):
        super().__init__(timeout=300)
        for label in ratings.LABELS:
            b = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            b.callback = self.make_cb(label)
            self.add_item(b)
        self.rec = rec

    def make_cb(self, label):
        async def cb(interaction):
            await interaction.response.send_modal(CommentModal({**self.rec, "label": label}))
        return cb


class RatingMixin:
    async def rate(self, interaction, message: discord.Message):
        if self.ch is None or message.channel.id != self.ch.id:
            await interaction.response.send_message(f"評価は #{self.ch} の発言だけ", ephemeral=True)
            return
        before = [m for m in self.recent if m["id"] < message.id][-6:]
        rec = {"at": now().isoformat(timespec="seconds"), "rater_id": interaction.user.id,
               "message_id": message.id, "author": message.author.display_name,
               "is_me": message.author == self.user, "text": self.describe(message),
               "context": [f"{m['author']}: {m['text']}" for m in before]}
        if rec["is_me"]:
            rec["state"] = self.recall(message.id)   # 喋った時の内部の値（評価と突き合わせる）
        await interaction.response.send_message(
            f"> {rec['text'][:80]}\nこの発言はどうだった？（あなたにしか見えません）",
            view=RateView(rec), ephemeral=True)
