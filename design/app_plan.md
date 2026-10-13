# アプリ化（手元で動く配布版）の計画

mtgtable を「インストールして起動すると、ブラウザで卓が開く」アプリとして配る。公開のサーバー（`serve --site`）を
運営者が持つ代わりに、各自の手元で同じ画面を動かし、AI の料金は各自のキー（BYOK）か Claude Code で払う。

> 2026-10-13: 配布に向けて機能を絞った。起動は手元のサイト（`serve`。DB は既定で `data/site.sqlite`）だけにし、
> 観戦だけのビューア・`serve --play`（CLI で `invite` した対局）・`--site` の旗を外した。審判と AI の席は Claude Code だけが回す
> （LLM の API で回す `auto`・`serve --ai`・`llm.py`・`worker.py`・`secrets/` は外した。段階 4 で作り直す）。静的書き出し（`export`・`docs/`）は
> 外し、対局の公開は再設計する。CLI からは `replay`・`diff`・`fork`・`policy`・`ops`・`--direct`・`db-backup`・`db-import`・`invite`・
> `revoke`・`wait` を外した。

## 前提（決定済み）

- **手元で動くことを主にする**。AI の料金は各自が提供元に直接払う（運営者は受け取らない。Fan Content Policy の範囲）。
- **人間同士の対戦はホストとクライアントの形**。片方が手元で立て、招待 URL を相手に渡す（相手はブラウザだけ）。
- **最初の配布は uv**（`uvx mtgtable`）。Python のインストールも署名も要らない。反応を見てから PyInstaller でアプリにする。
- **リポジトリから動かす今の使い方（Claude Code の `mtg-playtest` スキル・CLI・テスト）はそのまま動かす**。

## 今の状態（調べたこと）

### 1. 置き場所がリポジトリに決め打ち

パッケージ（`mtgtable/`）の一つ上、つまりリポジトリの直下を前提にしている。uv や PyInstaller で入れると
パッケージの外は存在しないか、書き込めない。

| 場所 | 中身 | 種類 | 備考 |
|---|---|---|---|
| `carddb.py:38` `REPO_ROOT / "cards"` | オラクル・画像・記号・日本語名のキャッシュ | 書く | `MTG_CARDS_DIR` で変えられる |
| `llm.py:59` `SECRETS` | API キー・`llm.json` | 書く（秘密） | — |
| `prompt.py:41` `REFERENCES` | `.claude/skills/mtg-playtest/references/cli.md` | 読むだけ | **パッケージの外のスキルを読んでいる**。審判のプロンプトの固定部分 |
| `prompt.py:42` `STRATEGY` | `decklists/strategy/*.md` | 読むだけ | デッキのプレイ方針 |
| `lobby.py:28` `REPO_DECKS` | `decklists/*.txt` | 読むだけ | AI の相手に選べるデッキ |
| `cli.py:692,698` `--root playtest` | 対局フォルダ | 書く | 今いるフォルダからの相対パス |
| `--db` / `MTGTABLE_DB` | サイトの DB | 書く | 既定は無し |
| `prompt.py:39` `prompts/`・`web.py:43` `web/` | プロンプト・画面 | 読むだけ | パッケージの中なので、同梱の指定さえあれば動く |

### 2. GUI で対局を作れるのは `--site` だけ

ロビー（対局を作る・招待）・デッキ登録・履歴は `--site`（DB が要る）でしか動かない（`web.py:114-118`）。
`serve --play` は、CLI の `new` で作った対局を開くだけ。手元で `--site` を立てる使い方は、開発用に
`.claude/launch.json` の `site` で既にしている（`data/site-local.sqlite`）。

→ **アプリは「手元で `--site` を立てる」形にする**。ロビーの招待がそのままホストとクライアントの対戦になる。

### 3. `--site` は公開を前提にしている

- 運営者・連絡先（`MTGTABLE_OPERATOR` / `MTGTABLE_CONTACT`）と利用規約・プライバシーの画面（`web/legal.js`）
- レート制限（`ratelimit.py`）と AI の利用の上限（`worker.Limits`。**トークン数で、金額ではない**）
- API キーは運営者の環境変数か `secrets/` から読む。**画面からキーを入れる手段が無い**

### 4. パッケージの定義が無い

`pyproject.toml` が無い。入口は `mtgtable/cli.py:787 main()`。必要な外部ライブラリは `anthropic`
（提供元に anthropic を使うときだけ）。

## 段階 1: 置き場所を分ける（済み）

**置き場所は `mtgtable/paths.py` だけで決める。書くものはユーザーのデータ用フォルダへ。**

- `data_home()`: 環境変数 `MTGTABLE_HOME` → **リポジトリから動いている時はリポジトリ直下**（今と同じ）→ OS のデータ用フォルダ
  （macOS `~/Library/Application Support/mtgtable`、Windows `%APPDATA%\mtgtable`、Linux `$XDG_DATA_HOME/mtgtable`）
  - 「リポジトリから動いている」は、パッケージの一つ上に `.git` か `pyproject.toml` があること（clone・worktree・`pip install -e`）
