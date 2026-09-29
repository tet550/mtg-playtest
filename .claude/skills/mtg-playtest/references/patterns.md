# よくある処理の書き方

MTG の処理を mtgtable の Operation に落とす定型。id・名前は例（架空の対局）。
各例は1つの ActionGroup の `ops`（`{"op": ...}` の `"op":` は省かず書く）。
**よく使う手順は複合 op（`turn_start` `turn_end` `land` `cast` `push_resolve` `pay`）で1手に書く。** 基本の op は、
複合 op で書けない所（マナを浮かせる、解決の途中で止めて相手に聞くなど）に使う。
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

T1 も同じ骨組み（ゲーム前から先攻の T1 に入る。ゲームの最初のターンは引かない）。

```json
{"label": "T5 開始", "pre": [{"turn": 4, "step": "cleanup"}], "ops": [{"op": "turn_start", "to": "main1"}]}
{"label": "T5 終了", "pre": [{"step": "main"}], "ops": [{"op": "turn_end"}]}
```

- 引いたカードを見てから次を決めるなら、`to` を省いてドロー・ステップで区切る
- アップキープの誘発: `turn_start {upkeep: [{"op": "push_resolve", "source": "#c7", "text": "..."}]}`
- 終了ステップの誘発（「次の終了ステップに生け贄」など）: `turn_end {end: [...]}`
- 手札が8枚以上なら `turn_end {cleanup: [{"op": "move", "card": "#c3", "to": "graveyard"}]}`
- 相手が応答できないなら、相手の Batch で先に `{"op": "pass", "until": "turn"}` を入れておく
- 中身は `step untap` → `untap_all` → `step upkeep` → `step draw` → `draw`、終了は `step end` → `step cleanup` →
  `note_remove until=end_of_turn` → `mana_clear`

## 土地

土地を出すときは、今出せるマナを Note に書く（名前無しの view でも読めるように）。条件で変わるものは
条件が変わったときに `note_update` する。

```json
{"ops": [{"op": "land", "card": "#c69", "mana": "{W} ({R} needs a Mountain or Plains)", "note_as": "m69"}]}
{"ops": [{"op": "land", "card": "#c9", "mana": "{R}"},
         {"op": "note_update", "note": "$m69", "text": "mana: {W} or {R}"}]}
```

| 土地の例 | Note の書き方 |
|---|---|
| 基本土地・ショックランド | `mana: {R}` / `mana: {R} or {W}` |
| Cavern of Souls | `mana: {C}; any color for Dwarf creature spells (uncounterable)` |
| Starting Town | `mana: {C}; any color for 1 life` |
| Verge 系 | 条件を満たす前は片方だけ、満たしたら両方 |

タップイン（入った時点でタップ）は `land {tapped: true}`。フェッチなどの起動型能力も Note に書いておく。

## 呪文

**唱える → 解決**

```json
{"ops": [{"op": "cast", "card": "#c40", "pay": {"#c10": "G", "#c11": "G"}}]}
{"ops": [{"op": "cast", "card": "#c41", "pay": {"#c12": "R"}, "targets": ["p2"],
          "then": [{"op": "life", "player": "p2", "amount": -3}]}]}
```

- 行き先はタイプ行から決まる（インスタント・ソーサリーは墓地、他は戦場）。両面・出来事などは `to` を書く
- `then` は解決時の処理（`stack_remove` の前に適用）。対象を取る呪文は `targets`（`target` Link が張られ、解決で外れる）
- 相手の応答を待つ（人間にパスを求める）なら `cast {resolve: false, as: "x"}` で積むだけにして区切り、
  次の Batch で解決の処理と `stack_remove {item: ...}` を書く
- Starting Town のライフ: `pay {"#c76": "R", "life": 1}`。浮いているマナを使う: `pay {"pool": "U"}`
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
  {"op": "cast", "card": "#c40", "pay": {"#c10": "G", "#c11": "G"}},
  {"op": "push_resolve", "source": "#c40", "text": "When this enters, draw a card", "then": [{"op": "draw"}]}]}
{"ops": [{"op": "push_resolve", "kind": "activated", "source": "#c12", "text": "+1/+1 counter",
          "pay": {"#c42": "R", "#c113": "B"}, "then": [{"op": "counter_add", "target": "#c12", "kind": "+1/+1"}]}]}
```

- 引いたカード・見たカードで次を決めるなら、`then` の後で Batch を区切る
- 解決の途中で相手が選ぶ（ディスカードなど）なら `pay` と基本の `stack_push` で積んで区切る

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
| サーチ | `search {name: "Swamp", to: hand, reveal: true}`（シャッフル込み。戦場へタップ状態なら `to: battlefield, tapped: true`、2枚なら `count: 2`、候補が複数なら `name: ["Swamp", "Mountain"]`）。何も持ってこないなら `shuffle` だけ |
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
