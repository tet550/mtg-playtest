# AI のプレイ方式と記録の設計メモ

[basic_design.md](basic_design.md) を補う検討メモ。AI のプレイ判断の効率化（トークン量・思考量）と、
処理の単位（19〜21節 Operation / Act / Batch）の整理、
GUI・サーバー化（24〜27節）を見据えた記録（29節 Operation Log）の形を扱う。

---

# 1. 前提

- 今の運用は、1つの会話が両方の Player を担当する（サブエージェントを使わない）。
  相手の非公開情報は「見えるが判断に使わない」という紳士協定で扱う
- 将来は GUI・サーバーで動かす。人間は自分の画面だけを見て、AI は判断ごとに API で呼ばれる
  （毎回コンテキストが新しい）形を想定する
- この2つで前提が変わる項目は、両方の場合を書く

---

# 2. 方針の決定とコマンドへの変換

## 決定

- **別のエージェント（LLM どうし）で分けない。**
  変換役も毎回 `cli.md`・`patterns.md`・view を読み直すことになり、入力が大きく増える。
  減るのは方針役の出力（Batch の JSON、1ターン数百トークン程度）だけで、釣り合わない。
  変換にも判断が残る（対象、マナの払い方、Note、どこで `apply` を区切るか）ので、
  思考量も減らない。判断が変換役に移るだけになる
- **同じ会話の中で「方針 → Batch」の順に書くのは有効。** Act のラベルが方針の役を果たしている
- **変換は AI ではなくエンジン（Python）に任せる。** 複合 op（`cast` / `land` / `pay`）と手順（`turn_start` /
  `turn_end`）の路線を進め、AI に残るのは判断だけにする

## 次の作業

- 対局ログから、AI が毎回同じように書いている定型を拾い出し、複合 op の候補にする
  （土地を出すときのマナの Note、攻撃宣言、生け贄から能力を積んで `remove` する流れなど）
- 方針に近い短い書き方（例: `land #c3; cast #c12 -> #c40; turn_end`）を Python で展開する形を検討する。
  GUI の人間の操作と AI の操作をそろえる共通の形式の候補にもなる

---

# 3. モデルの選び方

| 案 | 今の会話の運用 | サーバー（判断ごとに API） |
|---|---|---|
| 1局をまるごと安いモデル（Sonnet など）で回す | ◎ まず試す | ○ |
| 局面によってモデルを切り替える | ✗ 切り替えるたびにキャッシュが効かなくなる | ◎ 判断ごとに選べる |
| 方針は上位モデル、変換は安いモデル | △（2章のとおり） | 不要（変換はエンジン） |
| effort（思考量）を局面で下げる | ○ | ○ |

- 安いモデルで足りる所: Batch を書く、土地を置いてパスするだけのターン、定型の Note
- 足りない所: ルールの判定、カードの解釈、戦闘の読み、マリガンの判断
- 一番怖いのは、気づかないルール違反（テスト結果が信用できなくなる）。
  `precondition_failed` はエンジンが止めるので、やり直しの手間で済む
- 変換をエンジンに任せるほど、安いモデルでも成り立ちやすくなる

## 確かめ方

`fork` / `replay` で同じ局面を用意し、モデル別・effort 別に次を比べる:
使ったトークン量、`failed` / `precondition_failed` の回数、ルール違反の数（後で確認）、プレイ判断の質。

---

# 4. 計画を立てるタイミング

- **新しい情報が入ったとき（`learned` に新しいカードが出た、相手が動いた）だけ**、その Player の計画を
  1〜3行で書き直す（このターンの狙い / 次のターン以降の狙い / 見直す条件）
- 情報が増えない区切りでは書かない。区切りは `apply` の境目とそろっているので、往復は増えない
- 改善が見込める誤り: 手順（マナ・土地を置く順・ドローを見る前に決めてしまう）、
  複数ターンにまたがる狙いのぶれ。ルール違反は減らない（別の確認リストで防ぐ）
- 古い計画に引きずられないよう、見直す条件を必ず書く

## 計画の置き場所

| 運用 | 置き場所 |
|---|---|
| 今の会話の運用（AI 同士・人間 vs AI） | `apply` の前に会話の本文で書く。人間 vs AI でも、AI の view の生出力が見える以上、紳士協定の範囲で同じ扱い |
| サーバー（判断ごとに API） | **Player ごとの非公開メモ（`memo`）**。会話が残らないので、AI の記憶はゲームの状態の中に置くしかない |

