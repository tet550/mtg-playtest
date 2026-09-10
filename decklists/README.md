# decklists / decks の規約

`decklists/*.txt` が**人が書く正本**、`decks/*.json` が `deck add` で生成する**登録簿**。
登録簿は決め打ちの手編集をせず、テキストを直してから登録し直す。

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py deck add decklists/piza.txt --name piza --description "..."
```

## ファイル名

| 種類 | 形式 | 例 |
|---|---|---|
| 素の構築 | `<デッキ名>.txt` | `piza.txt` / `boros-dwarves.txt` |
| サイド後 | `<デッキ名>-vs-<相手の略称>-g2.txt` | `piza-vs-dw-g2.txt` |

- `<デッキ名>` は半角英小文字・数字・ハイフン。そのまま登録名になる。
- `<相手の略称>` は
  [`references/storage-layout.md`](../.claude/skills/mtg-playtest/references/storage-layout.md)
  のデッキ略称表（`dw` / `tokens` / `green` / `piza`）を使う。playtest のフォルダ名と同じ語彙。
- **登録名 = ファイル名の拡張子を除いた部分**。`deck add --name` にこれを渡すので、
  `decks/` 側のファイル名も `<登録名>-<ハッシュ>.json` になり、両者が一対一で対応する。

## 中身の書式

```text
# <登録名> — <一行説明>
# 出典: <URL・由来>              ← あれば
# 方針: decklists/<name>.md      ← 別途プレイ方針を書いてあれば
# 元: decklists/<素の構築>.txt   ← サイド後のみ
# IN  (n): <枚数> <カード名> / ...
# OUT (n): <枚数> <カード名> / ...
# 理由: <サイドの根拠>            ← あれば。複数行可

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
- `IN` / `OUT` は素の構築とのメインデッキの差分。枚数の合計が一致しない場合は書き間違い。

## 旧名との対応

2026-09-10 に命名を揃えた。playtest 配下の過去のレポートは旧名で書かれているため、
遡って改名せずこの表で読み替える。

| 旧名 | 新名 |
|---|---|
| `piza-g2` | `piza-vs-green-g2` |
| `piza-vs-dwarves-g2` | `piza-vs-dw-g2` |
| `mono-green-landfall-g2` / 登録名 `mono-green-g2` | `mono-green-landfall-vs-piza-g2` |
| 登録名 `ボロスドワーフ` | `boros-dwarves` |
| 登録名 `Boros Tokens` | `boros-tokens` |

デッキの中身（メイン60・サイド15の内訳）はこの整理で一切変えていない。
