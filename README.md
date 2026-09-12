# MTG Playtest

Magic: The Gathering の対戦をテキスト上で進行・検証するための、Claude Code 用スキルと盤面管理 CLI。

自作カードやデッキ案を「実際に回してみる」ための道具立てです。盤面・ライブラリー順・
ライフ・カウンター・スタック・ターン進行といった**取り違えやすい情報を Python が記帳し**、
カードテキストの解釈・誘発・対象の適正・レイヤーの判断は **AI（またはユーザー）が行います**。
スクリプトは記帳係であって、完全なルールエンジンではありません。

## 何ができるか

- Claude に「このデッキを回してみて」と頼むと、ルールに沿ってターンを進め、対戦ログとレポートを残す
- AI 同士の自動対戦（軽量モード／席分離モードの2種）と、人間 vs AI の対人モード
- デッキリスト（Arena 形式のテキスト）の登録・リーガリティ確認
- カード情報の Scryfall からの取得とローカルキャッシュ
- 装備・オーラ・期限付き効果・誘発の待ち行列など、忘れやすい状態の永続化
- undo、seed 付き乱数による再現可能なシャッフル、対戦結果の集計

## 必要なもの

- Python 3.12 以降（標準ライブラリのみ。追加パッケージ不要）
- カード情報の取得時のみインターネット接続（Scryfall API）
- スキルとして使う場合は [Claude Code](https://claude.com/claude-code)

## 使い方

### Claude Code から（想定している主な使い方）

このリポジトリをそのまま作業ディレクトリとして Claude Code を起動すると、
`.claude/skills/mtg-playtest/` がスキルとして読み込まれます。

```
decklists/piza.txt と decklists/boros-dwarves.txt を BO1 で1ゲーム回して
```

進行の作法・保存先の規約・ログ書式はすべて
[`.claude/skills/mtg-playtest/SKILL.md`](.claude/skills/mtg-playtest/SKILL.md) と
`references/` に書かれており、Claude が必要な部分だけ読みます。

### CLI を直接叩く

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py --help
```

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py deck add decklists/piza.txt --name piza
```

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py --state playtest/demo/g01.json init --deck1 piza --deck2 decklists/boros-dwarves.txt --seed 1
```

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py --state playtest/demo/g01.json show
```

`--deck1` / `--deck2` には登録名とデッキリストのファイルパスのどちらも渡せます。
`init` は両デッキのメイン・サイドボードのカードキャッシュを確認し、不足・未解決のカードを自動取得します。
有効なキャッシュは再取得しません。取得できないカードがあれば状態を保存せず停止します。
`--offline` では全カードがキャッシュ済みの場合だけ開始でき、従来の `--prefetch` は省略できます。
初手は `draw P1 7` / `draw P2 7` で引きます。

各サブコマンドの詳細は `mtg.py <コマンド> -h` で読めます。Windows で Python が PATH に
無い場合は `scripts/mtg.ps1` が同じ引数を受け取ります。

## リポジトリの構成

```text
.claude/
  agents/                       サブエージェント定義（プレイヤー役・ルール検証役）
  skills/mtg-playtest/
    SKILL.md                    エントリポイント。ここから必要な資料だけ辿る
    references/                 進行手順・スキーマ・保存先規約・ログ書式
    scripts/                    盤面管理 CLI（mtg.py とモジュール群）
    tests/                      unittest によるリグレッションテスト
decklists/                      デッキリスト（Arena 形式のテキスト。人が書く正本）
  strategy/<登録名>.md          デッキごとのプレイ方針とサイドボード計画
                                （あれば対局前に自動で案内される）
decks/                          登録済みデッキ（decklists から生成。検証済みの定義）
design/                         設計メモ（永続状態・誘発処理・効果の関連付けなど）
cards/                          カードキャッシュ（Git 管理外。実行時に自動生成）
playtest/                       対局データ（Git 管理外。実行のたびに生成）
```

### データの流れ

```text
decklists/*.txt  ──deck add──▶  decks/*.json  ──init──▶  playtest/<対局>/g01.json
（人が書く）                    （検証済み定義）           （対局の記録）
                                      ▲
                             cards/（Scryfall キャッシュ）
```

- **`decklists/`** … 手で書くデッキリスト。ここが唯一の手書きの正本。命名と書式の規約は
  [`decklists/README.md`](decklists/README.md)。デッキごとのプレイ方針は
  `decklists/strategy/<登録名>.md` に置くと、`deck show` と `init` が対局前にパスを案内します。
- **`decks/`** … `deck add` が `decklists/` を読んで作る**デッキ定義の登録簿**。カード名の表記揺れ・
  4枚制限・枚数不足を登録時に潰し、以後は登録名（`--deck1 piza`）で参照できる。**対局の記録ではない**
  ので、対局をいくら回しても増えません。増えるのはデッキを追加・サイド調整したときだけです。
- **`playtest/`** … 盤面の状態・操作バッチ・undo 履歴・`results.jsonl`・レポートといった
  **プレイ記録はすべてここ**。1つの依頼＝1フォルダで、命名規約は
  [`references/storage-layout.md`](.claude/skills/mtg-playtest/references/storage-layout.md)。
  `shuffle.py` の引き順出力と、サイドボード後のデッキリストもここに入ります。
- サイド後の構成は `decks/` に登録しません。計画は方針文書
  （`decklists/strategy/<登録名>.md` の「サイドボード」節）、実際に使った60枚は対局フォルダの
  `g02-<略称>.txt`。登録を増やすと同じ物理デッキが `stats` で別デッキとして数えられます。

`cards/` と `playtest/` は `.gitignore` で除外しています。クローン直後には存在せず、
初回の実行時に作られます。理由は [NOTICE.md](NOTICE.md) を参照してください。

## テスト

```bash
python -m pytest .claude/skills/mtg-playtest/tests -q
```

pytest を入れたくない場合は、各テストファイルを直接実行しても動きます。

```bash
python .claude/skills/mtg-playtest/tests/test_relations.py
```

## ライセンスと権利表示

コードと文書は [MIT License](LICENSE)。ただし Magic: The Gathering のカード名・カードテキスト
その他のゲーム素材は Wizards of the Coast LLC に帰属し、MIT の対象外です。本リポジトリは
同社とは無関係の非公式・非商用のファンプロジェクトです。詳細は [NOTICE.md](NOTICE.md) を
参照してください。