- 今の Note には公開範囲が無い（view にも log の event にも本文が出る）。計画は Note に置かない
- `memo` は Card に付ける Note を拡張するのではなく、Player 単位の別の仕組みにする。
  本人だけに見え、log の event では本文を伏せ、`undo` すると一緒に戻る
- トークンの面でも、毎回「view＋memo」だけを渡せば済み、長い履歴を持ち回るより安い

---

# 5. 処理の単位

## 5.1 Act は「効果の1セット」

**Act は、ルール上一体として行う処理の1セット**。応答のタイミング（優先権）で区切るのではない。

- 呪文を唱える（601.2: スタックへ移す → モード等の宣言 → 対象 → 総コスト → マナ能力 → 支払い）。順番はルールで一意
- 呪文・能力の解決（608: 効果を順に実行し、最後にスタックから取り除く）。途中に優先権は入らない
- ステップの開始とターン起因処理（ドロー、攻撃の宣言）、宣言（キープ・マリガン・パス）
- 「カードを1枚捨てる。そうしたなら、カードを1枚引く」のように前の結果に依存する処理
- 優先権を得る時点は、必ず Act と Act の間にある。だから、応答の請求の戻り先にもなれる
- どこまでが1セットかはカードのテキストとルールで決まる。当てはめるのは AI（エンジンはルールを判定しない）

Act は `undo` の単位で、ふつうは丸ごと適用するか丸ごと取り消す単位・log の1件も兼ねる（見てから選ぶ Act は複数のパートに分かれる。5.5）。

## 5.2 単位と書き方

| 単位 | 書き方 | 役割 | 丸ごと適用・取り消し | log | undo |
|---|---|---|---|---|---|
| op（基本の op） | `{"op": ...}` | 卓の状態を1つ変える最小の操作。event を出す | — | — | 単位にしない |
| 複合 op | `{"op": "cast" / "land" / "pay", ...}` | 1つの Act の中で使う書き方の省略 | — | — | 単位にしない |
| **Act** | `{"act": [op, ...]}` | 効果の1セット（5.1） | ○ | 1件 | **undo の単位** |
| 手順 | `{"proc": "turn_start" / "turn_end", ...}` | 複数の Act の並びの省略 | 単位にしない | 各件に手順の情報 | 単位にしない |
| Batch | `{"actor", "label", "acts": [...]}` | 往復を減らすための実行の単位。次に判断が要る所までをまとめる | 失敗した Act の手前まで適用 | 各件に Batch の番号 | 単位にしない |

- op は必ず Act の中に書く（略記は持たない）。`acts` の要素は Act か手順のどちらか
- Batch はゲーム上の意味を持たない（[basic_design.md](basic_design.md) 21節）。「1ターン1回の `apply`」は
  Batch の目標で、Act の目標ではない
- 名前: 総合ルールでもターン起因処理（turn-based action）や特別な処理（special action）など、この単位を
  action と呼ぶ。「effect」は効果だけを指し、コストの支払い・ターン起因処理・宣言を含まないので使わない

## 5.3 唱える・解決・打ち消し

- **唱える**は複合 op `cast`（1つの Act）: `stack_push`（スタックへ移し、対象を張る）→ `pay` → `cost` の op
  （追加コストの生け贄など）。601.2 の順（スタックへ移して対象を選んでから払う）に合わせる
- **解決**は、効果の op の後に `stack_remove` を書く Act。解決できるのは一番上だけなので `item` は省く。
  呪文のカードの行き先は、`card_to` を省くとタイプ行から決まる（パーマネントは戦場、インスタント・ソーサリーは墓地）
- **打ち消し**は、打ち消す呪文の解決の Act の中で、打ち消される項目を `stack_remove {item, card_to: graveyard}` で
  取り除くこと。消すのは cast（過去の Act。消すのは `undo`）でも効果（まだ起きていない）でもなく、スタックの項目
- **能力**は `pay` と `stack_push` で積む Act と、解決の Act

## 5.4 手順の展開

手順は、**適用する直前に、そのときの状態で Act の並びに展開する**。展開した Act を1つずつ、
普通の Act と同じように適用する。

| 手順 | 展開した Act の並び |
|---|---|
| `turn_start` | アンタップ・ステップ（`step untap` → `untap_all`）→ アップキープの開始 → `upkeep` の各 Act → ドロー・ステップ（`step draw` → `draw`）→（`to` があれば）そのステップへ |
| `turn_end` | 終了ステップの開始 → `end` の各 Act → クリンナップの開始 → `cleanup` の各 Act → `note_remove` と `mana_clear` |

