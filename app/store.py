"""設定・状態ファイルの置き場と、時刻。"""
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # config.json などはリポジトリ直下
JST = timezone(timedelta(hours=9))
CONFIG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
STATE_PATH = os.path.join(ROOT, "state.json")
RATINGS_PATH = os.path.join(ROOT, "ratings.jsonl")


def now():
    return datetime.now(JST).replace(tzinfo=None)


def load_state():
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except (IOError, ValueError):
        return {"counts": {}, "stopped": False}


def save_state(s):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)
