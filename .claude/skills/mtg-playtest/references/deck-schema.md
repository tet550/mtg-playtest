# デッキ情報の規定（mtg-playtest/deck@2）

`scripts/decks.py` が読み書きするデッキ登録の規定。
**この文書とコードが食い違ったらコードが正**（`decks.validate_record()` が検査系）。

カード情報の規定は [card-schema.md](card-schema.md) を参照。

## 考え方

**登録するのは素の構築（G1で使う75枚）だけ。** サイド後の構成はマッチごとの記録なので
登録せず、対局フォルダに置く（[storage-layout.md](storage-layout.md)）。
マッチアップごとに登録を増やすと、同じ物理デッキが `stats` で別デッキとして数えられる。

**登録した時点でカード名を英語名に正規化する。** 内部の識別子はすべて英語名に揃え、
デッキリストに書かれた表記は `source_name`、日本語名は `printed` に残す。
同じカードが表記ごとに別物として扱われるのを防ぐため
（詳細は [card-schema.md](card-schema.md)）。

対局中の表示も英語名になる。日本語で確認したいときは `glossary` で対応表を出す。

デッキは**登録した時点で全カードを解決し、検証する**。表記揺れ・存在しないカード・
4枚制限違反・枚数不足は、対局を始める前に潰しておきたい。ゲームが始まってから
「そのカード名は存在しなかった」と分かると、そのテストプレイの結論ごと使えなくなる。

入力（人が書くテキスト）と保存（解決済みの記録）を分けているのはこのため。

---

## 入力：デッキリストのテキスト形式

一般的なデッキリスト（Arena / MTGO からの貼り付けを含む）をそのまま読める。

```
# コメント行（# と // は無視）
Deck
4 稲妻
4x 僧院の速槍
4 Lightning Bolt (2XM) 129
24 山

Sideboard
2 紅蓮破
```

### 解釈規則

| 対象 | 規則 |
|---|---|
| カード行 | `<枚数> [x] <カード名>`。`4 稲妻` `4x 稲妻` のどちらでも可 |
| Arena の末尾 | `(2XM) 129` のようなセット記号＋コレクター番号は**除去する** |
| 空行・`#`・`//` | 無視 |
| 見出し | `Deck` `Main` `メイン` → メイン、`Sideboard` `SB` `サイド` → サイドボード |
| 対象外の節 | `Commander` `Companion` `統率者` `相棒` 以降は読み飛ばし、**警告に残す** |
| 同名の行 | 合算する（`4 稲妻` が2行なら8枚として扱い、4枚制限で弾かれる） |
| 解釈できない行 | **捨てずに `warnings` に残す**。黙って無視すると枚数が合わなくなる |
| 見出しより前の行 | メインデッキとして扱う（見出しが無いリストに対応するため） |

カード名の照合は `cardcache` に任せる。表記揺れ・部分名・タイプミスは
[card-schema.md の「名前の解決」](card-schema.md) の規則で補正され、
補正された事実は `status: "corrected"` として記録に残る。

---

## 保存場所とファイル名

```
<decks-dir>/<slug>.json
```

- `<decks-dir>` … 既定 `decks`。`--decks-dir` で変更
- `<slug>` はカードと同じ規則（`cardcache.slug`）。登録名から一意に決まる
- 書き込みはアトミック（`.tmp` → `os.replace`）
- エンコードは UTF-8 / `ensure_ascii=false` / インデント1 / **キー辞書順**

```
burn → decks\burn-03b5db72.json
```

---

## レコードのスキーマ

### 必須フィールド

`schema` / `key` / `name` / `main` / `main_total` の5つ。

### トップレベル

| フィールド | 型 | 意味 |
|---|---|---|
| `schema` | string | 固定値 `"mtg-playtest/deck@2"` |
| `key` | string | `cardcache.cache_key(name)`。同一性はこれで判断 |
| `name` | string | 登録名。`init --deck1 <名前>` で指定するのはこれ |
| `format` | string | `standard` など。既定 `standard`。検証規則の選択に使う |
| `description` | string | 任意のメモ（何を試すデッキか） |
| `main` | object[] | メインデッキ。下記のエントリ |
| `sideboard` | object[] | サイドボード。同じ形 |
| `main_total` | integer | `main` の `count` 合計。**一致しなければ不正** |
| `sideboard_total` | integer | 同上 |
| `source_file` | string\|null | 取り込み元のファイルパス（区切りは `/`） |
| `source_text` | string | **元のテキストをそのまま**保存する。解釈が変わったとき読み直せる |
| `warnings` | string[] | 解釈時の警告（読めなかった行、読み飛ばした節） |
| `problems` | string[] | 検証で見つかった問題。空なら適正 |
| `legal` | boolean | `problems` が空かどうか |
| `created_at` | string | `YYYY-MM-DDTHH:MM:SSZ`（UTC） |
| `updated_at` | string | 同上 |

