# よくある処理の書き方

MTG の処理を mtgtable の Operation に落とす定型。id・名前は例（架空の対局）。
各例は1つの ActionGroup の `ops`（`{"op": ...}` の `"op":` は省かず書く）。
ルールの要点は [rules.md](rules.md)、Operation の仕様は [cli.md](cli.md)。

## 目次

- [ターンの骨組み](#ターンの骨組み)
- [呪文](#呪文)
- [能力](#能力)
- [対象・付ける・追放する](#対象付ける追放する)
- [トークン・コピー](#トークンコピー)
- [P/T・ダメージ・破壊](#ptダメージ破壊)
- [ライブラリー・手札](#ライブラリー手札)
- [戦闘](#戦闘)
- [ゲームの終わり](#ゲームの終わり)

## ターンの骨組み

T1 も同じ骨組み（ゲーム前から `step to=untap` で先攻の T1 に入る）。先攻は `draw` を書かない。

```json
{"label": "T5 開始", "pre": [{"turn": 4, "step": "cleanup"}], "ops": [
  {"op": "step", "to": "untap"}, {"op": "untap_all"},
  {"op": "step", "to": "upkeep"},
  {"op": "step", "to": "draw"}, {"op": "draw"}]}
{"label": "T5 終了", "pre": [{"step": "main"}], "ops": [
  {"op": "step", "to": "end"}, {"op": "step", "to": "cleanup"},
  {"op": "note_remove", "until": "end_of_turn"}, {"op": "mana_clear"}]}
```

- 相手が応答できないなら、相手の Batch で先に `{"op": "pass", "until": "turn"}` を入れておく
- アップキープの誘発があれば `step to=upkeep` の後で積む（下の「誘発型能力」）
- 手札が8枚以上ならクリンナップで捨てる（`move ... to graveyard`）

## 土地

土地を出すときは、今出せるマナを Note に書く（名前無しの view でも読めるように）。条件で変わるものは
条件が変わったときに `note_update` する。

```json
{"ops": [
  {"op": "move", "card": "#c69", "to": "battlefield"},
  {"op": "note_add", "target": "#c69", "text": "mana: {W} ({R} needs a Mountain or Plains)", "as": "m69"}]}
{"ops": [
  {"op": "move", "card": "#c9", "to": "battlefield"},
  {"op": "note_add", "target": "#c9", "text": "mana: {R}"},
  {"op": "note_update", "note": "$m69", "text": "mana: {W} or {R}"}]}
```

| 土地の例 | Note の書き方 |
|---|---|
| 基本土地・ショックランド | `mana: {R}` / `mana: {R} or {W}` |
| Cavern of Souls | `mana: {C}; any color for Dwarf creature spells (uncounterable)` |
| Starting Town | `mana: {C}; any color for 1 life` |
| Verge 系 | 条件を満たす前は片方だけ、満たしたら両方 |

タップイン（入った時点でタップ）は `move {tapped: true}`。フェッチなどの起動型能力も Note に書いておく。

## 呪文

**唱える → 解決（パーマネント）**

```json
{"ops": [
  {"op": "tap", "cards": ["#c10", "#c11"]},
  {"op": "mana_add", "color": "G", "source": "#c10"}, {"op": "mana_add", "color": "G", "source": "#c11"},
  {"op": "mana_spend", "color": "G", "amount": 2},
  {"op": "stack_push", "card": "#c40", "as": "bear"}, {"op": "pass"}]}
{"ops": [{"op": "stack_remove", "item": "$bear", "card_to": "battlefield"}]}
```

- インスタント・ソーサリーは `card_to: "graveyard"`
- 対象を取る呪文は `stack_push {targets: ["#c7"]}`（`target` Link が張られ、解決で外れる）
- 打ち消し: 打ち消す呪文を積んで解決したら、打ち消された側を `stack_remove {item: "#s3", card_to: "graveyard"}`
- 追加コストの生け贄（Rottenmouth Viper など）: `stack_push` の前に `move ... to graveyard`

**相手の対応**: エンジンは優先権では止めない。AI 同士なら、相手として対応するかを判断し、対応するなら
相手の呪文を積んでから解決する。人間が相手なら、クリティカルな場面でパスを求め、宣言（`pass`）をもらってから
解決する（もらわずに進めた場合は巻き戻しの請求を受ける。`undo --to N`）。

## 能力

**マナ能力**（スタックに積まない）

```json
{"ops": [{"op": "tap", "card": "#c62"},
         {"op": "mana_add", "color": "C", "amount": 2, "source": "#c62", "note": "abilities only"}]}
```

**起動型（コストに生け贄）**: 生け贄は墓地へ動かしてから、そのカードを source にして積む。トークンは積んだ後に消す

```json
{"ops": [
  {"op": "move", "card": "#t2", "to": "graveyard"},
  {"op": "stack_push", "kind": "activated", "source": "#t2", "text": "put a +1/+1 counter on target creature", "targets": "#c132", "as": "ab"},
  {"op": "remove", "card": "#t2"},
  {"op": "pass"}]}
```

**誘発型能力**（ETB・生け贄時など）: 誘発の元が起きた ActionGroup の中で積む

```json
{"ops": [
  {"op": "stack_remove", "item": "$bear", "card_to": "battlefield"},
  {"op": "stack_push", "kind": "triggered", "source": "#c40", "text": "When this enters, draw a card", "as": "etb"},
  {"op": "pass"}]}
{"ops": [{"op": "draw"}, {"op": "stack_remove", "item": "$etb"}]}
```

- 同時に複数誘発したら、アクティブ・プレイヤーの分 → 相手の分の順で積む。自分の分の順は積む順で決める
- 選択肢のある能力（「〜してもよい」「1つを選ぶ」）は、選んだ内容を `text` に書く

## 対象・付ける・追放する

| 関係 | 書き方 |
|---|---|
| オーラ・装備 | 戦場に出してから `{"op": "link_add", "kind": "attached", "source": "#c20", "targets": "#c40"}` |
| 付け替え | `link_remove {object: "#c20", kind: "attached"}` → `link_add` |
| 「〜を追放する。〜が戦場を離れたとき戻す」 | **追放した後に** `link_add {kind: "exiled_by", source: 追放元, targets: 追放したカード}` |
| 「〜と共に追放されたカード」を参照 | 同上の Link を `kind` で探す |

- 追放元が戦場を離れると Link は外れ、結果の `links_removed` に中身が返る。戻す誘発を積むなら、
  そこから対象を取って `stack_push {targets: [...]}`
- 付けられていたクリーチャーが戦場を離れたら、オーラは状況起因処理で墓地へ（AI が `move`）

## トークン・コピー

```json
{"op": "create", "name": "Treasure", "definition": {"type_line": "Token Artifact — Treasure", "text": "{T}, Sacrifice this token: Add one mana of any color."}}
{"op": "create", "copy_of": "#c138", "definition": {"haste": true, "sacrifice": "next end step"}, "as": "tok"}
{"op": "create", "name": "Saproling", "definition": {"type_line": "Token Creature — Saproling", "power": 1, "toughness": 1}, "count": 3}
```

- コピーは `copy_of` で元を参照するだけ。コピーにだけ付く性質は `definition` に足す
- Treasure を使う: `move {card: "#t1", to: "graveyard"}` → `mana_add` → `remove {card: "#t1"}`
- 「次の終了ステップに生け贄」などの遅延誘発は、その時点で AI が積む（終了ステップの骨組みに入れる）

## P/T・ダメージ・破壊

```json
{"op": "note_add", "target": "#c33", "text": "+2/+2", "until": "end_of_turn", "as": "pt"}
{"op": "note_update", "note": "$pt", "text": "+20/+20"}
{"op": "counter_add", "target": "#c33", "kind": "+1/+1", "amount": 3}
{"op": "note_add", "target": "#c33", "text": "damage 3", "until": "end_of_turn"}
{"op": "move", "card": "#c33", "to": "graveyard"}
```

- 同じ効果が重なったら Note を増やさず `note_update` でまとめる
- ダメージは Note で記録し、致死なら状況起因処理として墓地へ `move`
- プレイヤーへのダメージ・ライフの喪失・回復は `life {amount: -3}`

## ライブラリー・手札

| 処理 | 書き方 |
|---|---|
| 占術 N | `look {cards: {zone: library, top: N}}` → 見てから `move ... position: bottom`（上に残す順は `move ... position: top`） |
| 諜報 N | `look` → `move ... to graveyard` |
| 切削 N | `move {cards: {zone: library, top: N}, to: graveyard}` |
| サーチ | `look {cards: {zone: library, all: true}}` → `move {card: {zone: library, name: "Swamp"}, to: ...}` → `shuffle`（何を取るか決めてから書けるなら1つの Batch で） |
| 手札を公開させる（Duress など） | `reveal {cards: {zone: p2.hand, all: true}, to: p1}` → 見てから選ぶ（公開の所で Batch を区切る） |
| 相手が選んで捨てる | 相手として判断し、その Player の Batch で `move ... to graveyard` |
| 上から N 枚を見て1枚を手札、残りを下に無作為の順で | `look {top: N}` → `move {card, to: hand}` → `move {cards: [...], to: library, position: bottom, order: random}` |
| 無作為に捨てる | `move {card: {zone: p2.hand, random: 1}, to: graveyard}` |
| 墓地から手札・戦場へ | `move {card: "#c8", to: hand}` |

## 戦闘

```json
{"pre": [{"step": "main"}], "ops": [
  {"op": "step", "to": "beginning_of_combat"}, {"op": "step", "to": "declare_attackers"},
  {"op": "attack", "attackers": ["#c66", "#c132"], "target": "p2", "tap": true},
  {"op": "step", "to": "declare_blockers"}]}
```

- 攻撃時の誘発はこの後で積む。ブロックは防御側として判断する（パスではないので必ず聞く）:
  `{"op": "block", "blocker": "#c98", "attacker": "#c66"}` またはブロックしない `declare {kind: "no_block"}`
- 人間が相手で、ブロックできるクリーチャーがいない（選択の余地が無い）ときは、同じ Batch に代理で書いて
  ダメージまで進めてよい（SKILL.md の「代理の宣言」）:
  `{"proxy": "p1", "label": "ブロック無し（アンタップのクリーチャーなし）", "ops": [{"op": "declare", "kind": "no_block"}]}`
- ダメージ: `step to=combat_damage` → `life` / ダメージ Note → 状況起因処理 → `step to=end_of_combat` → `combat_clear`
- 攻撃しないなら戦闘を飛ばして `step to=main2`

## ゲームの終わり

```json
{"pre": [{"player": "p2", "life": 0}], "ops": [
  {"op": "player_set", "player": "p2", "status": "lost"},
  {"op": "player_set", "player": "p1", "status": "won"}]}
```

投了は `declare {kind: "concede"}`（status が `conceded` になる）。
