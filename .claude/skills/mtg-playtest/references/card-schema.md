# カード・オラクル情報の保存形式（mtg-playtest/card@2）

`scripts/cardcache.py` が読み書きするローカルキャッシュの規定。
**この文書とコードが食い違ったらコードが正**（`cardcache.validate()` が唯一の検査系）。

## 目的と原則

0. **内部はすべて英語名で扱う。** 正本のファイル名も `name` も英語名。
   日本語名・略称・デッキリスト上の表記は**別名レコード**から英語名へ転送する。
   同じカードが表記ごとに別物として増えるのを防ぐため。人に見せるときは
   [日英対応表](#日英対応表) を使う

1. **使用時に取得し、一度取得したら二度と取りに行かない。** カード情報は事実上不変なので、
   ゲームのたびに Scryfall を叩く理由がない
2. **判定に使う値は英語で持つ。** タイプ・キーワードは英語の `type_line` / `keywords` から作る。
   日本語の印刷テキストは表示専用に別フィールドで持つ。これを混ぜると
   「クリーチャー」が `Creature` と一致せず、召喚酔い判定や土地セット数え上げが黙って壊れる
3. **1枚1ファイル。** 手で直せて、差分が読めて、書き込みが1枚に閉じる
4. **壊れたファイルは黙って使わない。** スキーマ検査に落ちたらキャッシュミス扱いにする

---

## 保存場所とファイル名

```
<cards-dir>/<slug>.json
```

- `<cards-dir>` … 既定 `cards`。`--cards-dir` または環境変数 `MTG_CARDS_DIR` で変更
- 対局の状態（`playtest/state.json`）とは**別ディレクトリ**。キャッシュは全ゲームで共有する

### slug の決定規則（`cardcache.slug`）

1. 参照名を `normalize_name` で正規化する
   - Unicode NFKC 正規化
   - 前後の空白を除去
   - 連続する空白を半角スペース1つに畳む
   - **大小文字は変えない**（表示に使うため）
2. `cache_key` = 正規化名を `casefold()` したもの。**同一性はこのキーで判断する**
3. ファイル名本体 = 正規化名のうち `\ / : * ? " < > |` と制御文字を `_` に置換し、
   空白を `_` にし、末尾の `.` と空白を落とし、先頭60文字に切る（空なら `card`）
4. `-` + `sha1(cache_key)` の先頭8桁を必ず付ける

```
稲妻                    → 稲妻-945f6793.json
Lightning Bolt          → Lightning_Bolt-4af10cb1.json
試作:炎の槍             → 試作_炎の槍-d3fd08e3.json
```

ハッシュを必ず付けるのは、使えない文字を落とした結果の衝突（`A/B` と `A_B` が
同じファイル名になる）を防ぐため。**名前からファイル名は一意に決まる**ので、
探索は不要で1回のパス計算で当たる。

---

## レコードのスキーマ

トップレベルは JSON オブジェクト。エンコードは **UTF-8**、`ensure_ascii=false`、
インデント1、**キーは辞書順**（`sort_keys=True`）で書く。差分を読みやすくするため。

### 必須フィールド

`schema` / `key` / `name` / `types` / `source` の5つ。欠けていたら不正とみなす。

### 全フィールド

| フィールド | 型 | 意味・規則 |
|---|---|---|
| `schema` | string | 固定値 `"mtg-playtest/card@2"`。これ以外は読まない |
| `key` | string | `cache_key(name)`。同一性の判断はこれで行う |
| `name` | string | **英語名**（Scryfall の `name`）。内部の同一性はすべてこれで判断する。ファイル名の由来 |
| `en_name` | string\|null | `name` と同じ英語名（互換のため残している） |
| `printed_name` | string\|null | 印刷面の名前（日本語版なら日本語名）。無ければ null |
| `lang` | string\|null | 取得した印刷面の言語（`ja` / `en` など） |
| `mana_cost` | string | `"{1}{R}"` 形式。土地など無い場合は `""` |
| `mana_value` | integer | マナ総量。Scryfall の `cmc` を整数化 |
| `colors` | string[] | 色。`["R"]` など |
| `color_identity` | string[] | 色指標 |
| `supertypes` | string[] | **英語**。`Legendary` / `Basic` / `Snow` / `World` のみ |
| `types` | string[] | **英語**。`Creature` `Land` `Instant` など。判定はすべてこれを見る |
| `subtypes` | string[] | **英語**。`Human` `Monk` など |
| `type_line_en` | string | 英語のタイプ行そのまま。`"Creature — Human Monk"` |
| `type_line_printed` | string\|null | 印刷面のタイプ行。表示専用 |
| `power` | string\|null | **文字列**。`"*"` や `"1+*"` があるため数値にしない |
| `toughness` | string\|null | 同上 |
| `loyalty` | string\|null | プレインズウォーカーの初期忠誠度 |
| `defense` | string\|null | バトルの守備値 |
| `oracle_text_en` | string | 英語のオラクル・テキスト（改行含む） |
| `oracle_text_printed` | string\|null | 印刷面のテキスト。表示に優先して使う |
| `keywords` | string[] | **英語**のキーワード能力。Scryfall の `keywords` をそのまま |
| `layout` | string | `normal` / `transform` / `modal_dfc` / `split` など |
| `faces` | object[] | 複数面カードのみ。単面なら `[]`（下記） |
| `scryfall_id` | string\|null | その印刷のID |
| `oracle_id` | string\|null | オラクル上のカードID。**別名レコードの同一性の根拠** |
| `scryfall_uri` | string\|null | 人が見るためのURL |
| `source` | string | `"scryfall"` か `"manual"` のみ |
| `unresolved` | boolean | true なら中身が未確定（下記） |

| `alias_of` | string | 別名レコードのときのみ。元レコードの `key` |
| `fetched_at` | string\|null | Scryfall から取得した時刻。`YYYY-MM-DDTHH:MM:SSZ`（UTC） |
| `updated_at` | string | ファイルを書いた時刻。同上 |

### faces（複数面カード）

`layout` が `transform` / `modal_dfc` / `split` などのとき、面ごとの情報を順に入れる。
`faces[0]` の値がトップレベルの代表値としてコピーされる（つまり**表面で判定する**）。
裏面の情報が要るときは `faces[1]` を見る。単面カードでは `faces` は空配列。

```json
"faces": [
  {"name": "Delver of Secrets", "mana_cost": "{U}",
   "type_line_en": "Creature — Human Wizard", "types": ["Creature"],
   "subtypes": ["Human", "Wizard"], "power": "1", "toughness": "1",
   "oracle_text_en": "At the beginning of your upkeep, ...", "...": "..."},
  {"name": "Insectile Aberration", "types": ["Creature"], "power": "3", "toughness": "2"}
]
```

---

## 導出規則

タイプの分解（`split_type_line`）は**英語の `type_line` にのみ適用する**。

1. `//` があれば先頭の面だけを見る（複数面は `faces` 側で個別に処理済み）
2. ダッシュ（`—` `–` `-`）で左右に割る
3. 左側の語を空白で分け、`Legendary` `Basic` `Snow` `World` を `supertypes` に、
   残りを `types` に入れる
4. 右側の語を `subtypes` に入れる

手入力（`card set`）で日本語のタイプ名を書いた場合は `JP_TYPES` で英語に写してから
同じ規則を適用する（`クリーチャー` → `Creature`）。**保存されるのは英語**。

`mana_value` は Scryfall の `cmc`（float）を `int()` する。

---

## 名前の解決（表記揺れ）

日本語名は Scryfall の**完全一致でしか引けない**ため、揺れはこちらで吸収する。
確からしい順に降りていき、**一意に決まった時点で止める**。

| 順 | 手段 | 例 |
|---|---|---|
| 1 | `lang:ja !"<名前>"` 完全一致 | `僧院の速槍` |
| 2 | 表記を直して完全一致（`,`→`、`、`･`→`・`、空白除去、区切り記号の除去） | `敏捷なこそ泥,ラガバン` |
| 3 | `lang:ja name:"<名前>"` 名前の部分一致 | `僧院の速` → `僧院の速槍` |
| 4 | 名前を区切りで割った断片で部分一致 | `敏捷なこそ泥、ラガバン` の `ラガバン` |
| 5 | `named?fuzzy=` | — |

英語名は 5 の fuzzy 検索を先に使う。タイプミス（`Lightnig Bolt`）まで吸収される。

なお句読点の揺れ（1と2の差）は Scryfall の完全一致側でも吸収されるため、
多くの場合は 1 で当たる。2 は空白の揺れなど、それでも外れる場合の保険。

### 結果の扱い

| status | 意味 | 動作 |
|---|---|---|
| `found` | 求めた名前で一意に決まった | 保存する |
| `corrected` | 表記を補正して一意に決まった | 保存し、**補正したことを必ず知らせる**。レコードに `corrected: true` を残す |
| `ambiguous` | 候補が複数 | **推測しない**。候補を並べて正式名を求める。何も保存しない |
| `notfound` | Scryfall は答えたが該当なし | 負のキャッシュを書く |
| `error` | 通信できなかった | 何も保存しない |

**複数候補のときに推測しないのが要点。** 「ラガバン」は
《敏捷なこそ泥、ラガバン》と《ラシュミとラガバン》の両方に一致する。
勝手に選ぶと、間違ったカードでテストプレイが進んで結論ごと無駄になる。

`corrected: true` はレコードに残るので、**2回目以降のキャッシュヒットでも警告が出る**。
デッキリストの誤記が一度キャッシュされて以後黙って通る、という事故を防ぐため。
別名レコード（正式名で書かれる方）には引き継がない。

## source と unresolved

| source | unresolved | 意味 | 生成される場面 |
|---|---|---|---|
| `scryfall` | false | Scryfall から取得済み。信頼してよい | 通常の取得 |
| `manual` | false | 人が `card set` で登録した。自作カードなど | `card set` |
| `manual` | true | **負のキャッシュ**。Scryfall に存在しない名前 | 取得を試みて 404 だった |

負のキャッシュは、自作カード名を毎ゲーム問い合わせに行かないためのもの。
`get()` はこれを見つけると**ネットワークに出ずに** `unresolved` を返し、
呼び出し側は「`card set` で登録してください」と促す。

**通信できなかった場合は負のキャッシュを書かない。** 一時的な障害を
「存在しないカード」として固定してしまうため。`fetch_scryfall` は
404/400（該当なし）と、それ以外の通信失敗（`error`）を区別している。

---

## 別名レコード（mtg-playtest/alias@1）

正本は英語名の1ファイルだけ。日本語名・デッキリスト上の表記からは、
**中身を複製せず指し先だけを持つ**別名レコードで転送する。

```json
{
 "schema": "mtg-playtest/alias@1",
 "key": "稲妻",
 "name": "稲妻",
 "alias_of": "lightning bolt",
 "en_name": "Lightning Bolt",
 "corrected": false,
 "updated_at": "2026-09-04T08:00:00Z"
}
```

| フィールド | 型 | 意味 |
|---|---|---|
| `schema` | string | 固定値 `"mtg-playtest/alias@1"` |
| `key` | string | 別名の `cache_key`。ファイル名の由来 |
| `name` | string | 別名そのもの（日本語名など） |
| `alias_of` | string | 指し先の `key`（英語名の `cache_key`） |
| `en_name` | string | 指し先の英語名。読み込みはこれを辿る |
| `corrected` | boolean | この別名が正式名ではない（誤記・部分名）。参照のたびに警告する |

複製ではなく転送にしているのは、カードを取り直したときに古い写しが
別名側に残らないようにするため。読み込み（`load`）は別名を自動で辿り、
返ってくるのは常に英語名の正本。どの別名で引かれたかは `_alias`
（ファイルには保存しない）に入る。

別名が書かれるのは次の場合。既にファイルがあれば上書きしない（手入力を壊さないため）。

- 取得したカードの `printed_name`（日本語名など）
- 参照に使われた名前が英語名と違うとき
- `cardcache.py alias <別名> <英語名>` で手動登録したとき
  （日本語版が存在せず Scryfall から日本語名が得られないカードに使う）

## 日英対応表

内部が英語名なので、日本語で書かれたデッキリストや会話とつなぐには対応表が要る。

```bash
python scripts/cardcache.py --dir cards glossary          # キャッシュ全体
python scripts/mtg.py glossary                            # 対局中のカードだけ
python scripts/mtg.py glossary --deck piza --out gl.txt
```

日本語名は、正本の `printed_name` → 無ければ日本語を含む別名、の順に探す。
どちらも無ければ「（日本語名なし）」と出す。

---

## 書き込み規則

- **アトミックに書く。** `<slug>.json.tmp` に書いてから `os.replace` で置き換える。
  中断しても壊れたファイルが残らない
- 書くたびに `updated_at` を更新する
- `fetched_at` は Scryfall から実際に取得したときだけ更新する
- 既定では**期限切れにしない**。オラクルの訂正を取り込みたいときは `card fetch --refresh`
  （`get(max_age_days=N)` を使えば N 日で再取得させることもできる）

## 検証

```bash
python scripts/cardcache.py --dir cards verify
```

各ファイルについて次を確認する。1つでも該当すればそのファイルは**キャッシュミス扱い**になる。

- JSON として読めるか
- 必須フィールドが揃っているか
- `schema` が `mtg-playtest/card@2`（正本）か `mtg-playtest/alias@1`（別名）か
- 配列であるべきフィールド（`types` `supertypes` `subtypes` `colors` `keywords` `faces`）が配列か
- `power` / `toughness` / `loyalty` が文字列か null か（数値で入っていたら不正）
- `source` が `scryfall` か `manual` か
- ファイル名が `name` から導かれる slug と一致するか

---

## 参照時の解決順

```
state["cards"][名前]（このゲーム限定の上書き）
  → <cards-dir>/<slug>.json（ディスク。別名なら英語名の正本へ転送）
    → Scryfall（初回のみ。--offline なら行かない）
```

- 対局の状態ファイルには**オラクル情報を持たない**。持つのはトークンや
  `card set --local` で入れたゲーム限定の上書きだけ
- トークンは実在カードではないので Scryfall に問い合わせない。
  ただし同名のカードが既にディスクにあれば（コピー・トークン）それを土台に使う
- 同じプロセス内では名前ごとに1回だけ解決し、以後はメモリ上の結果を使う

## 具体例

```json
{
 "color_identity": ["R"],
 "colors": ["R"],
 "en_name": "Monastery Swiftspear",
 "faces": [],
 "fetched_at": "2026-09-04T07:24:07Z",
 "key": "僧院の速槍",
 "keywords": ["Prowess", "Haste"],
 "lang": "ja",
 "layout": "normal",
 "mana_cost": "{R}",
 "mana_value": 1,
 "name": "僧院の速槍",
 "oracle_id": "dafd2713-d1bc-474b-b390-d2ff20b5375e",
 "oracle_text_en": "Haste\nProwess (Whenever you cast a noncreature spell, ...)",
 "oracle_text_printed": "速攻\n果敢（あなたがクリーチャーでない呪文を唱えるたび、...）",
 "power": "1",
 "printed_name": "僧院の速槍",
 "schema": "mtg-playtest/card@1",
 "scryfall_id": "d11777e9-8f90-4241-a990-583f483f8044",
 "source": "scryfall",
 "subtypes": ["Human", "Monk"],
 "supertypes": [],
 "toughness": "2",
 "type_line_en": "Creature — Human Monk",
 "type_line_printed": "クリーチャー — 人間・モンク",
 "types": ["Creature"],
 "unresolved": false,
 "updated_at": "2026-09-04T07:24:07Z"
}
```

---

## バージョニング

`schema` の値は `mtg-playtest/card@<整数>`。**フィールドの削除・意味の変更・型の変更**を
したら番号を上げる。フィールドの追加だけなら上げない（読み手は未知のフィールドを無視する）。

旧バージョンのファイルは読まれず、移行を促すメッセージが出る。

```bash
python scripts/cardcache.py --dir cards migrate
```

`card@1` → `card@2` の移行はこれで行う（通信不要）。内容を複製していた旧・別名ファイルは
指し先だけの別名レコードに置き換えられ、正本は英語名のファイルにまとめられる。

---

## Scryfall への礼儀

- リクエスト間隔は 100ms 以上あける（`_last_request` で強制）
- `User-Agent` と `Accept` を必ず付ける（付けないと 400 が返る）
- 一度取ったものは取り直さない。これが本キャッシュの存在理由
