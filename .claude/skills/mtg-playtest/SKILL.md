---
name: mtg-playtest
description: >-
  Magic: The Gathering（MTG／マジック：ザ・ギャザリング）の対戦をテキスト上で進行・シミュレートするスキル。
  ルールに従ってターンを回し、盤面・スタック・優先権・状況起因処理を厳密に管理する。
  「MTGのテストプレイをしたい」「このデッキを回してみて」「自作カードのバランスを見たい」
  「対戦相手になって」「この盤面どうなる？」「1ゲーム回して勝率を見たい」など、
  MTG・マジック・カードゲームのプレイやデッキ検証に関する依頼では必ずこのスキルを読み込むこと。
  カード名・デッキリスト・マナコスト・ライフ・フェイズといった語が出てきたら、
  明示的に「テストプレイ」と言われていなくても該当する可能性が高いので読み込むこと。
  ポケカ・遊戯王など他TCGや、MTGアリーナの操作代行は対象外。
---

# MTG テストプレイ

盤面と乱数は `scripts/mtg.py` で保持し、AIがプレイとルールを判断する。スクリプトは記帳係であり、完全なルールエンジンではない。

## 必要な手順だけ読む

資料の読み分けは [総目次](references/index.md)。リンクを再帰的に全部読まない。初期読み込みは選んだモードの開始手順・保存先・記録規約・対象デッキの開始方針だけ。分冊は各資料が指定する判断の前に読む。

- **AI同士・自動シミュレート**：進行は [references/ai-vs-ai.md](references/ai-vs-ai.md) を読む。単一AIで両席を操作する軽量方式が既定。手札情報の影響はある程度許容し、席分離や検証サブエージェントは使わない。
- **対人戦・盤面ジャッジ**：[references/play-modes.md](references/play-modes.md) の該当モードと必要箇所を読む。対人戦ではユーザーのマリガンと選択可能な優先権で止める。AIの手札は伏せる。
- **席分離を明示指定された場合のみ**：[references/ai-vs-ai-isolated.md](references/ai-vs-ai-isolated.md)。
- **実行中の記録**：[references/log-format.md](references/log-format.md) を開始時に一度読む。保存版の書式は決着後に [references/report-format.md](references/report-format.md) を読む。
- **新規対局の保存先を作るとき**：[references/storage-layout.md](references/storage-layout.md) の命名・採番・ファイル配置に従う。
- キャッシュ形式を編集するときだけ [references/card-schema.md](references/card-schema.md)、デッキ登録形式を編集するときだけ [references/deck-schema.md](references/deck-schema.md)。
- **装備・オーラの効果、関連追放、期限付き効果**：[references/relations.md](references/relations.md)。装着効果は `fx` に一度登録して自動計算し、付け替え時にmod/grantを再入力しない。関連追放は `linked` で世代と帰還条件を保存する。
- **果敢・上陸などの処理待ちと確認メモ**：[references/bookkeeping.md](references/bookkeeping.md)。AIが判断した誘発を `pending` に登録し、解決の効果と完了をまとめて保存する。`remind` は確認表示のみで、誘発を自動生成しない。

依頼からモードが明らかなら確認せず進める。不明な場合だけ聞く。実装修正では対局を開始しない。

## 共通の最小原則

- Pythonの状態が正本。手札・ライブラリー・全履歴のJSONを直接読み込まず表示コマンドを使う。
- **デッキの方針文書は対局前に読む。** `decklists/strategy/<登録名>.md` があれば `init` と `deck show` がパスを出す。出たら開始用本文を最初のプレイ判断より前に読み、以後その方針に従う。コンボ・サイド等の分冊があれば指定された判断の前に該当節を読む。無ければ方針は自分で決めて宣言する。
- カードは必要になった名前だけ `card show` で全文を確認し、同じ文脈では再読しない。キャッシュを優先し、不明な裁定は公式ルールを調べる。自作カードの未確定な解釈は確認する。
- マナ・対象・優先権・誘発・SBAは省略しない。乱数はseed付きのコマンドで扱う。
- 不明なコマンドは `python .claude/skills/mtg-playtest/scripts/mtg.py <command> -h` だけ読む。ソースや参考資料一式を先読みしない。
- 詳しい操作は [操作の早見表](references/commands.md) の該当節を読む。追加の用例だけ `references/cli-guide.md` の必要箇所を検索する（装備：`attached`、能力付与：`grant`、戦闘：`combat damage`、集計：`stats`）。

## コマンド資料の保守（必須）

実装を拡張する前に既存コマンド・保存・通知との重複を確認する。同じ状態変更は小さい共通関数へ寄せ、用途の違う保存単位まで統合しない。ルール自動判定や汎用イベント基盤を追加する前に、既存操作と明示的な記帳で解決できるか検討する。

コマンドの追加・構文変更・必須引数や既定値の変更時、および使用中に資料の誤記・不足が判明したときは、[references/commands.md の操作の早見表](references/commands.md) を同じ作業内で必ず更新する。該当する本文例も併せて直す。早見表には実在する構文と必要な引数を含め、条件付き必須オプションと省略時の注意を明記する。変更箇所を該当コマンドの `-h` と照合し、ヘルプだけでは分からない制約は該当実装を確認する。早見表への反映・確認が済むまで修正完了としない。
