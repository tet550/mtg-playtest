# よくある処理の書き方

MTG の処理を mtgtable の Operation に落とす定型。id・名前は例（架空の対局）。
各例は Batch の `acts` の要素（`{"act": [...]}` か `{"proc": ...}`）、または Act の中の op（`{"op": ...}`）。
**Act はルール上一体の処理の1セット**: 呪文を唱える、解決する（効果の op → `stack_remove`）、ステップを進める、
「1枚捨てる。そうしたなら1枚引く」など。1ターンを1つの Act に詰めない（1ターンを1つの Batch にするのはよい）。
よく使う流れは手順（`turn_start` `turn_end`）と複合 op（`cast` `land` `pay`）で書く。
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
{"actor": "p1", "label": "T5", "acts": [
  {"proc": "turn_start", "to": "main1", "pre": [{"turn": 4, "step": "cleanup"}]},
  ...,
  {"proc": "turn_end"}]}
```

- 引いたカードを見てから次を決めるなら、`to` を省いてドロー・ステップで区切る
- アップキープの誘発: `{"proc": "turn_start", "upkeep": [{"act": [積む]}, {"act": [効果, stack_remove]}]}`
- 終了ステップの誘発（「次の終了ステップに生け贄」など）: `{"proc": "turn_end", "end": [...]}`
- 手札が8枚以上なら `{"proc": "turn_end", "cleanup": [{"act": [{"op": "move", "card": "#c3", "to": "graveyard"}]}]}`
- 相手が応答できないなら、相手の Batch で先に `{"act": [{"op": "pass", "until": "turn"}]}` を入れておく
- 中身は `step untap` → `untap_all` → `step upkeep` → `step draw` → `draw`、終了は `step end` → `step cleanup` →
  `note_remove until=end_of_turn` → `mana_clear`（ステップごとに別の Act になる）

## 土地

土地を出すときは、今出せるマナを Note に書く（名前無しの view でも読めるように）。条件で変わるものは
条件が変わったときに `note_update` する。

```json
{"act": [{"op": "land", "card": "#c69", "mana": "{W} ({R} needs a Mountain or Plains)", "note_as": "m69"}]}
{"act": [{"op": "land", "card": "#c9", "mana": "{R}"},
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

**唱える → 解決**: 唱える Act と解決の Act は別。解決は効果の op の後に `stack_remove`（一番上を取り除く）

```json
{"act": [{"op": "cast", "card": "#c40", "pay": {"#c10": "G", "#c11": "G"}}]}
{"act": [{"op": "stack_remove"}]}
{"act": [{"op": "cast", "card": "#c41", "targets": ["p2"], "pay": {"#c12": "R"}}]}
{"act": [{"op": "damage", "target": "p2", "amount": 3, "source": "#c41"}, {"op": "stack_remove"}]}
{"act": [{"op": "move", "card": "#c5", "to": "graveyard"}, {"op": "draw"}, {"op": "stack_remove"}], "label": "捨てて引く"}
```

**解決の途中で、見てから選ぶ**（「2枚引く。その後2枚捨てる」など）: 見る所までのパートに `cont: true` を付けて Batch を
区切り、`learned` を見てから次の Batch で続きを書く。2つのパートで1つの Act（Undo も一緒に戻る）

```json
{"act": [{"op": "draw", "count": 2}], "cont": true}
{"act": [{"op": "move", "cards": ["#c54", "#c55"], "to": "graveyard"}, {"op": "stack_remove"}]}
```

- 行き先はタイプ行から決まる（インスタント・ソーサリーは墓地、他は戦場）。両面・出来事などは `card_to` を書く
- 対象を取る呪文は `targets`（`target` Link が張られ、解決で外れる）。モード・X は `text` に書く
- 相手の応答を待つ（人間にパスを求める）なら、唱える Act の後で Batch を区切る。解決は次の Batch で同じように書く
- Starting Town のライフ: `pay {"#c76": "R", "life": 1}`。浮いているマナを使う: `pay {"pool": "U"}`
- 打ち消し: 打ち消す呪文の解決の Act で、打ち消される項目を取り除く:
  `{"act": [{"op": "stack_remove", "item": "#s1", "card_to": "graveyard"}, {"op": "stack_remove"}]}`
- 追加コストの生け贄（Rottenmouth Viper など）: `cast {cost: [{"op": "move", "card": ..., "to": "graveyard"}]}`

**相手の対応**: エンジンは優先権では止めない。AI 同士なら、相手として対応するかを判断し、対応するなら
相手の呪文を積んでから解決する。人間が相手でも、対応が予想されない場面は確認を省略し、ラベルにその旨を残す。
対応の可能性があるクリティカルな場面ではパスを求め、宣言（`pass`）をもらってから解決する。
確認省略は明示的なパスと区別し、相手からの巻き戻し請求には `undo --to N` で対応する。

## 能力

**マナ能力**（スタックに積まない）

```json
{"act": [{"op": "tap", "card": "#c62"},
         {"op": "mana_add", "color": "C", "amount": 2, "source": "#c62", "note": "abilities only"}]}
```

**起動型**: コストを払って積む Act と、解決の Act

```json
{"act": [{"op": "pay", "#c42": "R", "#c113": "B"},
         {"op": "stack_push", "kind": "activated", "source": "#c12", "text": "+1/+1 counter"}]}
{"act": [{"op": "counter_add", "target": "#c12", "kind": "+1/+1"}, {"op": "stack_remove"}]}
```

**起動型（コストに生け贄）**: 生け贄は墓地へ動かしてから、そのカードを source にして積む。トークンは積んだ後に消す

```json
{"act": [
  {"op": "move", "card": "#t2", "to": "graveyard"},
  {"op": "stack_push", "kind": "activated", "source": "#t2", "text": "put a +1/+1 counter on target creature", "targets": "#c132"},
  {"op": "remove", "card": "#t2"}]}
{"act": [{"op": "counter_add", "target": "#c132", "kind": "+1/+1"}, {"op": "stack_remove"}]}
```

**誘発型能力**（ETB・生け贄時など）: 誘発の元の後に積み、解決する

```json
{"act": [{"op": "stack_remove"}]}
{"act": [{"op": "stack_push", "kind": "triggered", "source": "#c40", "text": "When this enters, draw a card"}]}
{"act": [{"op": "draw"}, {"op": "stack_remove"}]}
```

- 引いたカード・見たカードで次を決めるなら、見る所までのパートに `cont: true` を付けて Batch を区切る
- 解決の途中で相手が選ぶ（ディスカードなど）なら、相手が選ぶ前までのパートに `cont: true` を付けて区切り、
  相手の選択を続きのパート（相手の `actor` / `proxy`）で書く
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
{"op": "damage", "target": "#c33", "amount": 3, "source": "#c41"}
{"op": "move", "card": "#c33", "to": "graveyard"}
```

- 同じ効果が重なったら Note を増やさず `note_update` でまとめる
- ダメージは `damage`（パーマネントには `damage N` の Note がターン終了まで付く）。致死なら状況起因処理として墓地へ `move`
- ダメージ・ライフの喪失・ライフの獲得は別の op: `damage {target, amount, source}` / `life_loss {amount}` /
  `life_gain {amount}`（絆魂は `damage` と同じ Act に `life_gain`）。ライフの支払いは `pay {"life": N}`（喪失になる）
- 感染・萎縮・プレインズウォーカーなど結果が違うダメージは `damage {apply: false}` で与えた記録を残し、
  結果をカウンターの op で書く（例: 感染でプレイヤーへ → `counter_add {target: "p2", kind: "poison", amount: 2}`）
- 量が計算で負になったら 0。0 のダメージ・ライフは何も起きない（op を書かない）

## ライブラリー・手札

| 処理 | 書き方 |
|---|---|
| 占術 N | `look {cards: {zone: library, top: N}}`（`cont: true`）→ 見てから `move ... position: bottom`（上に残す順は `move ... position: top`） |
| 諜報 N | `look`（`cont: true`）→ `move ... to graveyard` |
| 切削 N | `move {cards: {zone: library, top: N}, to: graveyard}` |
| サーチ | `search {name: "Swamp", to: hand, reveal: true}`（シャッフル込み。戦場へタップ状態なら `to: battlefield, tapped: true`、2枚なら `count: 2`、候補が複数なら `name: ["Swamp", "Mountain"]`）。何も持ってこないなら `shuffle` だけ |
| 手札を公開させる（Duress など） | `reveal {cards: {zone: p2.hand, all: true}, to: p1}`（`cont: true`）→ 見てから選ぶ |
| 相手が選んで捨てる | 相手として判断し、その Player の Batch で `move ... to graveyard` |
| 上から N 枚を見て1枚を手札、残りを下に無作為の順で | `look {top: N}`（`cont: true`）→ `move {card, to: hand}` → `move {cards: [...], to: library, position: bottom, order: random}` |
| 無作為に捨てる | `move {card: {zone: p2.hand, random: 1}, to: graveyard}` |
| 墓地から手札・戦場へ | `move {card: "#c8", to: hand}` |

## 戦闘

```json
{"act": [{"op": "step", "to": "beginning_of_combat"}], "pre": [{"step": "main"}]}
{"act": [{"op": "step", "to": "declare_attackers"},
         {"op": "attack", "attackers": ["#c66", "#c132"], "target": "p2", "tap": true}]}
{"act": [{"op": "step", "to": "declare_blockers"}]}
```

- 攻撃時の誘発はこの後で積む。ブロックは防御側として判断する（パスではないので必ず聞く）:
  `{"op": "block", "blocker": "#c98", "attacker": "#c66"}` またはブロックしない `declare {kind: "no_block"}`
- 人間が相手で、ブロックできるクリーチャーがいない（選択の余地が無い）ときは、同じ Batch に代理で書いて
  ダメージまで進めてよい（SKILL.md の「代理の宣言」）:
  `{"proxy": "p1", "label": "ブロック無し（アンタップのクリーチャーなし）", "act": [{"op": "declare", "kind": "no_block"}]}`
- ダメージ: `step to=combat_damage` → 攻撃・ブロックしたクリーチャーごとに `damage`（`source` はそのクリーチャー）→
  状況起因処理 → `step to=end_of_combat` → `combat_clear`
- 攻撃しないなら戦闘を飛ばして `step to=main2`

## ゲームの終わり

```json
{"pre": [{"player": "p2", "life": 0}], "act": [
  {"op": "player_set", "player": "p2", "status": "lost"},
  {"op": "player_set", "player": "p1", "status": "won"}]}
```

投了は `declare {kind: "concede"}`（status が `conceded` になる）。
