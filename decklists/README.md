# decklists / decks の規約

`decklists/*.txt` が**人が書く正本**、`decks/*.json` が `deck add` で生成する**登録簿**。
登録簿は決め打ちの手編集をせず、テキストを直してから登録し直す。

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py deck add decklists/piza.txt --name piza --description "..."
```

## ここに置くもの / 置かないもの

**`decklists/` と `decks/` が持つのは素の構築（G1で使う75枚）だけ。**
サイド後の構成はマッチごとの記録なので登録しない。計画は方針文書
（`strategy/<登録名>.md` の「サイドボード」節）、実際に使った60枚は
`playtest/<対局フォルダ>/g02-<略称>.txt`（規定は
[`references/storage-layout.md`](../.claude/skills/mtg-playtest/references/storage-layout.md)）。

マッチアップごとに登録を増やすと、同じ物理デッキが `stats` で別デッキとして数えられ、
2ゲームのマッチが「4デッキ・各50%」になって勝率が読めなくなる。

## ファイル名

`<デッキ名>.txt`（`piza.txt` / `boros-dwarves.txt`）。

- `<デッキ名>` は半角英小文字・数字・ハイフン。そのまま登録名になる。
- **登録名 = ファイル名の拡張子を除いた部分**。`deck add --name` にこれを渡すので、
  `decks/` 側のファイル名も `<登録名>-<ハッシュ>.json` になり、両者が一対一で対応する。
- 短い略称（`dw` / `tokens` / `green` / `piza`）は
  [`references/storage-layout.md`](../.claude/skills/mtg-playtest/references/storage-layout.md)
  のデッキ略称表で固定する。playtest のフォルダ名と同じ語彙。

## 中身の書式

```text
# <登録名> — <一行説明>
# 出典: <URL・由来>                    ← あれば
# 方針: decklists/strategy/<登録名>.md ← 方針文書があれば

Deck
<クリーチャー>

<その他の呪文>

<土地>

Sideboard
<同じ並び>
```

- **カード名は英語名**（エンジンが内部で使う正規名）。日本語名は
  `mtg.py glossary` で引ける。日本語の対戦ログと突き合わせるときはそちらを使う。
  1ファイル内で日英が混ざらないよう、日本語印刷があるカードも英語名で書く。
- セット記号とコレクター番号（`(DFT) 218`）は書かない。`deck add` が名前で解決するため
  参照されず、書いてあるファイルと無いファイルが混在するだけになる。
- 並びは **クリーチャー → その他の呪文 → 土地**、各群はマナ総量→名前の順。
  群の間は空行で区切る（空行と `#` 行はパーサーが無視する）。
  素の構築とサイド後の diff がそのまま IN/OUT になるので、この順を崩さない。
- 見出しは `Deck` / `Sideboard`。`デッキ` / `サイドボード` も解釈されるが、書くときは英語で揃える。
- 対局フォルダに書くサイド後の60枚も同じ書式にする（ヘッダーは素の構築と IN/OUT を書く）。

## プレイ方針とサイドボード計画

`strategy/<登録名>.md` に置く。ファイル名は登録名と同じにする（`piza` → `strategy/piza.md`）。

置いておくと、そのデッキを使うときに自動で案内が出る。**登録JSONには書き写さない**
（写すと方針を直すたびに登録し直すことになる）。

| 出る場所 | 出力 |
|---|---|
| `deck show <登録名>`（`--brief` でも） | `方針: decklists/strategy/piza.md （このデッキを使うなら対局前に読む）` |
| `deck list` | 該当デッキの行に `方針あり` |
| `init`（そのデッキを使ったとき） | `方針: P2 = decklists/strategy/piza.md （最初のプレイ判断より前に読む）` |

長い方針は開始用の`<登録名>.md`と、`<登録名>-combo.md`／`<登録名>-sideboard.md`等へ分割できる。開始用にはキープ・基本展開・追加資料を読むタイミングとリンクを残す。

中身の書式は自由。デッキの狙い・始動手順・キープ基準・不利マッチでの方針など、
プレイ中に迷う判断を書く。
