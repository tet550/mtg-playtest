# バッチ実行と起動例

<!-- toc -->
- [バッチの適正サイズ](#バッチの適正サイズ)
- [呼び出し例（PowerShell、プロジェクト直下）](#呼び出し例powershellプロジェクト直下)
<!-- /toc -->

必要なときだけ読む。開始の基本は[軽量対局](ai-vs-ai.md)。

## バッチの適正サイズ

**長さは行数ではなく判断点の数で決める。1バッチに含めてよい判断点は0個。** 判断点に達したらそこで切り、結果を読んでから次のバッチを書く。切るコストはCLI呼び出し1回、切らないコストは巻き戻しと手作業の状態補正で、後者のほうが常に高い。

### 判断点（ここに達したら切る）

| 種別 | 例 |
|---|---|
| 乱数・非公開情報 | `draw` / `mill` / `look` / `pick` / 占術・諜報の判断 |
| 候補提示 | `search` は名前で候補を出させてから `--oid` を選ぶ。oidを推測して後続行を書かない |
| 新規ID | トークン生成後の参照。`--label` で確定できる場合だけ同一バッチ可 |
| 優先権 | 呪文・能力をスタックに乗せた後、攻撃宣言の前、ブロック宣言、戦闘ダメージの前 |
| 選択 | モード・対象・支払い方法・任意の誘発（may）・生け贄や捨て札の選択・タップイン／ライフ支払いの選択 |
| 恒久誘発 | アップキープ・終了ステップなど、`remind` に登録した確認 |

### 必ず切る位置

1. **`turn next` はバッチの最終行にする。** 次のターンの操作を同じバッチに書かない。自分のターンと相手のターンを1バッチに混ぜない。
2. **恒久誘発があるターンは `turn next --to precombat_main --draw` を使わない。** `turn next --to beginning.upkeep` で止め、誘発を処理してから `phase to beginning.draw` → `draw <席> 1` → `phase to precombat_main` と進める。
3. **自分のターン内を進めるのは `phase to`。** アップキープや戦闘中に `turn next` を使うと次のターンへ飛び、そのターンのドローとメインが消える。`phase` は巻き戻せない。
4. **攻撃宣言の前で切る。** 戦闘開始時に相手の行動（瞬速・起動型能力）が無いことを確認してから `attack` を書く。ブロック宣言とダメージの前も同様。
5. 対人戦では、ユーザーが優先権を持ちうる箇所ごとに切る。相手のターンの処理を先回りして同じバッチに入れない。

### 恒久誘発を落とさない手当て

恒久誘発を持つパーマネントが戦場に出たら、**その着地と同じバッチで `remind add ... --on turn --player <席> --src <oid>` を登録する。** CLIは誘発を自動生成しないので、登録しなければ何も警告しない。`remind` はターン開始時に「確認 R1: ...」と表示されるだけなので、表示を見たら `pending add` で台帳に積む。

### サイズの目安

- 判断点0の記帳（土地設置→タップ→`mana add`→`stack push`→`pass-both`→`stack resolve`→`move`→`note`）は10〜20行を1バッチにしてよい。
- コンボ始動・除去の使い所・決着ターンは**1バッチ1アクション**にする。
- 30行を超えたら判断点を見落としていないか疑う。行数を減らすこと自体は目的ではない。削ってよいのは説明であって、判断と確認ではない。

### 訂正時の注意

`note --event` のIDはゲーム内で一意。訂正で同じイベントを書き直すときは新しいID（E30など）を使う。`pending` のIDも取消し・完了後は再利用できないので、事前に番号を書いたバッチは途中で落ちる。`pending add` の出力を読んでから `pending stack` を書く。

**`undo` は万能ではない。** トークン生成やドローは戻るが、ライフ・タップ状態・カウンター・攻撃宣言・pending台帳の完了状態は戻らないことがある。戻り切らない分は `note` に訂正IDを付けて手で補正する。上の規定どおりに切っていれば、そもそも undo を使う場面がほとんど無くなる。

## 呼び出し例（PowerShell、プロジェクト直下）

**必ずプロジェクト直下から実行する。** カードキャッシュの既定は相対パスの `cards`（環境変数 `MTG_CARDS_DIR` / `--cards-dir` で変更可）なので、対局フォルダへ`cd`して実行するとそこに `cards/` が新規作成され、Scryfallへの再取得とキャッシュの分裂が黙って起きる。

`run` は実行前にバッチ全体の構文を検査し、エラーを元ファイルの行番号付きでまとめて返す（状態変更なし）。対象・マナ・応答など、進行中の状態に依存する適法性は事前検査では保証しない。

席制限なしのバッチでは、双方の応答なしを判断済みの場合だけ `pass-both P1`（P1から）または `pass-both P2` を使える。通常のpass 2行へ展開し、それぞれ保存・undo・証跡を維持する。スタックが空なら従来どおりステップが進むため、直後に重ねてphase nextしない。未知の誘発や選択をまたぐ用途には使わない。

`--delta` は要約欄の同じ項目を `ライフ 20→17` と表示する。他の変更は `-` / `+` を使う。実行末尾の `詳細ログ: <path>:1-N` は証跡の物理行範囲。JSONLの `source_line` で元バッチの行に戻れる（短縮指定の展開行は同じsource_line）。

バッチファイル（`.mtg`）の書き方で毎回踏むところ:

- `note` の本文は `note "説明"` の形で引用する。本文に`--draw`などを含む場合、引用しないとオプションとして解釈される。秘匿指定は引用の外に`--private P2`を置く。
- `--delta`はバッチ内のshowではなく`run <file> --compact --delta`に付ける。showは`show --hand both`などの通常構文にする。
- 通常の次ターンは **`turn next --to precombat_main --draw`**。アップキープ等で判断が必要なら手前で区切る。`--draw`だけで`--to`を省く指定は変更前に停止する。アンタップのみの`turn next`の後はphaseで進め、ドローを手動処理する。同じターンを進めるためにturn nextを繰り返さない。
- `phase`はカードを引かない。ドロー・ステップの通過時は警告を確認し、未処理の場合だけ`draw <席> 1`を実行する。再開や補正で既に引いている場合に二重ドローしない。
- マナは `mana P1 add` に**実際に生み出す色だけ**（無色は `C`、数字はエラー）、`mana P1 spend` に**コスト表記そのまま**（`spend 2G` でよい）。
- 装備の効果は `fx add --src <装備品oid> --ability bonus --pt +1/+0 --grant トランプル` のように一度登録する。複数のキーワードは `--grant 速攻 "護法{1}"` と**1つの`--grant`に並べる**（`--grant A --grant B` は後勝ちで上書きされ、先に書いた方が消える）。カウンター連動はテキストを確認して `--counter <名前> --per-counter +1/+0` を指定すると自動再計算する。付け替えはattachだけ。旧mod/grantの移行、効果の訂正・例外は [relations.md](relations.md) を読む。
- 使い捨てのバッチは標準入力から `run - --compact` へ渡す。Windows/Git Bashのプロセス置換（`run <(...)`）は使わない。状態JSON・証跡JSONL・連番入力ファイルはCLIが読み書きするため、AIが生成・全文確認する必要はない。

```powershell
$runDir = 'playtest/20260909-1412-bo1-dw-vs-piza' # 例。実際の作成日時・席で作る。作成前に ls playtest/ で同名の有無を確認する
& ./mtg.ps1 --state "$runDir/g01.json" session dw_piza_g01 # 未使用の短縮名を選ぶ
& ./mtg.ps1 --session dw_piza_g01 init --deck1 A --deck2 B --seed 101 --first P1 --history-hand both
@'
draw P1 7
draw P2 7
show --hand both
'@ | & ./mtg.ps1 --session dw_piza_g01 run - --compact
# キープ・マリガンを判断後、各バッチを同様に実行する。
& ./mtg.ps1 --session dw_piza_g01 end --winner P1 --reason '決着理由' --tag A-vs-B
# 決着済みの盤面を記帳し続けるより投了が早いときだけ:
# & ./mtg.ps1 --session dw_piza_g01 concede P2 --reason '...' --tag A-vs-B
& ./mtg.ps1 --session dw_piza_g01 stats --tag A-vs-B
```

`python` がPATHにない環境では利用可能なPython実行ファイルの絶対パスを使う。
Windows / Codex desktop では `& .claude/skills/mtg-playtest/scripts/mtg.ps1 <引数>` も使える。PATHのPythonを優先し、なければ同梱ランタイムを探す。
プロジェクト直下の `& ./mtg.ps1 <引数>` は同じ入口の短縮で、標準入力もそのまま渡す。

毎回の `--state` / `--results` は `session` で短縮名に登録できる。`python $mtg --state <state> --results <results> session g01green` の後は `python $mtg --session g01green show` のように書く。**`--session` とパス指定（`--state` など）は併用できず、同じ名前の再登録もできない**（既存の登録を黙って上書きしないため）。登録は `playtest/.sessions/<名前>.json` に残る。`run` の中では `session` を使えない。

## 採番待ちを減らす入力

両席の応答なしを確認した区間は、既存の`pass-both P1`（P1→P2）または`pass-both P2`を使う。席制限付きでは使用しない。新しい能力やトークンのIDは、同じバッチ内なら`--label`で別名へ結び付けられる。事前にIDを推測しない。

```text
pending add "Clue生成" --controller P2 --label etb
pending stack $etb
pass-both P2
pending resolve $etb --do "token P2 --preset clue" --part create --label clue
sba --apply-deaths
note "ETBでClueを生成" --event E01
show --ids --packed --hand both
```

`--label`はrunが処理する指定で、直接CLIや解決用ファイル内には書かない。使用できる生成行はpending add、token（1個）、トークンをちょうど1個作るpending resolve。別名は英小文字から始まる英小文字・数字・_。同じrun内で重複・先行参照は不可。別runへは実際のT番号・OIDを引き継ぐ。解決区間が0個または複数個のトークンを生成すると、その区間の状態を保存せず停止する。

`$clue`のような独立した語を参照として使い、`--do "tap $clue"`内も展開できる。`--src=$clue`のようなオプションとの結合や、`--file`で読むファイル内は展開しない。別名入りバッチでundo/initを使わず、復旧は別バッチで行う。証跡には入力・展開後コマンド・実際のIDを残す。

zsh/bashでは`<<'EOF'`、PowerShellでは単一引用符のhere-string（`@'`〜`'@`）等で渡し、シェルによる`$name`の展開を防ぐ。乱数・ドロー・探索等の結果で判断が変わる境界は、別名があっても必ず区切る。