- 書くもの: `cards_dir()`（`MTG_CARDS_DIR` が優先）・`secrets_dir()`・`games_dir()`（`serve` / `export` の `--root` の既定）
- 読むだけのもの: `decklists_dir()`・`references_dir()`。**ファイルは今の場所から移さない**。移すと README・スキルの
  `--deck p1=decklists/piza.txt` やリンクが全部壊れるため。リポジトリから動く時は今の場所を、パッケージとして入れた時は
  `mtgtable/assets/` を読む。**`assets/` へはビルドで写す**（段階 2。正本は今の場所の1つだけ）
- `carddb.py`・`llm.py`・`prompt.py`・`lobby.py`・`cli.py` の決め打ちを `paths` に置き換えた
- `llm.py` のキーと設定の場所は、読み込んだ時に決まる（テストがモジュールの定数を差し替える形を保つため）。
  `MTGTABLE_HOME` は起動前に設定する
- 回したもの: `db_path()` は段階 3（`mtgtable app` で初めて使う）、`secrets/` の権限 `0600` は段階 4（画面からキーを書く時）

### 確認（済み）

- テストはファイル版・SQLite 版とも全件通過（236件。`tests/test_paths.py` を追加）。リポジトリから動く時の置き場所は変わらない
- パッケージだけをリポジトリの外に写し（`assets/` も手で写して）、HOME を空のフォルダにして動かした:
  `new` でカードのキャッシュが `~/Library/Application Support/mtgtable/cards/` に入る。同梱のデッキが選べる。
  審判と席のプロンプト（リファレンス・プレイ方針）が組める

## 段階 2: パッケージにする（済み）

- `pyproject.toml`（ビルドは hatchling）
  - `name = "mtgtable"`（PyPI で空いていた。2026-10-10 時点。**公開はまだしていない**）
  - `requires-python = ">=3.10"`、依存は無し。`[project.optional-dependencies] anthropic = ["anthropic"]`
    （既定は標準ライブラリだけのまま。既定の提供元 openai は urllib で呼ぶので、anthropic を使う人だけが足す）
  - `[project.scripts] mtgtable = "mtgtable.cli:main"`。版は `mtgtable/__init__.py` の `__version__`（`--version` でも出る）
- **ビルドで `assets/` に写す**（hatch の force-include）: `decklists/` → `mtgtable/assets/decklists/`、
  `.claude/skills/mtg-playtest/references/` → `mtgtable/assets/references/`。リポジトリには `assets/` を置かない
- sdist の include は先頭に `/` を付けてリポジトリ直下に限る（無いと gitignore 式に全階層で当たり、
  `deploy/mtgtable.service` や `secrets/README.md` まで入った）
- 同梱のデッキを名前で選べるようにした（`--deck p1=piza`。`setup.deck_path`。ファイルがあればファイルが優先、
  見つからなければ同梱のデッキの名前を添えて終わる）
- `USER_AGENT`（Scryfall）に版番号を入れた

### 確認（済み）

- `uv build` で sdist と wheel ができる。wheel に `assets/`（デッキ 13・リファレンス 3）・`web/`・`prompts/` が入り、
  `cards/`・`secrets/`・`playtest/`・`__pycache__` は入らない。sdist は `mtgtable/`・`decklists/`・リファレンス・`tests/`・文書だけ
- 空の仮想環境に wheel を入れ、HOME を空にしてリポジトリの外で: `--version`・`new g1 --deck p1=piza --deck p2=jund-sacrifice`
  （キャッシュは `~/Library/Application Support/mtgtable/cards/`）・知らないデッキ名の案内・審判のプロンプト（`cli.md` 入り）・
  `serve`（`/`・`/static/*`・`/api/games`）
- `uvx --from <wheel> mtgtable --version`、`"mtgtable[anthropic] @ <wheel>"` で SDK（1.13.0）が入り読み込める
- テスト: Python 3.11 でファイル版・SQLite 版とも全件通過。Python 3.10（uv の管理する Python）＋ anthropic SDK で
  239 件すべて通過（いつもスキップされる anthropic のテストも含む）

### 残り（段階 3 以降か、公開の判断と一緒に）

- PyPI への公開（`uv publish`）。公開すれば `uvx mtgtable` の1行になる
- GitHub Actions でテスト（3.10〜最新、ファイル版・SQLite 版）とビルドを回す

## 段階 3: 起動コマンド（`mtgtable app`）

> 2026-10-12: 下の「手元の起動（local）という区別」は、`--site` そのものを手元の1人用に作り変えて済ませた
> （[site_plan.md](site_plan.md) の冒頭）。所有者の鍵は使わず、ホストは手元のブラウザ、相手は席の鍵で区別する。

ダブルクリックや1行で、手元のサイトが開くようにする。

- `mtgtable app`（引数なしの `mtgtable` もこれにするかは、CLI の使い勝手と相談）
  - データ用フォルダと DB（`db_path()`）を用意する
  - 空いているポートを選び、`127.0.0.1` で `--site --ai` 相当を立てる
  - 起動したらブラウザで開く（`webbrowser`）。ターミナルには URL と止め方を出す
  - **二重起動の防止**: データ用フォルダにロック（ポートを書いたファイル）を置き、既に動いていればそのページを開くだけ