### エントリ（`main` / `sideboard` の要素）

| フィールド | 型 | 意味 |
|---|---|---|
| `count` | integer | 枚数。**1以上の整数** |
| `name` | string | **英語名**。解決できたら英語名に正規化する。ゲーム中の識別子はこれ |
| `source_name` | string | **デッキリストに書かれたままの名前**。誤記を指摘するときに使う |
| `printed` | string\|null | 印刷名（日本語版なら日本語名）。日英対応表の材料 |
| `oracle_id` | string\|null | オラクル上のカードID。**同一カード判定の根拠** |
| `types` | string[] | 英語のカードタイプ（キャッシュから） |
| `supertypes` | string[] | 英語の特殊タイプ。`Basic` の判定に使う |
| `mana_value` | integer | マナ総量。マナカーブの集計に使う |
| `colors` | string[] | 色 |
| `status` | string | 下記 |

### status

| 値 | 意味 |
|---|---|
| `ok` | そのままの名前で解決した |
| `corrected` | 表記を補正して解決した。**問題として報告する**（誤記を放置しないため） |
| `ambiguous` | 候補が複数あり確定できない |
| `unknown` | 実在するカードとして解決できない（自作カードなら `card set` で登録） |
| `offline` | 通信できず確認できていない |

導出値（`types` `mana_value` `colors` など）はキャッシュのコピーであり、
**キャッシュを更新したら `deck verify` で取り直す**。マナカーブや色の内訳は
保存せず、表示のたびに数え直す（古くならないようにするため）。

---

## 検証規則（`format: standard` = 構築戦）

| # | 規則 | 判定 |
|---|---|---|
| 1 | メインデッキは60枚以上 | `main_total < 60` なら問題 |
| 2 | サイドボードは15枚まで | `sideboard_total > 15` なら問題 |
| 3 | 同名カードは4枚まで | **メインとサイドの合計**で数える |
| 4 | 基本土地は3の対象外 | `supertypes` に `Basic` を含むもの |
| 5 | すべてのカードが解決できること | `status` が `ok` 以外は問題 |

### 3が重要な理由

同一カード判定は**名前ではなく `oracle_id`** で行う。
`4 稲妻` と `4 Lightning Bolt` は表記が違うだけで同じカードなので、合計8枚として弾く。

```
NG 《Lightning Bolt / 稲妻》が 8枚です（4枚まで）（表記違いを合算）。
```

名前で数えていると、この事故を通してしまう。

---

## 使い方

```bash
python $M deck add burn.txt --name burn --description "赤単テスト"
python $M deck list
python $M deck show burn          # 内訳・マナカーブ・色
python $M deck verify             # 全デッキを再解決して検証しなおす
python $M deck rm burn
python $M init --deck1 burn --deck2 control --seed 5
```

`--deck1` は**登録名でもファイルパスでも**受け取る。ファイルを直接渡した場合は
その場で解釈・検証するだけで、登録はしない。問題があれば警告してから続行する
（自作カードを含むデッキを試したい場合があるため、検証は止めない）。

登録済みデッキで対局を始めると、状態ファイルに `players[*].deck` として
デッキ名が残り、`end` で記録する対戦成績にも紐づく。

---

## バージョニング

`schema` は `mtg-playtest/deck@<整数>`。フィールドの削除・意味の変更・型の変更で
番号を上げる。追加だけなら上げない（読み手は未知のフィールドを無視する）。

番号を上げた場合、旧レコードは読まれない。`deck verify` が `source_text` から
**自動で作り直す**（`created_at` は引き継ぐ）。これが `source_text` を持っている理由。

```bash
python scripts/mtg.py deck verify        # 古い形式のデッキも作り直される
```
