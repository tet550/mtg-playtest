# secrets/

API キーなど、リポジトリに入れないものの置き場所。このフォルダの中身は README.md 以外 git の管理外（`.gitignore`）。

| ファイル | 中身 | 使う所 |
|---|---|---|
| `openai_api_key` | OpenAI の API キーを1行で（`sk-...`）。拡張子なし | `python -m mtgtable auto`（`mtgtable/llm.py`） |
| `openai.json` | モデルなどの設定（省略可）。下の例 | 同上 |

```json
{"model": "gpt-5", "judge_model": "gpt-5", "player_model": "gpt-5-mini", "reasoning_effort": "medium"}
```

- `model`: 全部の役割の既定。`judge_model` / `player_model` で審判・AI の席だけ別のモデルにできる（省略で `model`）
- `reasoning_effort`: 推論モデルの深さ（`low` / `medium` / `high`）。省略で送らない
- 優先順位: `auto --model` ＝ 環境変数 `MTGTABLE_OPENAI_MODEL` → このファイル（役割別 → `model`）→ 既定 `gpt-5`
- 上の例のモデル名は書き方の例。使えるモデルはアカウントによる

- 環境変数 `OPENAI_API_KEY` があれば、そちらを優先する
- キーを他の人に渡さない。チャット・Issue・ログに貼らない。漏れたら OpenAI のダッシュボードで無効にして作り直す

