# -*- coding: utf-8 -*-
"""感情エンジンの複数人版。Discordの雑談チャンネル1つで、人格（persona.py）を持ったBotとして話す。

- 返すかどうか・型は engine/ の規則が決める。Jev は読み取り、LLM は文面書きだけ
- 発言は数秒溜めてから1回だけ判断する（全員に返信しない）
- **安全装置**: 1人あたり／全体の1日上限、オーナーの停止コマンド（!minato stop / start）
- Discord 側は app/ に機能ごとに分けてある（client・llm・jev・learning・life・rating・status・store）
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from app.client import Minato  # noqa: E402（ログの設定を先に済ませる）
from app.store import CONFIG  # noqa: E402

if __name__ == "__main__":
    Minato().run(CONFIG["token"], log_handler=None)
