# mtgtable — AI が紙の MTG をプレイするためのデジタル卓

設計は [design/basic_design.md](design/basic_design.md)。これはその基本実装（GUI は観戦ビューアと、人間が1席を持つ対局まで）。

Rules Engine ではなく **Table Engine**。カード・カウンター・メモ・配置・宣言といった
紙の卓上の道具と、それを動かす操作だけを提供する。呪文を唱えられるか、対象が適正か、
誘発・置換・継続的効果・状況起因処理・戦闘ダメージなどの判断はすべて AI が行う。

Python 3.10 以上、標準ライブラリのみ。

## 構成

| モジュール | 役割 | 設計書 |
|---|---|---|
| `mtgtable/model.py` | Player / Card / Zone / ManaPool / Mana / Counter / Note / Link / Stack / Combat / TurnState / Declaration / GameState | 5〜15, 30節 |
| `mtgtable/info.py` | 公開範囲・KnownInformation・InformationPolicy・PlayerView | 16〜18節 |
| `mtgtable/operations.py` | Operation（卓上の基本操作）とカード参照 | 19節 |
| `mtgtable/engine.py` | Act・Batch・Precondition | 20〜23節 |
| `mtgtable/procedures.py` | 手順（複数の Act の並びの省略: `turn_start` `turn_end`） | 20節 |
| `mtgtable/store.py` | Operation Log・Undo/Redo・Replay・Snapshot(fork)・State Diff。保存先は対局フォルダ（既定） | 29節 |
| `mtgtable/sqlstore.py` | 同じ保存の SQLite 版（`--db`。サーバー用） | 29節 |
| `mtgtable/setup.py` | デッキリスト読み込みと初期状態 | — |
| `mtgtable/carddb.py` | Rule Reference（Scryfall のオラクルをローカルにキャッシュ） | 28節 |
| `mtgtable/render.py` | PlayerView のテキスト表示 | 24節（表示とモデルの分離） |
| `mtgtable/cli.py` | コマンドライン | 26節 |
| `mtgtable/web.py`・`mtgtable/web/` | 観戦ビューア（`serve`）と GUI の対局（`serve --play`。人間が席を持って操作） | 24〜26節 |
| `mtgtable/play.py` | GUI の対局の部品（席の鍵・待たれている Player・`wait`） | 26節 |
| `mtgtable/owners.py` | 公開のサーバー（`serve --site`）の所有者の鍵・復元・席を持つ人 | — |
| `mtgtable/decks.py` | 公開のサーバーのデッキ登録（検査と、所有者ごとの保存） | — |
| `mtgtable/lobby.py` | 公開のサーバーで対局を作る（AI と・招待した人と）・時間切れ | — |
| `mtgtable/prompt.py`・`mtgtable/prompts/` | AI のプロンプト（Player の意図・審判・直接 Batch）の書き出しと、返答の適用 | 27節 |
| `mtgtable/llm.py` | LLM の API（OpenAI・Anthropic）で審判と AI の席を回す（`auto`）。キーは `secrets/` | 27節 |
| `mtgtable/worker.py` | サーバーの中で審判と AI の席を回す（`serve --ai` / `--site`）・利用の上限・中断と再開 | 27節 |

Web の責務分割と設計判断は [design/web_design.md](design/web_design.md)。ビルド不要の ES modules を使用する。

## 使い方

### Windows / PowerShell の Python 起動

最初に `Get-Command python, py -ErrorAction SilentlyContinue` で実行環境を確認する。
`python` が無ければ、Python Launcher がある環境では以下の `python` を `py -3` に読み替える。
どちらも無い Codex 環境では `load_workspace_dependencies` ツールが返す Python executable の絶対パスを使う。
同梱ランタイムのパスは環境ごとに取得し、再インストールや PATH 変更は不要。

PowerShell では取得したパスを変数に入れ、呼び出し演算子 `&` で起動する。
JSONをパイプする場合も同じ形式を使う（リポジトリ直下で実行）。

