# mtgtable スクリプト・リファレンス

`python -m mtgtable <コマンド>`。リポジトリ直下で実行する。Operation の一覧は `python -m mtgtable ops` でも出る。
このファイルが Operation・Batch・参照・Precondition の正本（README は概要だけ）。

## 目次

- [コマンド](#コマンド)
- [id と名前](#id-と名前)
- [Batch と ActionGroup](#batch-と-actiongroup)
- [apply の結果](#apply-の結果)
- [Operation](#operation)
- [カード参照](#カード参照)
- [Precondition](#precondition)
- [エンジンが自動でやること](#エンジンが自動でやること)
- [情報公開](#情報公開)

## コマンド

| コマンド | 内容 | 主なオプション |
|---|---|---|
| `new GAME --deck p1=FILE [--deck p2=FILE]` | 対局を作る。デッキのオラクル・キャッシュも用意する | `--seed N` `--first p2` `--hand 7` `--life 20` `--policy p1=omniscient` `--offline` `--force` |
| `view GAME --as pN` | Player View。ライブラリー・墓地は枚数だけ、サイドボードは出さない | `--no-names` `--library` `--graveyard` `--sideboard` `--full` `--json` `--oracle` |
| `apply GAME [FILE] --as pN` | Batch を適用（FILE 省略で標準入力） | `--view`（と view の表示オプション） |
| `ids GAME --as pN [#c12 ...]` | id とカード名の対応（知っているカードだけ。省略で手札・戦場・スタック・墓地・追放） | |
| `undo GAME [n]` / `redo GAME [n]` | ActionGroup 単位で戻す／やり直す | `undo --to N`（log の N 件目の直後まで。割り込みの巻き戻しに） |
| `log GAME` | Operation Log（`~` は Undo 済み。ラベルの無いグループは Operation の要約） | `--last N` `--events`（全情報。判断には使わない） `--no-names` |
| `replay GAME --to N` | log の N 件目時点 | `--as` `--json` `--no-names` |
| `diff GAME --from A [--to B]` | 2時点の状態差分 | `--all`（knowledge も） |
| `fork GAME DEST [--at N]` | 途中時点から別の対局 | `--force` |
| `policy GAME p1=omniscient` | Information Policy を変える | |
| `ops` | Operation の一覧 | |
| `oracle NAME...` | カードのオラクル（初回は Scryfall から取得してキャッシュ） | `--offline` `--refresh` `--brief` `--json` |
| `oracle --deck FILE` | デッキの全カードを1枚の一覧で（デッキ・キャッシュから） | `--no-sideboard` `--brief` `--offline` |
| `oracle --game GAME --as pN [--card #c12 ...]` | 対局の中で pN が知っているカード（`--card` 省略でサイドボード以外の全部） | `--brief` |
| `deck FILE` | 枚数の確認とデッキ・キャッシュの作成 | `--fetch`（無いカードを取得） `--refresh` |

- `--as` は `p1` / `p2` / `judge`（全知）。Player の判断に `judge` を使わない
- `--no-names` はカード名を出さず id だけにする。`apply` の `learned` は `--no-names` でも名前を出す
  （新しく知ったカードの id と名前はここで分かる）。後で名前が要るときは `ids` か `oracle --game ... --card` で引く
- `--brief` は注釈文（括弧内）を省く。トークンの能力（Lander・Treasure など）は注釈文にしか無いことがあるので、
  初めて読むカードには使わない
- 終了コード: `apply` は失敗・前提不成立で 2、`oracle` は見つからないカードがあると 1

### オラクルのキャッシュ

| 置き場所 | 中身 |
|---|---|
| `cards/<名前>-<hash>.json` | 1枚ごと。Scryfall から取った名前・コスト・タイプ・本文・P/T など |
| `cards/decks/<デッキ名>-<hash>.json` | デッキの全カードを束ねたもの。hash はデッキリストの中身から作る（リストを直すと別ファイル） |

`new` は各デッキのキャッシュを作って `initial.json` の `meta.decks.<pN>.oracle` に記録する。
キャッシュに無いカードだけ Scryfall に取りに行く（`--offline` で取りに行かない）。
`cards/` はリポジトリ直下（`MTG_CARDS_DIR` で変更可）。第三者の著作物なのでコミットしない。

## id と名前

- オブジェクトの id は必ず `#` 付き: `#c12` カード、`#t3` トークン、`#s4` スタック、`#n2` Note、`#l5` Link、`#m1` マナ
- 入力では `#` を省いてよい（`c12`）。Player は `p1`、領域は `p1.hand` / `battlefield` / `stack` / `exile` / `command`
- 個人の領域: `pN.library` / `pN.hand` / `pN.graveyard` / `pN.sideboard`。`hand` のように省くと、
  `move` の `to` ではそのカードの持ち主の領域、それ以外では操作者の領域
- 順序付き領域（library / graveyard / stack）は index 0 が一番上

## Batch と ActionGroup

```json
{"actor": "p1", "groups": [
  {"label": "説明", "pre": [{"step": "main"}], "ops": [ ... ]}
]}
```

- `actor` は `--as` で渡してもよい（両方書くなら一致させる）。`[op, ...]` や `{"ops": [...]}` は1グループの略記
- **グループごとの actor**: Batch の actor を judge（`--as judge`）にすると、各グループに `"actor": "p2"` を書ける。
  AI 同士の対戦で、相手の手番まで1つの Batch に入れるときに使う。各グループはその actor として適用され
  （知らないカードの id は使えない）、log にもその actor で残る。actor を書かないグループは judge になるので、
  この形では全グループに actor を書く
- **代理の宣言（`"proxy": "p1"`）**: Batch の actor が Player のとき、グループに `"proxy"` を書くと、そのグループを
  相手の Player として適用する（人間が相手の対局で、相手の宣言を同じ Batch に入れるとき）。log には
  `p1  [proxy by p2] ...` と残り、結果にも `"proxy_by"` が付く。`learned` と id の伏せ字は操作者（Batch の actor）
  から見た形。エンジンは妥当かどうかを判定しない（止めない）。使ってよい場面と巻き戻しは SKILL.md の「代理の宣言」
- **ActionGroup**: 1つでも Operation が失敗したら丸ごと取り消す。log の1件・Undo の1単位
- **Batch**: 先頭から順に適用し、次で止まる

| `stopped.reason` | いつ | 止まったグループ |
|---|---|---|
| `failed` | Operation が適用できない | 取り消し。以降は `skipped` |
| `precondition_failed` | `pre` が成り立たない | 適用しない。以降は `skipped` |

- **止まるのはこの2つだけ。** 知らないカードを見たとき（ドロー・公開・サーチ）も、優先権が相手に渡ったときも
  止まらない。見てから選ぶ・相手の応答を待つ所で Batch を区切るのは AI の責任（SKILL.md の「Batch の計画」と
  「優先権のパス」）。新しく知ったカードは結果の `learned` に出る
- **エイリアス**: `"as": "名前"` を付けた Operation の結果を、同じ Batch の後ろで `"$名前"` として参照できる。
  次の `apply` には持ち越さない
- 乱数（シャッフル・無作為）は `(seed, version)` で決まる。Replay で同じ結果になる

## apply の結果

```json
{"actor": "p1", "applied": 2, "version": 17, "stopped": null,
 "groups": [{"label": "...", "status": "applied", "version": 16,
             "created": ["#s3"], "aliases": {"spell": ["#s3"]},
             "learned": [{"id": "#c40", "name": "Forest", "zone": "p1.hand"}],
             "links_removed": [...], "results": [...]}]}
```

- `learned`: このグループで操作者が新しく知ったカード
- `links_removed`: 領域移動で外れた Link（張り直すかは AI が決める）
- 操作者が知り得ないカードの id は `"hidden"` に置き換わる

## Operation

`player` を省くと操作者。`card` / `cards` はどちらでもよい（リストや参照も可）。

### カード

| op | パラメーター | 内容 |
|---|---|---|
| `move` | `card, to, position?, order?, face_down?, tapped?, controller?, keep?, as?` | 領域を移す。`position`: `top`（既定）/ `bottom` / 数値。複数を `top` に置くと先頭が一番上。`order: "random"` で無作為の順に置き、全員その位置を知らない扱いになる |
| `draw` | `player?, count?=1, as?` | ライブラリーの上から手札へ。空なら `short` に足りない枚数 |
| `shuffle` | `zone?=library, owner?` | 全員その領域の位置の記憶を失う |
| `tap` / `untap` | `card` | |
| `untap_all` | `player?` | そのプレイヤーがコントロールする戦場のパーマネント全部 |
| `set` | `card, tapped?, face_down?, face?, controller?, name?` | 物理状態を直接設定 |
| `create` | `name` または `copy_of`, `definition?, zone?=battlefield, controller?, owner?, count?=1, token?=true, tapped?, face_down?, as?` | トークン等。`copy_of` は元カードを参照（コピーのコピーも大元を指す）。`definition` は解釈しない辞書 |
| `remove` | `card` | ゲームから取り除く（トークンの消滅など）。Counter / Note / Link も消える |
| `reveal` | `card, to?=all` | 見た Player が中身を覚える |
| `look` | `card, player?, as?` | その Player だけが見る（占術・サーチ） |

### Counter / Note / Link

| op | パラメーター | 内容 |
|---|---|---|
| `counter_add` / `counter_remove` / `counter_set` | `target, kind, amount?=1` | target はカード・Player など。0 になったら消える |
| `note_add` | `target, text, until?, as?` | 単一対象の付箋。`until` は自由記述（`end_of_turn` など。エンジンは解釈しない） |
| `note_update` | `note, text?, until?` | 同じ効果が重なったら1枚を更新する |
| `note_remove` | `note` または `target` / `until` | `{"until": "end_of_turn"}` でまとめて外す |
| `link_add` | `kind, source, targets, text?, as?` | 複数対象の関係（`attached` `exiled_by` `target` など。kind は自由） |
| `link_remove` | `link` または `object, kind?` | |

### Stack

| op | パラメーター | 内容 |
|---|---|---|
| `stack_push` | `card?` または `source?`, `kind?, controller?, text?, targets?, as?` | `card` を渡すとそのカードをスタック領域へ（呪文）。`kind`: `spell` / `activated` / `triggered` など。`targets` を渡すと `target` Link を張る。積んだ Player が優先権を持つ |
| `stack_remove` | `item?`（省略で一番上）, `card_to?, position?` | 解決・打ち消し。`card_to` で呪文のカードも動かす。その項目が source の Link も外す。アクティブ・プレイヤーが優先権を持つ |
| `stack_move` | `item, index` | 順番の入れ替え（index 0 が一番上） |

### Combat

| op | パラメーター | 内容 |
|---|---|---|
| `attack` | `attacker(s), target, tap?` | `tap: true` でタップ（警戒なら付けない） |
| `block` | `blocker, attacker` | 攻撃していないクリーチャーはブロックできない。複数ブロックは複数回 |
| `combat_remove` | `card` | 戦闘から取り除く |
| `combat_clear` | | 戦闘終了時に |

### Mana

| op | パラメーター | 内容 |
|---|---|---|
| `mana_add` | `color, amount?=1, source?, duration?, note?, player?, as?` | 色・source・duration・note が完全に同じマナとはまとまる。用途制限は `note` |
| `mana_spend` | `mana` または `color`, `amount?=1, player?` | `color` 指定は制限の無いマナから使う |
| `mana_clear` | `player?, keep_duration?` | ステップ・フェイズの終わりに |

### Player / Turn / 優先権

| op | パラメーター | 内容 |
|---|---|---|
| `life` | `amount`（増減）または `set`, `player?` | |
| `player_set` | `status?, name?, player?` | `status`: `playing` / `lost` / `won` / `conceded` / `draw` |
| `step` | `to` | 名前で進める: `untap` `upkeep` `draw` `main1` `beginning_of_combat` `declare_attackers` `declare_blockers` `combat_damage` `end_of_combat` `main2` `end` `cleanup`。今より前を指定すると次のターン（アクティブ交代）。途中のステップは飛ばす |
| `turn_set` | `turn?, phase?, step?, active?, priority?` | 標準に無い進行（追加ターン、`first_strike_damage` など） |
| `priority` | `player` | 優先権を直接渡す |
| `pass` | `until?, player?, text?` | パスの宣言を記録する（人間との対戦で、求めたパスをもらったとき）。`until`: `stack` / `step` / `turn` で継続的なパス。全員が続けてパスすると `all_passed: true`。エンジンはこれで止まったり進んだりしない |
| `hold` | `player?` | 継続的なパスを取り消す |
| `declare` | `kind, text?, player?` | `keep` / `mulligan` / `no_block` など。`concede` は status も変える。`pass` は `pass` と同じ |

## カード参照

| 書き方 | 意味 |
|---|---|
| `"#c12"` / `"c12"` | id |
| `"$名前"` | 同じ Batch で `"as"` を付けた結果 |
| `["#c1", "#c2"]` | 複数 |
| `{"zone": "library", "top": 2}` / `{"bottom": 1}` | 上（下）から n 枚 |
| `{"zone": "p1.library", "index": 0}` | 位置（負数は下から） |
| `{"zone": "p2.hand", "random": 1}` | 無作為に n 枚 |
| `{"zone": "hand", "name": "Forest", "count": 1}` | 知っているカードのうち名前が一致するもの |
| `{"zone": "graveyard", "all": true}` | 全部 |

Player として操作するとき、中身を知らない非公開カードは id で指定できない。位置・無作為の指定は使える。
シャッフル後のライブラリーでは、覚えているカードでも位置が分からないので id 指定できない。

## Precondition

`pre` のすべてが成り立つときだけ適用する（状態の確認であって、ルールの合法性ではない）。

```json
{"version": 12}
{"step": "main"}
{"turn": 3, "phase": "main1", "active": "p1", "priority": "p1"}
{"card": "#c3", "zone": "p1.hand"}
{"card": "#c3", "zone": "battlefield", "tapped": false, "controller": "p1", "entered_at": 41}
{"zone": "p1.library", "top": ["#c9", "#c4"]}
{"zone": "p1.hand", "size": 7}
{"player": "p2", "life": 5}
{"counter": "#c3", "kind": "+1/+1", "amount": 2}
{"stack_size": 1}
{"stack_top": "#s4"}
```

知らないカードを前提に使うことはできない。

## エンジンが自動でやること

紙で物理的に起きることと、優先権の記録だけ。ルールの判定はしない（優先権で Batch を止めることもしない）。

- 領域を移ったカードは新しいオブジェクト: Counter・Note・Link が外れる（`keep: ["counters", "notes", "links"]` で残す）。
  Link はそのカードが source なら丸ごと、targets の1つならそれだけ外れる
- 戦場を離れたカード: アンタップ、表の面へ、戦闘から外れる、コントローラーは持ち主へ
- 別の領域へ移ったカードは表向きになる（`face_down: true` を明示しない限り）
- スタック領域からカードを動かすと、そのカードの StackItem も外れる
- `remove` したオブジェクトは Counter / Note / Link / Stack からも消える（スタック上の能力の `source` の id は残る）
- 優先権: `stack_push` で積んだ Player へ、`stack_remove` でアクティブ・プレイヤーへ、`step` で新しいステップの
  アクティブ・プレイヤーへ（アンタップとクリンナップは空）。`pass` で次の Player へ回し、継続的なパスの Player は自動でパス
- 継続的なパスの期限切れ（ステップ・ターンが変わった、スタックが空になった）

## 情報公開

- **Player View**: 相手の手札は枚数と既知のカード。ライブラリーは既知のカードを、位置が分かるもの
  （`known_positions`）と分からないもの（`known_unordered`）に分けて出す。知らない非公開カードは id も出さない
- **KnownInformation**: 見えた・公開された・`look` で見たカードは覚える。シャッフルで位置の記憶だけ消える
- **InformationPolicy**: `normal` / `own_library` / `all_libraries` / `omniscient`（一人回し・テスト用）
- カード名は表示では `<Name>` で囲む（view・ids・oracle・log --events。JSON の値には付けない）
- 表示の整理: 同じ状態のオブジェクトは `#t4..#t11 <Name> ×8` の1行、同じ内容の Note は `×N` にまとめる。
  Link はカードの行に `[attached->#c28]`（source 側）/ `[attached<-#c130]`（targets 側）と出る。付いている先が
  違うものはまとめない
- ライブラリー・墓地は既定では枚数だけ（`library (44, known positions: 3)`）。中身は `--library` / `--graveyard`
- 戦場は土地を先に並べる。カードは印刷されたタイプ行（`type_line`。`new` がデッキのオラクル・キャッシュから入れる。
  トークンは `definition.type_line`、コピーは元から）を持ち、表示の `land` と `sick` にだけ使う
- **`sick`（召喚酔いの目安）**: 戦場のクリーチャー（タイプ不明と裏向きも含む）が、コントローラーの直近のターンの
  開始より後にコントロールされ始めたときに付く。コントロールが変わるとやり直し。**速攻は考えない**
  （攻撃・`{T}` できるかは AI が判断する）。ターンの開始は `step` で新しいターンに入ったときと、`turn_set` で
  `turn` / `active` を変えたとき
