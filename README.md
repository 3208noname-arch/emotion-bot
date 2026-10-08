# emotion-bot

Discordの雑談チャンネルで、人格を持ったBotを友人たちと話させるための実験です。

感情や返事の判断をLLMに任せず、コードの規則で決めます。
LLMは、決まった型と体の状態に沿って文面を書くだけです。
このBotは自分がAIであることを隠しません（Discordでは「アプリ」タグが付きます）。

## 何をしているか

- **全員には返事をしない。**発言を数秒溜めてから1回だけ判断し、無反応／リアクションだけ／一言／通常の返事から規則で選ぶ（`decide.py`）
- **自分の発言の割合を数える。**参加人数分の1を超えたら、話しかけられない限り黙る
- **体の変数を持つ。**眠気・空腹・気力・残高が少しずつ動き、寝る・食べる・会話から抜けるをBot自身が選ぶ（`body.py`）
- **予定がある。**講義中は遅れて一言だけ、バイト中と寝ている間は返さず、終わってから「ごめん今見た」で返す
- **いつも即答しない。**気付くまでの時間と、端末（スマホ／PC）ごとの打つ速さを入れている
- **評価を集める。**メッセージの右クリックメニュー「評価」で、本人にだけ見える形で「自然／流れとズレてる／キャラじゃない／バグ」を付けられる（`ratings.py`）

体の変数の考え方は、Man & Damasio (2019) "Homeostasis and soft robotics in the design of feeling machines"（*Nature Machine Intelligence*）を参考にしています。
感情を、体を生きられる範囲に保つための内部状態として扱う立場です。

## 使う外部サービス

| サービス | 用途 |
|---|---|
| Discord | Botアカウント（Message Content Intent を有効にする） |
| [TypeSafe](https://typesafe.ai)（Jev） | 会話が人格の食いつく話題かを、確率で判定する |
| [OpenRouter](https://openrouter.ai) | 文面を書く（Qwen） |

会話の内容はTypeSafeとOpenRouterに送られます。参加者には伝えてから使ってください。

## 動かし方

```bash
pip install -r requirements.txt
cp config.example.json config.json   # 値を埋める
cp persona.example.py persona.py     # 人格を書く
python bot.py
```

テストは次のとおりです（外部サービスには繋がりません）。

```bash
python body_test.py && python decide_test.py && python ratings_test.py
```

## 安全装置

- 1人あたり1日30回、全体で1日150回まで返事をする（`decide.py`）
- `config.json` の `owner_id` の人だけが、チャンネルで `!minato stop` ／ `start` ／ `status` ／ `ratings` を使える
- 敬語や「人間だ」という返事は、はじいて書き直させる（AIかと聞かれた時に人間だと偽らない）

## ファイル

| ファイル | 役割 |
|---|---|
| `bot.py` | Discord・Jev・OpenRouterの配線 |
| `decide.py` | 返すかどうか・型・予定・上限（純関数） |
| `body.py` | 体の変数と相互作用（純関数） |
| `ratings.py` | 評価の記録と集計（純関数） |
| `persona.example.py` | 人格の見本 |
| `*_test.py` | 純関数のテスト |
