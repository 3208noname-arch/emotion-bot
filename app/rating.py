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

    async def on_submit(self, interaction):
        self.rec["comment"] = self.comment.value.strip()
        ratings.append(RATINGS_PATH, self.rec)
        name = ratings.label_text(self.rec)
        log.info("評価: %s「%s」%s", name, self.rec["text"][:40],
                 f"— {self.rec['comment']}" if self.rec["comment"] else "")
        await interaction.response.send_message(f"「{name}」で記録した。ありがとう", ephemeral=True)


class ChoiceView(discord.ui.View):
    """ボタンを並べ、押されたら on_pick(interaction, 押された名前) を呼ぶ。"""

    def __init__(self, names, on_pick):
        super().__init__(timeout=300)
        for name in names:
            b = discord.ui.Button(label=name, style=discord.ButtonStyle.secondary)
            b.callback = self._cb(name, on_pick)
            self.add_item(b)

    @staticmethod
    def _cb(name, on_pick):
        async def cb(interaction):
            await on_pick(interaction, name)
        return cb


def rate_view(rec):
    """1段目: 評価を選ぶ。「感情がズレてる」のように詳しく分けるものは、2段目で中身を選んでからコメント欄を開く。"""
    async def pick_label(interaction, label):
        r = {**rec, "label": label}
        details = ratings.DETAILS.get(label)
        if not details:
            await interaction.response.send_modal(CommentModal(r))
            return

        async def pick_detail(interaction2, detail):
            await interaction2.response.send_modal(CommentModal({**r, "detail": detail}))
        await interaction.response.edit_message(content=f"> {rec['text'][:80]}\n「{label}」の中身はどれ？\n"
                                                        f"-# {ratings.DETAIL_HELP.get(label, '')}",
                                                view=ChoiceView(details, pick_detail))
    return ChoiceView(ratings.LABELS, pick_label)


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
            view=rate_view(rec), ephemeral=True)