- `upkeep` / `end` / `cleanup` は `acts` と同じ並び（Act か手順）
- 実行時に決まる値（`turn_start` の引く枚数）は、展開するときの状態で決まる
- 失敗したときは、失敗した Act だけを取り消し、Batch はそこで止まる。前の Act は残る
- 実装: `apply_batch` は Act の待ち行列。先頭が手順なら、そのときの状態で展開して待ち行列の先頭に戻す。
  展開の関数は `procedures.py`。展開した Act には `proc`（`"turn_start to=main1 2/4"`）を付け、結果と log に残す

## 5.5 見てから選ぶ Act（cont）

解決の途中で、新しく見た情報で選ぶ（「2枚引く。その後2枚捨てる」、占術、<Thoughtseize>）、または相手が選ぶ
（勅令、<Fact or Fiction>）とき、Act は丸ごと適用されるので1回の `apply` に収まらない。

**Act をパートに分け、続くパートの前に `cont: true` を付ける。**

```json
{"act": [{"op": "draw", "count": 2}], "cont": true}
{"act": [{"op": "move", "cards": ["#c54", "#c55"], "to": "graveyard"}, {"op": "stack_remove", "card_to": "exile"}]}
```

- `cont: true` は「この Act は次のパートに続く」。`cont` の無いパートで Act が終わる。印はこの1つだけ
- パートは1つずつ適用・取り消しされ、log の1件・`version`・乱数を1つずつ持つ。Replay は今のまま
- Act のまとまりは log の並び（`cont` の件とその次の件）だけで決まる。GameState には何も足さず、ID も持たない
  （開いている Act はルール上いつも1つなので、一意に決まる。外から指したくなったら最初のパートの seq を使う）
- `undo` は Act 単位。`undo --to N` で Act の途中を指したら、その Act の始まりまで戻す。`redo` も Act 単位
- log は続くパートを `…` でつなげて表示する
- 続きの書き忘れはエンジンでは確かめない（`cont` の次の件は、何であれ同じ Act の続きになる）。起きても log のまとめ方と
  `undo` の戻り先がずれるだけで、盤面は壊れない

## 5.6 書き方（トークンを増やさない）

- Act のラベルは任意。報告用の説明は Batch に1つ付ける（Batch の `label`。log の最初の件に Batch の番号と一緒に残す）
- `apply` の結果は Act 1つを1行で出す（Act が増えても字下げで量を増やさない）
- `log --batches [N]` で Batch ごとに1行の表示。報告に使う量を Act の数によらず保つ

## 5.7 入力とログの例（<Shock> を <Counterspell> で打ち消す）

人間 vs AI（p1 が <Shock>、p2 が <Counterspell>）。p1 の T1 のメイン・フェイズ。

**1. p1: <Shock> を唱えて、相手の応答を待つ**（唱える Act の後で区切る）

```json
{"actor": "p1", "label": "T1: <Shock> を p2 に", "acts": [
  {"proc": "turn_start", "to": "main1"},
  {"act": [{"op": "cast", "card": "#c84", "targets": ["p2"], "pay": {"#c8": "R"}}]}]}
```

```text
{"actor": "p1", "applied": 5, "version": 7, "stopped": null, "acts": [
 {"status": "applied", "proc": "turn_start to=main1 1/4", "results": [...]},
 ...
 {"status": "applied", "created": ["#s1", "#m1"], "results": [{"card": "#c84", "item": "#s1"}]}
]}
```

**2. p2: <Counterspell> で対応し、パスする**（対象は <Shock> のスタックの項目 `#s1`）

```json
{"actor": "p2", "label": "<Counterspell> で <Shock> を打ち消す", "acts": [
  {"act": [{"op": "cast", "card": "#c49", "targets": ["#s1"], "pay": {"#c98": "U", "#c115": "U"}}]},
  {"act": [{"op": "pass"}]}]}
```

**3. p1: パスする**（p1 の宣言をもらって記録する）

```json
{"actor": "p1", "acts": [{"act": [{"op": "pass"}]}]}
```

**4. p2: <Counterspell> の解決**（打ち消される <Shock> を墓地へ → 一番上の <Counterspell> 自身を取り除く）