- **手元の起動（local）という区別を足す**。`--site` のうち公開のためだけのものを外す:
  - 運営者・連絡先の注意と、利用規約・プライバシーの画面を出さない（代わりに「手元で動いている」「データの置き場所」を出す）
  - レート制限は緩める（手元の1人のためのもの）
  - 所有者の鍵（Cookie）は今のまま使う（ホストとクライアントの対戦で、相手と自分を分けるのに要る）
- AI の上限を**金額（ドル）で持つ**。自分のお金なので、1局と月の上限を設定でき、超えたら止める
  - usage と単価の表から金額を出して記録する（`worker.Budget` を金額でも数える）
  - 対局を作る前に「この対局は約 $X」の見込みを出す

### 確認

- データ用フォルダが空の状態から `mtgtable app` → ブラウザが開く → デッキを登録 → AI と対局 → 決着まで
- 二つ目の `mtgtable app` が、新しいサーバーを立てずに同じページを開く
- 利用規約の画面と運営者の注意が出ない

## 段階 4: 画面から AI の設定をする

今はキーを `secrets/` に手で書くか環境変数で渡すしかない。

- 設定の画面: 提供元（openai / anthropic）・モデル（役割ごとに審判と AI の席）・effort・キー
- `POST /api/settings/llm`（キーを書く）と `GET /api/settings/llm`（キーは**末尾4文字だけ**返す）
  - **手元（`127.0.0.1` から、同じ Origin）からだけ受ける**。ホストとして外に出している時、相手からは使えない
  - キーは `secrets_dir()` に書く（段階 1 と同じ形式。CLI・スキルからも同じキーが使える）
  - `secrets/` は作る時に権限 `0700`、キーのファイルは `0600`（Windows はユーザーのフォルダの既定の権限に任せる）
- 「試す」ボタン: 小さな呼び出しを1回して、キーとモデルが使えるか確かめる
- キーが無い時は、AI との対局を作る画面で設定へ案内する（人間同士の対局は、審判のぶんのキーが要ることを出す）
- 先の候補: OpenRouter の OAuth（PKCE）。キーを手で扱わずに済む。OpenAI 互換の API なので、提供元を1つ足す形になる見込み
  （実装前に最新の手順と規約を一次情報で確かめる）

### 確認

- キーを入れる → 試す → AI と対局できる。画面・ログ・API の返答のどこにもキーの全体が出ない
- 外のアドレスから `/api/settings/llm` を叩くと拒否される

## 段階 5: ホストとクライアントの対戦

ロビーの招待（`lobby.py`）は手元のサイトでもそのまま動く。足りないのは「相手から届くこと」。

- `mtgtable app --share`（仮）: 待ち受けを外に出し、招待 URL の基準を外から届くアドレスにする
  - 同じ LAN: `0.0.0.0` で待ち受け、LAN の IP で招待 URL を作る
  - インターネット越し: Tailscale か Cloudflare Tunnel / ngrok を案内する。入っていればトンネルを張るところまで自動にする
  - 外に出している間は、画面にはっきり出す（いつでも止められるように）
- 外から届く要求には、`--site` の今の守り（所有者の鍵・Origin・レート制限）を効かせる。段階 3 で緩めたものは、外向きには戻す
- 審判の AI の料金はホスト持ち（画面で明示する）

### 確認

- 2台（か、同じ機械の別のブラウザのプロファイル）で、招待 → 対局 → 決着まで
- 相手から設定の画面・自分の履歴・相手の手札が見えない

## 段階 6: アプリとして配る（反応を見てから）

- PyInstaller で macOS（`.app`）・Windows（`.exe`）・Linux の実行ファイルを作る。`assets/`・`web/`・`prompts/` を同梱
- 必要なら pywebview でアプリのウィンドウに出す（ブラウザを開かない）
- GitHub Actions で3つの OS をビルドして、リリースに添付する
- 署名は費用と相談して決める（macOS の公証は Apple Developer 年 $99、Windows は Azure Trusted Signing など）。
  署名しない間は、初回に開く手順を README に書く
- PyInstaller の1ファイル形式はウイルス対策ソフトに誤検知されやすいので、フォルダ形式（onedir）を既定にする

## 初版の後に回すもの

- Pyodide でブラウザだけで動かす（サーバーもインストールも不要になるが、作り直しが大きい）
- 自動更新（初版は「新しい版があります」と出すだけ）
- 公開のサーバー（`serve --site`）の縮小・お試し用のデモ化

## 進める順番

1. 段階 1（置き場所を分ける）。**ほかのすべての前提**。今の使い方を壊さないことをテストで確かめる
2. 段階 2（パッケージにする）。ここで `uvx` で配れる最小の形になる
3. 段階 3（`mtgtable app`）と段階 4（画面から AI の設定）。ここまでで、ターミナルに慣れない人も1人で AI と遊べる
4. 段階 5（ホストとクライアント）
5. 段階 6（アプリとして配る）は、2〜4 を配ってみた反応を見てから