```powershell
$mtgPython = '取得した Python executable の絶対パス'
$env:PYTHONIOENCODING = 'utf-8'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
& $mtgPython -m mtgtable --help
# JSON を標準入力で渡す場合:
# $batchJson | & $mtgPython -m mtgtable apply playtest/g1 --as p1
```

### 対局の作成と操作

```bash
python -m mtgtable new playtest/g1 --deck p1=decklists/piza.txt --deck p2=decklists/jund-sacrifice.txt --seed 1
```

```bash
python -m mtgtable view playtest/g1 --as p1
```

対局はゲーム前（turn 0 / `pregame`）から始まる。マリガンと開始時の手札からの行動を書いてから、最初の
`step`（または `turn_start`）で先攻の T1 に入る。

```bash
python -m mtgtable apply playtest/g1 batch.json --as p1 --view
```

`apply` はファイルか標準入力から Batch（JSON）を読む。結果は JSON で、
操作者が新しく知ったカード（`learned`）と、止まった理由（`stopped`）を返す。
失敗・前提不成立なら終了コード 2。

表示のオプション（`view` / `apply --view`。`log` / `replay` も `--no-names` を持つ）:

| オプション | 内容 |
|---|---|
| `--library` / `--graveyard` | ライブラリーの既知のカード・墓地の中身も出す。既定では枚数だけ |
| `--sideboard` | サイドボード（ゲーム外）も出す。既定では出さない |
| `--full` | サイドボード・ライブラリー・墓地を全部出す |
| `--no-names` | カード名を出さず id だけにする（AI が id を覚えているときの省略用） |
| `--json` | JSON で出す |

オブジェクトの id には必ず `#` が付く（`#c12` カード、`#t3` トークン、`#s4` スタック、`#n2` Note、
`#l5` Link、`#m1` マナ）。入力では `#` を省いてもよい。Player（`p1`）と領域名（`p1.hand`）は `#` なし。

### 対局を SQLite の DB に置く

既定では対局は1つのフォルダ（`playtest/g1` など）。`--db`（か環境変数 `MTGTABLE_DB`）を付けると、対局を SQLite の
DB に置く（サーバーで動かすときの形。[design/site_plan.md](design/site_plan.md) のフェーズ 1）。コマンドの使い方は同じで、
GAME はパスの最後の名前を対局の id にする（`playtest/g1` → `g1`）。

```bash
export MTGTABLE_DB=data/mtg.sqlite   # PowerShell: $env:MTGTABLE_DB = 'data/mtg.sqlite'
python -m mtgtable new g1 --deck p1=decklists/piza.txt --deck p2=decklists/jund-sacrifice.txt --seed 1
python -m mtgtable serve --play          # DB の対局を見る・遊ぶ
python -m mtgtable db-import playtest/g1 # 今の対局フォルダを DB へ移す（席の鍵・止める場所・プロンプトも）
```

- DB に入るのは初期状態・log・現在状態・席の鍵・席ごとの止める場所。AI のプロンプトと返答（作業ファイル）は
  DB の隣の `data/mtg-files/<対局>/prompts/` に置く
- 書き込みは DB 全体で1人ずつ（`BEGIN IMMEDIATE`）。読み手は止まらない（WAL）。サーバーと CLI（`auto` など）が同時に書いても壊さない
- 環境変数: `MTGTABLE_DB`（DB の場所）、`OPENAI_API_KEY`（AI のキー。[secrets/README.md](secrets/README.md)）

その他のコマンド:

| コマンド | 内容 |
|---|---|
| `ops` | Operation と手順の一覧 |
| `export DEST [--game ID ...]` | 観戦ビューアを静的サイトに書き出す（GitHub Pages など用。judge の席だけ。カードの画像・文・マナ・シンボルはサイトに含めず、見る人のブラウザが Scryfall から取る） |
| `serve [--port 8765]` | 観戦ビューアを起動し、http://127.0.0.1:8765/ で `playtest/` の対局を見る（席ごとの view、log の再生、AI が書いた変更を自動で反映、カード画像）。`--offline` で画像を取りに行かない。`--play` で GUI の対局も受ける（下の「GUI で AI と対戦する」） |
| `invite GAME --seat p1` | GUI で席を持つための鍵付き URL を作る。鍵は既定で7日で切れる（`--ttl 時間`。0 で期限なし） |
| `revoke GAME --seat p1` | 席の鍵を失効させる（席は人間の席のまま。`invite` で作り直すまで誰も使えない） |
| `db-import GAME...` | 対局フォルダを `--db` の DB へ移す（`--force` で置き換え） |
| `wait GAME --as p2` | 相手（GUI の人間）が書いて自分の番が来るまで待つ（AI 用）。`--prompt` で番が来たらプロンプトも書き出す |
| `next GAME --ai p2` | 審判か AI の席の番まで待ち、そのプロンプトを `playtest/<対局>/prompts/` に書き出す |
| `prompt GAME --as p2` / `--judge` | プロンプトを今すぐ書き出す |
| `answer GAME [FILE]` | モデルの返答を、最後に作ったプロンプトの役割（審判か AI の席）として卓に書く |
| `auto GAME [--watch]` | 審判と AI の席を OpenAI の API で回す（人間の番になるまで。`--watch` で人間の操作を待ちながら決着まで） |
| `undo` / `redo [n]` | Act 単位で戻す／やり直す |
| `log [--batches [N]] [--events]` | Operation Log（`--batches` は Batch ごとに1行。`--events` は全情報。観戦・デバッグ用で AI には見せない） |
| `replay --to N` | log の N 件目時点の状態 |
| `diff --from A [--to B]` | 2時点の State Diff |
| `fork DEST [--at N]` | 途中時点から別の対局を作る（Snapshot からの分岐） |
| `policy p1=omniscient` | Information Policy を変える |
| `oracle NAME...` | カードのオラクル・テキスト（初回は Scryfall から取得してキャッシュ） |
| `oracle --deck FILE` | デッキの全カードのオラクルを1枚の一覧で |
| `oracle --game GAME --as p1` | 対局の中で p1 が知っているカードのオラクル |
| `ids GAME --as p1 [#c12 ...]` | id とカード名の対応（p1 が知っているカードだけ） |
| `deck FILE [--fetch]` | デッキリストの枚数確認とオラクル・キャッシュの作成（`--fetch` で無いカードを取得） |

`--as` には `p1` / `p2` / `judge`。`judge` は全知（Judge / Orchestrator / 手動介入用）。`view` `replay` `ids` は
省略すると judge なので、Player として見るときは必ず `--as` を付ける。カード名は表示では `<Name>` で囲む。

一人回し（Goldfish）はデッキを1つだけ渡し、必要なら Policy を広げる:

```bash
python -m mtgtable new playtest/gf --deck p1=decklists/piza.txt --seed 7 --policy p1=own_library
```

## GUI で AI と対戦する

人間がブラウザで1席（例: p1）を持ち、AI（Claude Code。スキル `mtg-playtest` の「人間が GUI で相手をする対局」）が
もう1席を CLI で持つ。どちらの席も卓（盤面）は動かさず、審判に依頼する。盤面を動かすのは審判だけ
（[design/request_play_design.md](design/request_play_design.md)）。

```bash
python -m mtgtable new playtest/g1 --deck p1=decklists/piza.txt --deck p2=decklists/jund-sacrifice.txt --seed 1
python -m mtgtable invite playtest/g1 --seat p1
python -m mtgtable serve --play
```

`invite` が出す URL（`http://127.0.0.1:8765/?game=g1&seat=p1#key=...`）を開くと、その席で操作できる。

- やることは**下書き**に足す: カード・束・Player・スタックを押したメニューで、土地として出す・唱える（対象と文は任意）・
  起動する・攻撃する（攻撃先は相手。プレインズウォーカー・バトルへは戦闘の欄で攻撃先を押して変える）・ブロックする・1 枚引く…を選ぶと、操作パネルの「審判への依頼（下書き）」に行が増える。
  **送るまで盤面は変わらない**（下書きのカードには点線の印、引く予定は手札に「？」、束に「予定」が付く）
