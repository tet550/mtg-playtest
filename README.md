# mtgtable — AI が紙の MTG をプレイするためのデジタル卓

設計は [design/basic_design.md](design/basic_design.md)。これはその基本実装（GUI なし）。

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
| `mtgtable/store.py` | Operation Log・Undo/Redo・Replay・Snapshot(fork)・State Diff | 29節 |
| `mtgtable/setup.py` | デッキリスト読み込みと初期状態 | — |
| `mtgtable/carddb.py` | Rule Reference（Scryfall のオラクルをローカルにキャッシュ） | 28節 |
| `mtgtable/render.py` | PlayerView のテキスト表示 | 24節（表示とモデルの分離） |
| `mtgtable/cli.py` | コマンドライン | 26節 |
| `mtgtable/web.py`・`mtgtable/web/` | 観戦ビューア（`serve`。読み取り専用のブラウザ画面） | 24〜25節 |

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

その他のコマンド:

| コマンド | 内容 |
|---|---|
| `ops` | Operation と手順の一覧 |
| `export DEST [--game ID ...]` | 観戦ビューアを静的サイトに書き出す（GitHub Pages など用。judge の席だけ。カードの画像・文・マナ・シンボルはサイトに含めず、見る人のブラウザが Scryfall から取る） |
| `serve [--port 8765]` | 観戦ビューアを起動し、http://127.0.0.1:8765/ で `playtest/` の対局を見る（席ごとの view、log の再生、AI が書いた変更を自動で反映、カード画像）。`--offline` で画像を取りに行かない |
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

- GUI の操作（24〜26節）。今は観戦ビューア（`serve`）だけ
- Judge / Orchestrator の自動進行（27節）。現状は `--as judge` での手動操作だけ
- 旧実装にあった対局記録の集計・ベンチマーク・Goldfish の統計

## テスト

```bash
python -m unittest discover -s tests
```

| ファイル | 対象 |
|---|---|
| `helpers.py` | 共通部品（小さなデッキ2つの対局、`ok` など） |
| `test_setup.py` | デッキリスト・初期状態 |
| `test_info.py` | 公開範囲・記憶・Player View・表示 |
| `test_operations.py` | 基本の Operation（カード・Link・トークン・スタック・戦闘・マナ・ライブラリー・ログの文字列） |
| `test_composite.py` | 複合 op（`pay` `cast` `land`）と手順（`turn_start` `turn_end`） |
| `test_turn.py` | ターン・ステップ・ゲーム前・宣言・優先権のパス |
| `test_batch.py` | Act・Batch・エイリアス・Act ごとの actor・代理の宣言 |
| `test_store.py` | Operation Log・Undo/Redo・Replay・Diff・Fork |
| `test_carddb.py` | オラクルのキャッシュ |
| `test_web.py` | 観戦ビューアのサーバー（席ごとの view・log・静的ファイル） |

## 権利

[NOTICE.md](NOTICE.md) を参照。カードテキストのキャッシュ（`cards/`）はリポジトリに含めない。