```json
{"actor": "p2", "label": "<Counterspell> の解決", "acts": [
  {"act": [{"op": "stack_remove", "item": "#s1", "card_to": "graveyard"}, {"op": "stack_remove"}]}]}
```

**log**（`log`）:

```text
     B2    T1: <Shock> を p2 に
    3 v3    p1    [turn_start to=main1 1/4] step to=untap; untap_all player=active
    4 v4    p1    [turn_start to=main1 2/4] step to=upkeep
    5 v5    p1    [turn_start to=main1 3/4] step to=draw
    6 v6    p1    [turn_start to=main1 4/4] step to=main1
    7 v7    p1    cast #c84 targets=p2
     B3    <Counterspell> で <Shock> を打ち消す
    8 v8    p2    cast #c49 targets=#s1
    9 v9    p2    pass
   10 v10   p1    pass
     B5    <Counterspell> の解決
   11 v11   p2    stack_remove #s1 card_to=graveyard; stack_remove
```

**log --batches**（報告用）:

```text
       3-7 v7    p1    T1: <Shock> を p2 に
       8-9 v9    p2    <Counterspell> で <Shock> を打ち消す
        10 v10   p1    pass
        11 v11   p2    <Counterspell> の解決
```

**log --events**（審判用。解決の Act）:

```text
   11 v11   p2    stack_remove #s1 card_to=graveyard; stack_remove
            stack remove #s1
            move #c84 <Shock>: stack -> p1.graveyard
            stack remove #s2
            move #c49 <Counterspell>: stack -> p2.graveyard
```

- 応答の請求の戻り先は Act の境目。たとえば p2 が「<Shock> の前に動きたかった」なら `undo --to 6`
- エイリアスは Batch の中だけで有効なので、Batch をまたぐ参照（`#s1`）は id で書く

---

# 6. 記録（Operation Log）

## 6.1 log は公開情報ではない

今の log（`log.jsonl`）は審判のもの。`replay` / `undo` の正本で、非公開の情報を含む:

- `--events`: 全情報（README でも「AI には見せない」）
- op の要約: マリガンで下に置いたカード、占術の振り分け、サーチで選んだカードの id。id を追えば推測できる
- ラベル: AI の自由な文章。公開の宣言と非公開の意図が混ざる

## 6.2 3つの層に分ける

| 層 | 見る人 | 中身 | 作る者 |
|---|---|---|---|
| Operation Log | 審判、`replay`、`undo` | 全部の op と全部の event | スクリプト |
| 対局の記録（新規） | 見る人ごと | event を公開範囲で絞ったもの | スクリプト（event から自動で作る） |
| Player の memo（新規） | 本人だけ | 計画・意図 | AI |

- 対局の記録は、event に「誰に見えるか」を付けて作る。view の `--as` と同じく、
  knowledge / `can_reference` の仕組みを使う。例: `draw` は本人には名前付き、相手には「1枚引いた」
- ラベルは公開してよいか機械で判断できないので、審判用の log にだけ残し、対局の記録には出さない。
  公開の宣言はやがて GUI の決まった操作（パス・ブロック宣言など）に置き換わる
- 今の CLI の報告に使う `log --last N` は、将来この対局の記録に置き換える

## 6.3 中身は「処理した基本の op ごと」に書く（実装済み。公開範囲は未実装）

**要件: log の各件から、実際に適用した基本の op（プリミティブ）を再現できること。** Replay はそれだけを使う。

1件の単位は **Act**（効果の1セット、undo の単位。5章）。`undo --to N` の意味も変えない。

```json
{"seq": 7, "version": 7, "time": "...", "batch": 2, "actor": "p1", "label": "", "pre": [],
 "act": [{"op": "cast", "card": "#c84", "targets": ["p2"], "pay": {"#c8": "R"}}],
 "steps": [
   {"op": {"op": "stack_push", "card": "#c84", "targets": ["p2"]}, "parent": "cast",
    "events": ["move #c84 <Shock>: p1.hand -> stack", "stack push #s1 spell card=#c84 source=None ",
               "link #l1 target: #s1 -> ['p2']"]},
   {"op": {"op": "tap", "card": "#c8"}, "parent": "cast", "events": ["set #c8 <Mountain> {'tapped': True}"]},
   {"op": {"op": "mana_add", "color": "R", "amount": 1, "source": "#c8"}, "parent": "cast", "events": ["..."]},
   {"op": {"op": "mana_spend", "mana": "#m1", "amount": 1}, "parent": "cast", "events": ["..."]}
 ]}
```