- 送る前の行は、行の「×」・「直前の行を取り消す」・「全部捨てる」で何度でも外せる（下書きはブラウザに残り、再読み込みでも消えない）
- 送るボタンは「審判に依頼」の1つ。「その後」は下書きの最後の行で決まり、ボタンに出る: 進めるボタン（自分のターンのステップを全部アイコンで決まった位置に並べ、行けない所・相手のターンは押せない。
  下の段は »次のステップ・▶|ターン終了・↑パス）で足した
  「○○へ進む」はそのステップへ、「ターン終了」はターンを終える、「パス」は優先権を渡す。無ければ、自分のターンは続ける・
  相手のターンはパス（相手のクリンナップでは自分のターンを始める）。下書きが空でパスになるなら、審判を通さずにそのまま渡す。
  唱える・起動の欄は「唱えて解決」（「起動して解決」）が既定で、積む行と解決する行を一緒に足す（相手が対応しなければ審判が解決する）。
  積んだままにする（解決の前に続けて何か積む）なら「スタックに積むだけ」。
  ブロックは盤面で組み（「ブロックしない」も行になる）、「審判に依頼」で送る。ボタンに無い操作・補足（対象・X など）は、依頼の欄の文の欄に書いて Enter で1行足す。マナ・タップイン・コストの支払い・誘発・解決は審判が処理する
- 送った依頼は取り消せない。審判が処理している間は操作できない。引いたカードは、審判の処理の後に初めて見える
- 計画（依頼の行）が途中で止まったとき: 相手が対応しなければ審判がそのまま続ける。相手が割り込んだ（呪文・能力・ブロック）・
  引いたカードを見て決める所で止まったときは、残りの行と「その後」が下書きに戻る（入力し直さずに、直してから送れる）
- 依頼を送れるのは、ゲーム前と、自分が待たれているとき（優先権・ブロック・審判の質問）だけ。相手が考えている間も下書きは
  作っておける。それ以外のときに伝えたいことは発言で
- 審判の質問は「今の操作」に選択肢つきで出る。送った依頼は「送った依頼」で見返せる
- キープ・マリガンは宣言だけ（引き直し・下に置くカードは審判が処理し、下に置くカードは審判が聞く）
- 鍵の無い席・judge は見えない（鍵を作った対局だけ。他の対局は今までどおり観戦できる）。外部に公開するサーバーとしての
  運用（HTTPS・DB・AI の HTTP 接続など）は [design/web_design.md](design/web_design.md) の「サーバーで動かすときに残っていること」

### 公開のサーバーとして動かす（`serve --site`）

[design/site_plan.md](design/site_plan.md) のフェーズ 2 まで。`--db` が要り、`--play` を含む。

```bash
python -m mtgtable --db data/mtg.sqlite serve --site --host 127.0.0.1 --port 8765   # 前に HTTPS のリバース・プロキシを置く
python -m mtgtable --db data/mtg.sqlite invite g1 --seat p1 --base https://mtg.example.com/
```

- 初めて来たブラウザに「所有者の鍵」を Cookie（HttpOnly・SameSite=Lax、外向きの名前では Secure）で渡す。DB にはハッシュだけ
- 招待の URL を開いた所有者が、その席を取る。以後はその所有者だけが、Cookie だけで（鍵を覚えていない端末でも）その席に入れる。
  同じ招待の URL を他の人が開いても通らない
