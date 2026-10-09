# secrets/

API キーなど、リポジトリに入れないものの置き場所。このフォルダの中身は README.md 以外 git の管理外（`.gitignore`）。

| ファイル | 中身 | 使う所 |
|---|---|---|
| `openai_api_key` | OpenAI の API キーを1行で（`sk-...`）。拡張子なし | 提供元 openai（`auto`・`serve --ai` / `--site`。`mtgtable/llm.py`） |
| `anthropic_api_key` | Anthropic の API キーを1行で。拡張子なし（省略可） | 提供元 anthropic |
| `llm.json` | 提供元・モデルなどの設定（省略可）。下の例。無ければ前の名前の `openai.json` を読む | 同上 |

```json
{"provider": "anthropic", "player_provider": "openai", "judge_model": "claude-opus-5-5", "player_model": "gpt-5-mini",
 "effort": "medium"}
```

- `provider`: 全部の役割の既定の提供元（`openai` / `anthropic`。省略で `openai`）。`judge_provider` / `player_provider` で
  審判・AI の席だけ別の提供元にできる
- `model`: 全部の役割の既定のモデル。`judge_model` / `player_model` で役割ごとに（省略で `model` → 提供元の既定。
  openai は `gpt-5`、anthropic は `claude-opus-5-5`）
- `effort`: 考える深さ（`low` / `medium` / `high` / `xhigh` / `max`）。anthropic は `output_config.effort`、openai は
  `reasoning_effort`（`xhigh` / `max` は `high`）。省略で送らない（前の名前 `reasoning_effort` も読む）
- 優先順位: `auto --provider` ＝ 環境変数 `MTGTABLE_LLM_PROVIDER`、`auto --model` ＝ `MTGTABLE_MODEL`（前の名前
  `MTGTABLE_OPENAI_MODEL` も）→ このファイル（役割別 → 共通）→ 既定
- 上の例のモデル名は書き方の例。使えるモデルはアカウントによる
- anthropic を使うには公式の SDK が要る（`pip install anthropic`）。使わなければ標準ライブラリだけで動く。断られた
  （`refusal`）ときはサーバー側の代わりのモデル（`fallbacks: "default"`）で続ける。キャッシュは固定部分に TTL 1時間

- 環境変数 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` があれば、そちらを優先する。anthropic はどちらも無ければ、SDK が
  `ANTHROPIC_AUTH_TOKEN` や `ant auth login` のプロファイルを探す
- キーを他の人に渡さない。チャット・Issue・ログに貼らない。漏れたら各社のダッシュボードで無効にして作り直す