- `act`: AI が書いたそのままの op（表示用。Replay には使わない）
- `steps`: 実際に適用した基本の op。複合 op（`pay` / `cast` / `land`）は展開し、エイリアス（`$名前`）は id に置き換え、
  `as` は落とし、`player: "active"` はその時点の Player にする。`replay` はこれだけを使う
- `parent`: どの複合 op を展開したものか。手順から展開した Act には、件の側に `proc` が付いている
- `events`: その op が出した event（全情報。審判用）
- 記録は `operations.apply_operation` の1か所で行う（複合 op 以外が呼ばれたら、その op と増えた event を積む）
- カードの指定方法（`{"zone": ..., "top": 3}` など）と乱数はそのまま残す。同じ状態から同じ順に適用すれば、
  同じカード・同じ乱数（`(seed, version)`）になる
- `log --steps` で基本の op を、`log --events` で event を表示する

利点:

- 複合 op・手順・エイリアスの実装を変えても、log の `replay` がずれない
- event を op ごとに持つので、公開範囲を op ごとに付けられる（6.2 の前提。未実装）
- GUI で1手ずつ再生・表示できる

コスト: `log.jsonl` が大きくなる（ローカルで、AI は直接読まないのでトークンには影響しない）。

## 6.4 保存は構造化、通知は begin / commit

- **保存する log は構造化（1行 = 1 Act）にする。** 1行を書けたかどうかで「丸ごと」が保証され、
  `undo` も行の単位で済む。エンジンは Act をメモリ上で最後まで適用し、成功したときだけ書く。
  1つの対局への書き手も1人なので、begin / commit の利点（少しずつ書く・書き手が混ざる）が効かない
- **GUI へ送る通知は begin / commit の形にする。** `act_begin` → op ごとの event（見る人ごとに絞ったもの）→
  `act_end` の順に流す。保存した log から作るもので、保存の形式とは別のもの

---

# 7. GUI・サーバー化で決めること

- **優先権の受け渡し**: 今の「対応なしと判断し進行」と「あとから巻き戻しを請求」は会話だから成り立つ。
  パスを求める、自動でパスする設定、タイムアウトを状態として持たせる
- **審判役と Player の AI を分ける**（27節）。トークンの理由ではなく、情報を隔てるための分離。
  審判は全情報を見て、Player の AI は自分の view と memo だけを見る
- **API**: `apply` / `view` / 対局の記録 / `memo` を見る人ごとに出す。Operation Log は審判用の API だけで出す
- **巻き戻しと乱数**（後で検討）: 乱数は `(seed, version)` で決まり、`undo` すると `version` も戻るので、
  やり直すと同じ乱数が出る。結果を見てから `undo` すると、その後の乱数の結果を知ったままやり直せる

---

# 8. 着手順

1. （済）Act（`{"act": [...]}`）と Batch の `acts`（5.2）
2. （済）`cast` を1つの Act の複合 op に（5.3）。手順は `turn_start` / `turn_end` だけ（5.4）。
   `apply_batch` を Act の待ち行列にする
3. （済）Batch のラベルと id を log に残し、Batch ごとの log の表示を作る。SKILL.md / cli.md / patterns.md の書き方の案内
4. （済）log の中身を「処理した基本の op ごと」にする（6.3）。残り: event に公開範囲を付ける
5. 見る人ごとの対局の記録を作る（6.2）。今の CLI でも、人間 vs AI の公平さが上がる
6. Player ごとの `memo`（4章）
7. （一部済）優先権の受け渡しの約束事（7章）: `waiting_on` と `wait`、GUI の自動パス（web_design「GUI の対局」）。
   自動でパスする設定・タイムアウトを状態に持たせるのは残り
8. （一部済）サーバーの API（7章）: ローカルの `serve --play`（席の鍵・apply・undo・409）。AI 用の HTTP 接続と
   審判の分離は残り

保留: ループのショートカット。案は、結果の op をまとめた Act に `"shortcut": {"loop": 手順の説明, "times": N}` を付けて
記録し、相手が `declare kind=shortcut_accept`（または割り込む回数）で受ける形（エンジンは記録だけ。量の計算は AI）。

並行して、複合 op の拡充（2章）と、モデル別・計画ルールの有無で `fork` を使って比べる検証（3・4章）を進める。

## 実装で気をつける点（公開範囲）

- **event に公開範囲を付けるのは別の作業。** 今の event は文字列で、`ctx.event(...)` の呼び出しが50か所ある
