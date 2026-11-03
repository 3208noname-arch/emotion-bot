"""更新内容を Discord の Webhook に流す。本文は標準入力から受け取る。

使い方（CT104）: python3 deploy/changelog.py < 本文.txt 。Web UI の「記録 → お知らせ」からも同じ post() で流す
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


def webhook_url():
    return json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8")).get("changelog_webhook")


def post(text, url=None):
    """本文を流す。分けて送った数を返す。"""
    url = url or webhook_url()
    if not url:
        raise RuntimeError("config.json に changelog_webhook が無い")
    text = text.strip()
    if not text:
        raise RuntimeError("本文が空")
    parts = chunks(text)
    for part in parts:
        body = json.dumps({"content": part, "allowed_mentions": {"parse": []}}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json",
                                                              "User-Agent": "minato-changelog"})
        urllib.request.urlopen(req, timeout=15)
    return len(parts)


def main():
    text = sys.stdin.read()
    try:
        post(text)
    except RuntimeError as e:
        sys.exit(str(e))
    print("posted", len(text.strip()), "chars")


if __name__ == "__main__":
    main()
