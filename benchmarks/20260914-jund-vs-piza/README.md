# jund-sacrifice vs piza ベンチマーク

同梱の60枚2つを固定の5seedで回し、**同じ条件の前回**と比べるための一式。デッキもseedも期待値も
このフォルダで閉じていて、リポジトリ現行の `decklists/` にも `playtest/` の対局データにも依存しない。

## 実行（この順に）

```bash
python benchmarks/20260914-jund-vs-piza/run/setup.py --condition a
```

実行フォルダと各ゲームの `init` コマンドが出る。そのまま5ゲームをAI同士で回し、決着ごとに
`end --tag <manifestのtag>`。回し方は [run/brief.md](run/brief.md)。

```bash
python benchmarks/20260914-jund-vs-piza/score.py playtest/_checks/<実行フォルダ>
```

同一性・A群（初手の再現性）・B群（進行の作法）・C群（プレイ品質）を採点し、実行フォルダに `score.md` を出す。
手順の全体は [mtg-benchmark スキル](../../.claude/skills/mtg-benchmark/SKILL.md)。

## フォルダの分け方

| 場所 | 中身 | 対局中に読んでよいか |
|---|---|---|
| `run/` | brief.md・games.json（seedと先手）・setup.py | **よい**。実行フェーズが読むのはここだけ |
| `decklists/` | 実施時点の60枚スナップショット | よい |
| `expected/` | cases.json・conditions.json・testcases.md | **だめ**。採点フェーズまで開かない |
| `score.py` / `verify.py` | 採点。出力に期待値を含む | **だめ**（採点フェーズで実行する） |

期待値と合否条件を見てから回すと、それはプレイの指針になり、その回は基準値との比較に使えない。
`run/` と `expected/` を分けてあるのはこのためで、片方に置くものを混ぜない。

## 単体で完結していること

- デッキは同梱スナップショット（`setup.py` と `verify.py` が現行の `decklists/` との差を実行時に知らせる）
- seedと先手は [run/games.json](run/games.json)、期待値は [expected/cases.json](expected/cases.json)
- 対局データは実行のたびに `playtest/_checks/` に作られ、採点はそのフォルダだけを見る
- 基準値の由来・3層（再現性／プロセス／プレイ品質）の考え方は [expected/testcases.md](expected/testcases.md)
