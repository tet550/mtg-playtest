# jund-sacrifice vs piza ベンチマーク・テストケース

作成: 2026-09-14 / デッキ: `decklists/jund-sacrifice.txt`・`decklists/piza.txt`（サイドボードなし、素の構築）
機械可読版: [cases.json](cases.json)

**対局データ（`playtest/` 配下）は .gitignore 対象でリポジトリに含まれない。** この文書は再実行時の期待値を残すためのもので、証跡そのものは各実行環境のローカルにある。

同一seedを「単一AIが両席」と「人間がpiza側／AIがjund側」で回した対の記録。AIのプレイ品質を測る基準として使う。
**同じseedで固定されるのは初期シャッフルだけで、AIの選択は固定されない。** そのため期待値は3層に分ける。

- **A. 再現性**：完全一致を要求する（初手・先手）
- **B. プロセス**：新しい[バッチ規定](../../.claude/skills/mtg-playtest/references/ai-batches.md#バッチの適正サイズ)の回帰。機械判定できる
- **C. プレイ品質**：人間の実測を上限基準にした確率的ベンチマーク

## A. 再現性（完全一致）

`init --deck1 jund-sacrifice --deck2 piza --seed <値> --first <席>` 直後の初手。

| seed | first | P1 (jund) 初手7枚 | P2 (piza) 初手7枚 |
|---|---|---|---|
| 9141 | P1（`--first random`の抽選結果もP1） | Tragic Trajectory / The Sackville-Bagginses / Biotech Specialist / Rottenmouth Viper / Blood Crypt / Obsessive Pursuit / Wastewood Verge | ピザ / レッドシフト / クロームドーム / 植物の聖域 / モナリザ / 迅速な救助 / 始まりの町 |
| 9142 | P2 | Blazemire Verge / Wastewood Verge / Witherbloom Charm / Duress / Obsessive Pursuit / Lively Dirge / Swamp | 森 / モナリザ / 繁殖池 / 植物の聖域 / 始まりの町 / 不注意な読書家 / マルチバースへの通り道 |
| 9143 | P1 | Rottenmouth Viper / Blood Crypt ×2 / Duress / Umbral Collar Zealot / Tragic Trajectory / Greedy Freebooter | レッドシフト ×2 / 継ぎ接ぎのけだもの / 食料配達人 / 不注意な読書家 / 監視亀ラ / 繁殖池 / 植物の聖域（7枚のうちレッドシフトが2枚） |
| 9144 | P2 | Wastewood Verge ×2 / Swamp / Witherbloom Charm / Blood Crypt / The Sackville-Bagginses / 森 | 始まりの町 ×2 / 監視亀ラ ×2 / 森 / ピザ / 不注意な読書家 |
| 9145 | P1 | Biotech Specialist / Mutagen Man / 踏み鳴らされる地 / The Sackville-Bagginses / Witherbloom Charm / Blazemire Verge / Rottenmouth Viper | レッドシフト / 継ぎ接ぎのけだもの / 食料配達人 / 不注意な読書家 / 監視亀ラ / 繁殖池 / 植物の聖域 |

9143でpiza方針のマリガン基準（アンタップ役もルーティングも無い手札は引き直しを検討）を適用した場合の2枚目は
始まりの町 / モナリザ / レッドシフト / 島 / アガサの魂の大釜 / クロームドーム / ピザ（うち1枚をボトム）。

## B. プロセス回帰（機械判定）

対象は `playtest/<対局>/output/<game>/run-*.jsonl` と連番 `.mtg`。期待値は全ケース共通。

| ID | 判定 | 期待 | 2026-09-14時点の実測 |
|---|---|---|---|
| B1 | `note` 本文に「訂正／補正／運用ミス」を含む件数 | 0 | 25 |
| B2 | 証跡中の `undo` 実行回数 | 0 | 6 |
| B3 | 恒久誘発を持つパーマネントの着地バッチに `remind add` があるか | 全件あり | 0件（後付けのみ） |
| B4 | `turn next` がバッチ最終行以外にある件数 | 0 | 12 |
| B5 | 1バッチに両者のターンが混在する件数 | 0 | 4 |
| B6 | `attack` 行の直前でバッチが切れているか | 全件 | 3件が同一バッチ内 |
| B7 | `--oid` 指定の前に候補提示を読んでいるか | 全件 | 2件が推測 |

## C. プレイ品質ベンチマーク

「コンボ成立」＝ piza側がクロームドームの `{5}`（または大釜経由で同能力を得たクリーチャーの起動）を初めて解決したターン。

| seed | AI両席（baseline） | 人間piza（基準） | 合格ライン（piza側を回すとき） | jund側の回帰確認 |
|---|---|---|---|---|
| 9141 | P1勝利 T13 | **P2勝利 T12** | コンボ成立 ≤T14 | — |
| 9142 | P1勝利 T15（投了） | **P2勝利 T13** | コンボ成立 ≤T15 | — |
| 9143 | P1勝利 T14（投了） | **P2勝利 T10** | コンボ成立 ≤T12 | — |
| 9144 | P2勝利 T19 | **P2勝利 T7** | コンボ成立 ≤T11 | — |
| 9145 | P1勝利 T15 | **P2勝利 T8** | コンボ成立 ≤T12 | 勝利を維持 |

人間5戦は5勝0敗。AI両席では1勝4敗（piza側から見て）。

### 個別プレイの合否条件

5戦を通じて再現した敗因。piza側・jund側それぞれの必須チェック。

| ID | 席 | 条件 | AI実測 |
|---|---|---|---|
| C6 | piza | アガサの魂の大釜をルーティング／捨て札で切らない。マナ役・反復役の**冗長化パーツ**として扱う | 3回捨てた |
| C7 | piza | 迅速な救助を土地待ちで温存せず、序盤の掘り札として使う | 温存・捨て札 |
| C8 | piza | 始動ターンに「必要打点＝相手ライフ」「必要な亀ラのコピー数＝相手のアンタップ・ブロッカー数」を先に算出してから回す | 未実施 |
| C9 | piza | 監視亀ラのコピーはETBと離場で**2回**アンタップできる（1周＝支出9・収入10で黒字）ことを収支に入れる | 見落とし |
| C10 | jund | 除去の優先順位は **アガサの魂の大釜（MV2）＞クロームドーム**。大釜が残ると墓地から能力だけ移植されて反復が再建される | クロームドームを優先し3seedで再建を許した |
| C11 | jund | Witherbloom Charm／Tragic Trajectory を持っている間はタップアウトしない | 対人2戦とも違反し、始動に割り込めず敗北 |

## 実行条件（切り分け）

| 条件 | 内容 | 期待される差 |
|---|---|---|
| (a) 現行 | 変更なし | 上記baseline |
| (b) 規定のみ | バッチ適正サイズを適用 | B群が0になる。C群はほぼ不変 |
| (c) 規定＋方針文書 | jundの方針文書を新規作成、pizaにC6〜C9を追記 | C6〜C11が改善 |
| (d) (c)＋思考予算 | キーターンのみeffortを上げる | C1〜C5（成立ターン）が改善 |

C群は確率的なので、条件ごとに5seed×1回を最小単位とし、差が出たseedだけ追試する。

## 対局フォルダ

| seed | AI両席 | 人間piza |
|---|---|---|
| 9141 | `playtest/20260914-0932-series-jund-vs-piza/`（g01） | `playtest/20260914-1231-bo1-jund-vs-piza/` |
| 9142 | 同上（g02） | `playtest/20260914-1624-bo1-jund-vs-piza/` |
| 9143 | 同上（g03） | `playtest/20260914-1710-bo1-jund-vs-piza/` |
| 9144 | 同上（g04） | `playtest/20260914-1002-bo1-jund-vs-piza/` |
| 9145 | 同上（g05） | `playtest/20260914-1050-bo1-jund-vs-piza/` |

AI連戦の詳細は `playtest/20260914-0932-series-jund-vs-piza/report.md`。各対人戦の展開は各フォルダの `output/g01/run-*.jsonl` と `turn-starts.md` を参照。