- 対局の一覧は自分が席を持つ対局だけ。judge の席・鍵の無い対局・他の人の席は見せない
- ヘッダーの「復元 URL」で、別の端末（か Cookie を消したブラウザ）を同じ所有者に戻す URL を作る。作り直すと前の URL は使えない
- 書き込みは、同じサイトのページからだけ（`Origin` を確かめる）
- ヘッダーの「トップ・デッキ・対局」で画面を切り替える。デッキの画面でデッキリストを貼り付けて登録する。登録・更新のたびに
  検査し（カード名は Scryfall の `/cards/collection` で 75 枚ずつまとめて引く。読めない行・見つからない名前は行番号つき）、
  通ったものだけを保存する。検査の中身: メイン 60 枚以上・サイドボード 15 枚まで・同じ名前は 4 枚まで（基本土地と
  「好きな枚数を入れてよい」カードは除く）・フォーマットで使えるか（制限カードは 1 枚）。プレイ方針（任意）は、そのデッキを使う
  AI のプロンプトに入る
- 「対局を作る」で、登録したデッキを選んで AI と（AI のデッキは `decklists/` のものか自分のデッキ）、または人を招待して
  対戦する。招待の URL は1回だけ使え、3日で切れる（作り直し・取り消しができる）。招待された人は自分のデッキを選んで席に
  着き、その時点で対局ができる。デッキは対局を作る時点の写しを使う（後で直しても対局は変わらない）。公開・非公開を選べる
- 人間どうしの対局で相手の番が 5 分続くと画面に出し、30 分続いたら「時間切れで勝ちにする」（相手の投了として書く）
- 審判と AI の席はサーバーの中で回す（下の「LLM の API で自動で回す」。`--no-ai` で止める）
- 利用者の識別より先（HTTPS の設定・対局を作る画面・レート制限など）はまだ。外に出すのは計画のフェーズ 7 の後

### 人間どうしで対戦する

両方の席に鍵を作れば、2人がそれぞれのブラウザで席を持つ。AI が受け持つのは審判だけ（鍵の無い席が AI の席）。

```bash
python -m mtgtable invite playtest/g1 --seat p1
python -m mtgtable invite playtest/g1 --seat p2
python -m mtgtable serve --play
python -m mtgtable auto playtest/g1 --watch   # 審判だけを回す（手動なら next / answer）
```

- それぞれの URL を、その席の人にだけ渡す。サーバーは 127.0.0.1 で待ち受けるので、今は同じ PC で使う
  （外に出すには HTTPS などが要る。[design/site_plan.md](design/site_plan.md)）
- 流れは `tests/test_pvp.py` で確かめている（キープ → ターンの受け渡し → 相手のターンの割り込み → 審判の質問 →
  止まった計画の戻り → ブロック → 投了）

### AI の席と審判をプロンプトで回す（手動）

AI は2つの役割に分かれる。**Player** は何をするかを決めて意図を文で返すだけで、**審判（judge）** がそれを操作（Batch）に
直し、ルールを確かめて卓に書く。人間も同じで、GUI の下書きを依頼として審判に送る。審判は、決まっていない選択
（対象・モード・捨てるカードなど）をその Player に質問する（GUI では「今の操作」に選択肢が出る）。やりとりは卓の宣言
（`intent` 依頼 / `ask` 質問 / `answer` 回答 / `ruled` 処理済み）として記録に残る。

OpenAI の API で自動で回すか（`auto`）、プロンプトをファイルに書き出して人がモデルに渡し、返答を `answer` に渡す（手動）。

#### LLM の API で自動で回す

提供元は OpenAI か Anthropic（Claude）。役割（審判・AI の席）ごとに分けてもよい。設定とキーは [secrets/README.md](secrets/README.md)。

1. API キーを `secrets/openai_api_key`（か `secrets/anthropic_api_key`）に1行で書く（環境変数 `OPENAI_API_KEY` /
   `ANTHROPIC_API_KEY` でもよい）。Anthropic を使うなら `pip install anthropic`
2. サーバーの中で回す: `serve --play --ai`（`serve --site` では既定）。人間が書くとすぐ、審判か AI の席の番なら回る

```bash
python -m mtgtable serve --play --ai
```

別のプロセスで回すなら、`serve --play` とは別のターミナルで `auto` を `--watch` 付きで動かす（`--ai` と同時には使わない。
同じ番を2度呼ぶ）:

