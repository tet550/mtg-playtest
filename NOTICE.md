# 第三者の権利について / Third-Party Rights

このリポジトリで **MIT License の対象になるのは、このリポジトリで書かれたコードと文書だけ**です
（`.claude/skills/mtg-playtest/` 以下のスクリプト・スキル定義・参考資料、`design/` の設計メモ、README など）。
以下はその対象外です。

## Wizards of the Coast

Magic: The Gathering は Wizards of the Coast LLC の商標です。カード名・カードテキスト・
アートその他のゲーム素材の著作権は同社に帰属します。本リポジトリは同社とは無関係の
非公式・非商用のファンプロジェクトであり、[Wizards of the Coast Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy)
に基づく Unofficial Fan Content です。Wizards of the Coast による承認・支援は受けていません。

そのため、**カードのオラクル・テキストを含むキャッシュ（`cards/`）はリポジトリに含めていません**
（`.gitignore` で除外）。実行時に Scryfall API から各自の環境に取得されます。
リポジトリに含まれるデッキリストとデッキ登録 JSON は、カード名・セット記号・収録番号・
`oracle_id` といった識別情報のみで、カードテキストは持ちません。

## Scryfall

カード情報は [Scryfall API](https://scryfall.com/docs/api) から取得しています。Scryfall とも無関係です。
利用にあたっては Scryfall の利用規約とレート制限に従ってください。

## Anthropic / Claude

`.claude/` 以下は Claude Code のスキル・サブエージェント定義です。Claude と Claude Code は
Anthropic PBC の製品であり、本リポジトリは同社の公式プロジェクトではありません。
