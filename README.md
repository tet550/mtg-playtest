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
| `mtgtable/engine.py` | ActionGroup・Batch・Precondition | 20〜23節 |
| `mtgtable/store.py` | Operation Log・Undo/Redo・Replay・Snapshot(fork)・State Diff | 29節 |
| `mtgtable/setup.py` | デッキリスト読み込みと初期状態 | — |
| `mtgtable/carddb.py` | Rule Reference（Scryfall のオラクルをローカルにキャッシュ） | 28節 |
| `mtgtable/render.py` | PlayerView のテキスト表示 | 24節（表示とモデルの分離） |
| `mtgtable/cli.py` | コマンドライン | 26節 |

## 使い方

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
| `ops` | Operation の一覧 |
| `undo` / `redo [n]` | ActionGroup 単位で戻す／やり直す |
| `log [--events]` | Operation Log（`--events` は全情報。観戦・デバッグ用で AI には見せない） |
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
{"actor": "p1", "groups": [
  {"label": "T3 開始", "ops": [{"op": "turn_start", "to": "main1"}]},
  {"label": "Forest, Llanowar Elves", "pre": [{"step": "main"}], "ops": [
     {"op": "land", "card": "#c12", "mana": "{G}"},
     {"op": "cast", "card": "#c40", "pay": {"#c12": "G"}}]},
  {"label": "T3 終了", "ops": [{"op": "turn_end"}]}
]}
```

- `land` `cast` `push_resolve` `pay` `turn_start` `turn_end` は、基本の Operation（`tap` `mana_add` `stack_push`
  `stack_remove` `step` など）をまとめた複合 Operation。ルールの判定はしない

- **ActionGroup**（`groups` の1要素）は丸ごと適用するか丸ごと取り消す。log の1件・Undo の1単位
- **Batch** が止まるのは、失敗と前提不成立だけ。知らないカードを見たときも、優先権が動いたときも止めない。
  どこで区切るか（見てから選ぶ所、相手の応答を待つ所）は操作する AI が決める。人間が相手なら、クリティカルな
  場面でパスの宣言を求め、もらわずに進めた場面は相手が巻き戻し（`undo --to N`）を請求できる

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

`playtest/<対局>/` に `initial.json`・`log.jsonl`・`state.json` を置く。乱数（シャッフル・無作為選択）は
`(seed, version)` から決まるので、初期状態から log を適用し直せば同じ状態になる。Undo はこれで cursor を戻す。

## 未実装（今後）

- GUI（24〜25節）
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
| `test_composite.py` | 複合 Operation（`pay` `cast` `push_resolve` `land` `turn_start` `turn_end`） |
| `test_turn.py` | ターン・ステップ・ゲーム前・宣言・優先権のパス |
| `test_batch.py` | ActionGroup・Batch・エイリアス・グループごとの actor・代理の宣言 |
| `test_store.py` | Operation Log・Undo/Redo・Replay・Diff・Fork |
| `test_carddb.py` | オラクルのキャッシュ |

## 権利

[NOTICE.md](NOTICE.md) を参照。カードテキストのキャッシュ（`cards/`）はリポジトリに含めない。