```bash
python -m mtgtable auto playtest/g1 --watch
```

サーバーの中で回すとき（`--ai` / `--site`）:

- 同じ対局は同時に1つ、全体で 4 対局まで並べて回す。1手ごとに画面へ届く
- 返答を続けて卓に書けない・API を呼べない・利用の上限に当たったら、その対局を「中断」にし、理由を画面に出す。
  席を持つ人が「再開する」で動かし直す
- 利用の上限（環境変数。トークンは入力（キャッシュ分を含む）＋出力）: `MTGTABLE_AI_CALLS_PER_GAME`（既定 600 回）・
  `MTGTABLE_AI_TOKENS_PER_GAME`（600万）・`MTGTABLE_AI_TOKENS_PER_OWNER_DAY`（所有者ごとの1日。1000万）・
  `MTGTABLE_AI_TOKENS_PER_DAY`（サイト全体の1日。1億）。数えた分は DB（`--db`）の `ai_usage` に残る

- 審判か AI の席（鍵の無い席）の番になるたびに、プロンプトを作って送り、返答を卓に書く。人間の番の間は待つ
- 待つのは `serve` の更新通知（SSE）。GUI の操作で、その場で起きる（`--server`、既定 http://127.0.0.1:8765）。サーバーに
  つながらない間は1秒ごとに卓を見て、10秒ごとにつなぎ直す
- 提供元・モデルは `secrets/llm.json` で指定する（審判・AI の席を分けるなら `judge_*` / `player_*`、考える深さは `effort`）。
  一時的に変えるなら `auto --provider` / `--model`。何も無ければ OpenAI の `gpt-5`
- 固定部分（役割の指示・リファレンス・デッキ）を先頭に置くので、プロンプト・キャッシュが効く（Anthropic では固定部分に
  TTL 1時間の区切りを付ける）。効いた量は `prompts/<役割>/usage.jsonl` の `cached_tokens`（どの提供元でも同じ形）
- 返答を続けて適用できなかったら止まる（`--max-failures`、既定 3）。そのときは `prompts/<役割>/` の `*.response.md` を見る
- 送るのは、その役割に見せてよい情報だけ（AI の席には自分の view、審判には全情報）。送り先は選んだ提供元の API だけ

#### 手動で回す

```bash
python -m mtgtable next playtest/g1 --ai p2
python -m mtgtable answer playtest/g1 response.md
```

GUI の人間がいる対局（`invite` した対局）では、`serve --play` が GUI の依頼（「審判に依頼」）を受けた時点で
審判のプロンプトを書き出し、サーバーのコンソールに場所を出す。`answer` も、卓に書いた後に審判か AI の席（鍵の無い席）の番なら
次のプロンプトを書き出す。人間の番になったら `waiting on p1` と出る。

`next` は審判か AI の席（`--ai`）が動く番まで待ち、その役割のプロンプトを書き出す（人間の番の間は待つ。自動で作られなかったときに使う）。`answer` は
最後に作ったプロンプトの役割として返答を卓に書く。これを繰り返す。`--direct` で、AI の席が審判を通さず Batch を直接書く
前の形にもできる（比較用）。

`playtest/<対局>/prompts/<p2 か judge>/` に書き出すもの:

| ファイル | 中身 |
|---|---|
| `system-1.md` | 役割の指示（`mtgtable/prompts/player_intent.md` / `judge.md`）とリファレンス。全対局で同じ（Player は rules.md だけ、審判は cli.md・patterns.md・rules.md も） |
| `system-2.md` | Player: 席・自分のデッキのオラクル・戦略メモ。審判: 両者のデッキのオラクル。対局の間は同じ |
| `NNNN.user.md` | 今回の状況。Player: 前回からの出来事・memo・自分の view・相手のカード・審判の質問。審判: 処理する依頼・最近の記録・全情報の盤面 |
| `NNNN.request.json` | OpenAI の Chat Completions の本文（固定部分の system 2つ ＋ 今回の user） |
| `latest.md` | 手動用に system と user を1つにしたもの（そのまま貼る） |
| `memo.md`（Player） / `NNNN.response.md` | Player の memo（次のプロンプトに入る） / 受け取った返答 |

