# ベンチマーク

テストプレイの期待値を残す場所。対局そのもののデータ（状態JSON・操作バッチ・証跡・report.md）は
`playtest/` 配下に生成されるが、そこは .gitignore 対象なのでリポジトリには入らない。
**再実行したときに何と比べるのか**だけをここに残す。

## 置くもの

`benchmarks/<YYYYMMDD>-<topic>/` に、**実行フェーズが読んでよいもの（`run/`）と期待値（`expected/`）を
分けて**置く。回す側が期待値・合否条件を見た時点で、それはプレイの指針になり、その回は基準値との比較に
使えなくなる。分けるのはこのため。

```text
benchmarks/<YYYYMMDD>-<topic>/
  README.md    この基準値の入口。期待値は書かない
  run/
    brief.md   対局中に読んでよい唯一のベンチマーク資料。回し方と測定条件だけ
    games.json seedと先手（入力であって期待値ではない）
    setup.py   実行フォルダ・manifest.json・initコマンドを用意する。expected/ を読まない
  expected/
    testcases.md  期待値の本体。再現性（決定的に一致すべき値）、プロセス回帰（機械判定できる項目）、
                  プレイ品質（確率的なベンチマーク）を分けて書く。同じseedでも初期シャッフルしか
                  固定されずAIの選択は固定されないので、この3層を混ぜない
    cases.json    初手と実測値の機械可読版
    conditions.json プロセス回帰の数え方と、プレイ品質の合否条件
  verify.py    初手など決定的に一致すべき項目の自動判定。一時領域で init をやり直す
  score.py     実行フォルダ1つを期待値と突き合わせ、score.md を出す
  decklists/   実施時点のデッキリストのスナップショット。**現行の `decklists/` を参照しない**ため、
               構築を変更しても基準値はその時点の60枚に対して有効なまま残る
```

実行と採点の手順は [mtg-benchmark スキル](../.claude/skills/mtg-benchmark/SKILL.md)。
対局データは `playtest/_checks/<日時>-bench-<topic>/` に作られ、実戦の `results.jsonl` と混ざらない。

## 置かないもの

対局フォルダの複製、カードキャッシュ、実行ログ。**期待値の判定が、対局フォルダにも現行のデッキリストにも
依存しないようにする**（`playtest/` は .gitignore 対象で別環境には無く、`decklists/` は後から変わる）。
出典として日付や実施内容を本文に書くのは構わない。

## 一覧

| 作成日 | 対象 | 内容 |
|---|---|---|
| 2026-09-14 | [jund-sacrifice vs piza](20260914-jund-vs-piza/README.md) | 5seedをAI両席と人間pizaで対にした基準値。バッチ規定の回帰項目も含む |
