"""更新内容を Discord の Webhook に流す。本文は標準入力から受け取る。

使い方（CT104）: python3 deploy/changelog.py < 本文.txt
Webhook の URL は config.json の changelog_webhook（GitHub に入れない。URL を知っていれば誰でも投稿できるため）。
**流す前に、本文をユーザーに確認してもらう**（2026-10-17 ユーザー「何を載せるかは適宜確認してから」）。
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 2000   # Discord の1メッセージの上限


def chunks(text):
    """2000字を超えたら、行の切れ目で分ける。"""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > LIMIT:
            out.append(cur)
            cur = ""
        cur += line
    return out + [cur] if cur.strip() else out


def main():
    url = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8")).get("changelog_webhook")
    if not url:
        sys.exit("config.json に changelog_webhook が無い")
    text = sys.stdin.read().strip()
    if not text:
        sys.exit("本文が空")
    for part in chunks(text):
        body = json.dumps({"content": part, "allowed_mentions": {"parse": []}}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                              "User-Agent": "minato-changelog"})
        urllib.request.urlopen(req, timeout=15)
    print("posted", len(text), "chars")


if __name__ == "__main__":
    main()