返答の形: Player は `{"request": {"plan", "then", "comment"}}`・`{"declare": {"kind", "text"}}`・`{"answer": {"text", "cards"}}` の
どれか1つと `memo`（GUI の席の API と同じ形）、
審判は `{"batch": {...各 Act に actor}, "ask": {"to", "text", "choices"}, "message"}`。適用できなかったとき・返答の間に
盤面が進んでいたときは、`answer` が理由を付けてプロンプトを作り直す。

## Batch と Operation

AI は Batch（JSON）を `apply` に渡して卓を動かす。書式・Operation の一覧・カード参照・Precondition・
エンジンが自動でやることは、スキルの [references/cli.md](.claude/skills/mtg-playtest/references/cli.md) が正本。
よくある処理の書き方は [references/patterns.md](.claude/skills/mtg-playtest/references/patterns.md)、
MTG の基本ルールの要点は [references/rules.md](.claude/skills/mtg-playtest/references/rules.md)。

```json
{"actor": "p1", "label": "T3: Forest, Llanowar Elves", "acts": [
  {"proc": "turn_start", "to": "main1"},
  {"act": [{"op": "land", "card": "#c12", "mana": "{G}"}]},
  {"act": [{"op": "cast", "card": "#c40", "pay": {"#c12": "G"}}]},
  {"act": [{"op": "stack_remove"}], "label": "解決"},
  {"proc": "turn_end"}
]}
```

- **Act**（`{"act": [op, ...]}`）は、ルール上一体として行う処理の1セット（呪文を唱える、解決する、
  「1枚捨てる。そうしたなら1枚引く」など）。丸ごと適用するか丸ごと取り消す。log の1件・Undo の1単位
  （解決の途中で見てから選ぶときは `cont: true` でパートに分け、次の Batch に続きを書く。Undo は Act 単位）
- **手順**（`{"proc": "turn_start"}` / `{"proc": "turn_end"}`）は、複数の Act の並びの省略。順番が来たときの状態で
  Act の並びに展開する。**複合 op**（`cast` `land` `pay`）は1つの Act の中で使う省略。どちらもルールの判定はしない
- **Batch** が止まるのは、失敗と前提不成立だけ。知らないカードを見たときも、優先権が動いたときも止めない。
  どこで区切るか（見てから選ぶ所、相手の応答を待つ所）は操作する AI が決める。人間が相手なら、対応が予想されない
  場面は確認を省いて進める。対応の可能性があるクリティカルな場面や相手の選択が必要な場面では止める。
  確認を省いた旨はラベルに残し、ユーザーが明示的にパスした記録とは区別する。相手は巻き戻し（`undo --to N`）を請求できる

## 情報公開

- **PlayerView**: その Player が知り得る情報だけ。相手の手札は枚数と既知のカード、
  ライブラリーは枚数と既知のカード（位置が分かるものは `known_positions`、
  シャッフル後などで位置が分からないものは `known_unordered`）。
  知らない非公開カードは id も出さない。公開領域の裏向きカードは id だけ出す（紙でも追跡できるため）。
- **KnownInformation**: 見えたカード・`reveal`・`look` で知ったカードは覚えておき、
  非公開領域へ移っても知ったまま。シャッフルで位置の記憶だけが消える。
- **InformationPolicy**（`--policy` / `policy`）: `normal` / `own_library` / `all_libraries` / `omniscient`。

## オラクルのキャッシュ

カード情報は Scryfall から取得し、リポジトリ直下の `cards/` にキャッシュする（`.gitignore` 済み）。
1枚ごとの `cards/<名前>-<hash>.json` と、デッキ1つ分を束ねた `cards/decks/<デッキ名>-<hash>.json`
（hash はデッキリストの中身から作る）。`new` が両デッキのキャッシュを用意し、無いカードだけ取りに行く。

## 記録と再現

`playtest/<対局>/` に `initial.json`・`log.jsonl`・`state.json` を置く。log の各件（Act）は、AI が書いた `act` と、
実際に適用した基本の op（`steps`。複合 op を展開し、エイリアスを id に置き換えたもの）と event を持つ。
Replay は初期状態に `steps` だけを適用し直す。乱数（シャッフル・無作為選択）は `(seed, version)` から決まるので、
同じ状態になる。Undo はこれで cursor を戻す。

## 未実装（今後）

- GUI の対局のサーバー運用（HTTPS・利用者の識別・AI をサーバーで回す。今はローカルの1台だけ）。Human vs Human の画面は未確認
- Judge / Orchestrator の自動進行（27節）。現状は `--as judge` での手動操作だけ
- 旧実装にあった対局記録の集計・ベンチマーク・Goldfish の統計

## テスト

```bash
python -m unittest discover -s tests
MTGTABLE_TEST_STORE=sqlite python -m unittest discover -s tests   # 同じテストを SQLite の保存先で（CI は両方）
```

Web の通信・時系列復元のテスト（開発時のみ Node.js 24 が必要）:

```bash
node --test tests/web.test.mjs
```

| ファイル | 対象 |
|---|---|
| `helpers.py` | 共通部品（小さなデッキ2つの対局、`ok`、保存先を開く `store` / `viewer` など） |
| `test_setup.py` | デッキリスト・初期状態 |
| `test_info.py` | 公開範囲・記憶・Player View・表示 |
| `test_operations.py` | 基本の Operation（カード・Link・トークン・スタック・戦闘・マナ・ライブラリー・ログの文字列） |
| `test_composite.py` | 複合 op（`pay` `cast` `land`）と手順（`turn_start` `turn_end`） |
| `test_turn.py` | ターン・ステップ・ゲーム前・宣言・優先権のパス |
| `test_batch.py` | Act・Batch・エイリアス・Act ごとの actor・代理の宣言 |
| `test_store.py` | Operation Log・Undo/Redo・Replay・Diff・Fork |
| `test_decks.py` | デッキ登録: 検査（書式・カード名・枚数・フォーマット）、Scryfall のまとめ引き（偽の Scryfall）、デッキの API、AI のプレイ方針 |
| `test_lobby.py` | 対局を作る: AI との対局・デッキの写し・招待（1回だけ・同時に着いても1人・期限・作り直し・取り消し）・時間切れ |
| `test_worker.py` | サーバーの中で回す: 人間が書いたらすぐ回る・利用の上限で中断・席の鍵で再開・API の失敗・所有者と1日の数え方 |
| `test_site.py` | 公開のサーバー: 所有者の鍵の Cookie・招待の URL で席を取る・自分の対局だけ・復元 URL・Origin・鍵の期限と失効 |
| `test_sqlstore.py` | SQLite の保存先: 対局フォルダからの移行・一覧の要約（終わった日時）・ロールバック・同時の書き込み・CLI の `--db` |
| `test_carddb.py` | オラクルのキャッシュ |
| `test_web.py` | 観戦ビューアのサーバー（席ごとの view・log・静的ファイル）と GUI の対局の API（席の鍵・依頼・宣言・回答だけの書き込み・409） |
| `test_play.py` | 待たれている Player・`wait`・書き込みの排他・席の鍵 |
| `test_pvp.py` | 人間どうしの対局（両方の席に鍵・審判だけ AI）の通し: 手番の受け渡し・割り込み・質問・ブロック・待たれていない席の依頼の拒否 |
| `test_prompt.py` | AI の席のプロンプト（固定部分が変わらないこと・見せる範囲）と返答の適用 |

## 権利

[NOTICE.md](NOTICE.md) を参照。カードテキストのキャッシュ（`cards/`）はリポジトリに含めない。
