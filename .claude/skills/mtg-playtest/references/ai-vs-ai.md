# AI同士の軽量テストプレイ（既定）

## 情報と判断

単一AIが両席を操作する。両手札を同じ文脈で扱い、判断に影響するリスクを許容する。席別の `view`、`--as`、プレイヤー役・検証役のサブエージェントは使わない。相手の手札に合わせた最適化は極力避け、開始時に席ごとのキープ基準とプレイ方針を短く固定する。未公開のライブラリー順は読まない。

## 読み込みを減らす進行

1. 対象の2デッキだけ `deck show <登録名> --brief` で確認する。出力に `方針: decklists/strategy/<登録名>.md` が出たら、その文書を対局前に読み、そのデッキのプレイ方針として固定する（`init` も同じ行を出す）。登録JSON全文は読まない。BO1はメインの名前・枚数・警告で足りる。登録済みで変更がなければ再登録・全件検証しない。新規・変更時は `deck add <file> --quiet` で検証し、警告を解消する。サイド後の構成は登録せず、対局フォルダの `g02-<略称>.txt` を `init --deckN <パス> --deckN-name <素の構築の登録名>` で読む（[storage-layout.md](storage-layout.md)）。カードキャッシュ全体、過去対局、履歴、ソース全文は読まない。
2. [storage-layout.md](storage-layout.md) に従って保存先を作り、`init` のseedでシャッフルする。別途全ゲーム分のシャッフルファイルは作らない。各ゲームに別の状態ファイルとseed、連戦に共通の結果ファイルを使う。独立した連戦は先手を交互、マッチ形式は前ゲーム敗者が選ぶ。
3. 初手を配り、マリガンを処理する。盤面と両手札は `show --hand both` の1回で確認する。既知のカードテキストは再表示しない。未知のカードが現れたときだけ `card show <名前>` を読む。
4. 確定した操作を `run <file> --compact` にまとめる。1行ごとの保存とundoは維持される。ドロー・ランダム結果・未知の誘発など、その出力で判断が変わる箇所でバッチを区切り、結果を読んでから次の操作を決める。応答可能なら両席の応答を判断してから解決する。未確認の分岐をまとめて実行しない。
5. 通常はバッチの末尾に `show --hand both` を1回だけ置く。初回は全文、開始盤面が既知の次回以降は `run <file> --compact --delta` でバッチ開始時からの差分を受け取る。同一バッチで2回目以降のshowは前回showからの差分。再開時や文脈を失ったときは `--delta` を外す。差分の `-` は変更前、`+` は変更後であり、物理的なカード移動だけを意味しない。個々の `hand` / `view` / `show` を重ねない。`--compact` は既知の成功メッセージ・定型説明だけ省略し、警告・未知の出力・ドロー・探索候補は残す。全stdoutとコマンドは状態ファイル横の `output/<state名>/run-*.jsonl` に保存する。詳細は異常時の該当行だけ読む。エラー時は止め、修正してから続ける（`--keep-going` は使わない）。
6. `note` は開始方針、重要な分岐、裁定、コンボの収支、補正に限定する。通常の展開・攻撃・ドローを操作と同じ内容で説明し直さない。進捗・保存版・終了報告は [log-format.md](log-format.md) に従い、重要イベントの根拠を実行時に記録する。毎手の理由や全ログを再読・再掲しない。
7. `end` で勝敗を記録し、最後に同じ結果ファイルの `stats --tag` を読む。報告・集計項目は共通規定に従う。

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
- `run` に渡せるのは実ファイルだけ。Windows/Git Bash のプロセス置換（`run <(...)`）は動かない。使い捨てのバッチもスクラッチ領域に書き出す。

```powershell
$mtg = '.claude/skills/mtg-playtest/scripts/mtg.py'
$runDir = 'playtest/20260909-1412-bo1-dw-vs-piza' # 例。実際の作成日時・席で作る。作成前に ls playtest/ で同名の有無を確認する
$state = "$runDir/g01.json"
$results = "$runDir/results.jsonl"
python $mtg --state $state init --deck1 A --deck2 B --seed 101 --first P1
# g01-001.mtg: draw P1 7 / draw P2 7 / show --hand both を1行ずつ記述
python $mtg --state $state run "$runDir/g01-001.mtg" --compact
# キープ・マリガンを判断後、各バッチを同様に実行する。
python $mtg --state $state --results $results end --winner P1 --reason '決着理由' --tag A-vs-B
# 決着済みの盤面を記帳し続けるより投了が早いときだけ:
# python $mtg --state $state --results $results concede P2 --reason '...' --tag A-vs-B
python $mtg --state $state --results $results stats --tag A-vs-B
```

`python` がPATHにない環境では利用可能なPython実行ファイルの絶対パスを使う。
Windows / Codex desktop では `& .claude/skills/mtg-playtest/scripts/mtg.ps1 <引数>` も使える。PATHのPythonを優先し、なければ同梱ランタイムを探す。
プロジェクト直下の `& ./mtg.ps1 <引数>` は同じ入口の短縮で、標準入力もそのまま渡す。

毎回の `--state` / `--results` は `session` で短縮名に登録できる。`python $mtg --state <state> --results <results> session g01green` の後は `python $mtg --session g01green show` のように書く。**`--session` とパス指定（`--state` など）は併用できず、同じ名前の再登録もできない**（既存の登録を黙って上書きしないため）。登録は `playtest/.sessions/<名前>.json` に残る。`run` の中では `session` を使えない。

## 投了（`concede`）を使ってよい場面

勝敗が決まった後の消化ターンを記帳し続けないための機能であって、**形勢が悪いから畳む機能ではない**。
早すぎる投了は勝率も平均決着ターンも壊すので、次の3つを全部書けるときだけ打つ。書けないならプレイを続けて `end` で決着させる。

1. **相手の勝ち手順** … 何が何ターン後に決まるのか。無限が成立しているなら、そのループの反復条件も。
2. **こちらのアウト** … 手札・戦場・残りライブラリーに解答が何枚あるか。**数える**。`zone P1:library`（P2なら `zone P2:library`）で残りを確認してよい（軽量モードでは同じAIが両席を見ているので隠匿の問題は起きない）。
3. **間に合わない根拠** … アウトが0枚、または引く前に死ぬ、または引いても止まらない。

典型的に該当するのは次の3つ。

- 相手が無限（マナ・ライフ・ダメージ・トークン）を成立させ、こちらのデッキにその置物／クリーチャーを触れるカードが0枚。
- 詰み。相手の盤面で次のNターン以内に必ず死に、こちらの残り札にそれを覆す線が無い。
- ライブラリーアウトが確定していて、山を増やす手段も勝ち切る手段も無い。

該当しない（投了しない）例：ライフ差が大きいだけ、土地事故で盤面が薄いだけ、相手のコンボ・パーツが揃いつつあるだけ。
**まだ引いていないカードに解答があるなら、それは「アウトがある」ということなので投了しない。**

投了したゲームは `stats` に `うち投了 N件` と理由が出て、平均決着ターンも投了を除いた値が併記される。
投了時点のターンは実際に死ぬターンではないので、`--reason` に「あと何ターンで死ぬか」を書いておくと後で読める。

## 省略しないチェック

- コストと用途制限、適正な対象、応答、誘発、解決ごとのSBAを確認する。確認は同じAI内で行い、毎回別の読み込みやメモは不要。
- `stack resolve` は解決の記録だけで、スタックから取り除かない。通常は効果適用後、カードは `move`、能力は `stack pop`。`pending` 管理能力は `pending resolve` が効果・除去・完了をまとめて保存する（[区間ファイルの手順](bookkeeping.md)）。関連帰還能力は `linked resolve L1` を使う。装備の修整・付与は `fx`、カウンター総数は `counter --set` で記録する。
- 装備由来の効果は装備ごとの `--src` で分ける。`fx` の付け替えでは旧対象からの適用終了と新対象への適用が自動計算される。`fx set E1` は同じ効果の全定義を置換、別の能力解決は `fx add`。旧 `mod/grant` は `--replace-legacy` で確認して移行し、二重計上しない。`grant --clear` で付け替えの後始末をしない。
- 起動コストの生け贄でLTBが誘発する場合は、元の起動型能力→コスト支払い・生け贄→LTBの順に記帳し、上にあるLTBから解決する。`stack resolve`直後は対象・効果を確認し、別の能力の効果を適用しない。
- `combat damage`は先制・二段攻撃・トランプル・接死に対応する。該当時は`combat damage --step first --trample`→SBAで死亡を適用→`combat damage --step regular --trample`。ブロッカーが全滅してもブロック済みの状態を保持する（CR509.1h）。非トランプルは通常ステップでもダメージを割り振らず（CR510.1c）、トランプルがあれば全点を攻撃先へ自動適用する（CR702.19d。この場合は`--trample`の有無に依存しない）。F11の手動回避策は不要。決着したら通常ダメージまで続けない。割り振り→軽減判定→全体適用の順で処理する。ブロッカーがいるトランプルの超過に`--trample`を省略した場合や、プロテクションの判定が未指定の場合は全ダメージ未適用で停止する。接死は割り振りでは1点を致死量として扱い、実際に正のダメージを与えた相手だけを`sba --apply-deaths`で破壊する（破壊不能を考慮）。絆魂等の未対応警告は適用後なので、同じダメージを重ねず不足分だけ補う。
- プロテクションの色・タイプ・プレイヤー条件や軽減禁止はAIが裁定する。例: 発生源1が接死・トランプル持ち5/5、ブロッカー2が該当プロテクション持ち2/2なら`combat damage --trample --prevent 1:2`。1点をブロッカーへ割り振って全軽減し、4点をプレイヤーへ通す。条件不一致・軽減禁止なら`--unprevented 1:2`。指定はその実行だけ有効で、複数組はオプションを繰り返す。受け手はoidまたはP1/P2。プロテクションを検出した受け手への正の割り振りにはどちらか必須。プレイヤー自身のプロテクションは自動検出しないため明示する。部分軽減・置換・任意配分などは手動処理とし、対象・装備・ブロックの適否は各操作前に確認する。
- コンボの必要マナは状態（手札／戦場／墓地、酔い、初期プール、用途制限）を明記して戦略資料と照合する。過去のnoteの「5マナ」などを一般条件として流用しない。ブロックは主要な割り当てごとに残ライフ・残る始動役を比較し、生存する別案がある場合に「ブロックせざるを得ない」と断定しない。
- 無限コンボは1周のコスト・増減・繰り返し条件を自分で検証し、有限回数を宣言して記帳する。裁定に確信がなければ該当カードと公式条文を確認する。
- 一括記帳は `loop <P1|P2> <回数> --life <1周の純増> --mana <1周の純増> --draw <1周の枚数> --proof "コスト・収支・反復条件・合意"`。必要な増分だけ指定する。詳細と制限は [loop.md](loop.md) を参照。方針文書があるデッキ（`init` の「方針:」行）では、その文書の始動手順を先に読む。
- 異常時のみ直近の `log N` と現在の `show` を照合し、必要なら `undo`。軽量化のためにルール処理や状態保存を飛ばさない。
- 数手戻すときは`undo N`（N保存分）を使える。show等の読取や失敗行は数えない。過去の証跡は保持されるが、巻き戻した区間は現行の進行ではないと訂正に明記する。results.jsonl・カード／デッキファイルは戻らないため、決着後の分岐は状態を別名で扱い結果も分ける。
- `search`の候補複数は未完了として停止する。候補から`--oid`を選び、失敗行から後続の未実行操作だけを新しいバッチで続ける。既に成功したコスト支払い等を含む元バッチを先頭から再実行しない。
- `sba` 単独は報告だけで状態を変えない。戦場を離れたトークンは `sba --apply` で消滅させ、致死ダメージは裁定を確認して `sba --apply-deaths` または `move` で適用する。報告に「破壊」「消滅」と出ただけで処理済みにしない。
- 決着ターンは各プレイヤーの何ターン目かも併記する。CLIの `Turn` と `stats` は両席のターンを通算する（例：先手P1なら T8 P2 は後手4ターン目）。

## 操作の早見表

既知の操作はここを利用し、未知のオプションだけ個別の `-h` を読む。

| 目的 | コマンド |
|---|---|
| 登録デッキのメイン確認 | `deck show boros-tokens --brief` |
| カード全文の確認 | `card show <名前>`。**未キャッシュの名前は `[missing]` と出して終了コード0で終わる**（取得しない）ので、その場合は `card fetch <名前>` で取り直す。`cards/` は.gitignore対象なのでクローン直後は全カードが未キャッシュ |
| 残りライブラリーの確認 | `zone P1:library`（P2なら `zone P2:library`。投了判断でのアウト確認用） |
| ライブラリーの上からN枚を見る | `look P1 4`（上から4枚。移動しない。効果などで見る必要があるときだけ） |
| 呪文をスタックへ | `stack push <oid> --cast --controller P1`（詠唱を明示して回数記録・castメモ表示。`--cast`省略は詠唱扱いしない） |
| 誘発の処理待ちを記帳 | `pending add "果敢" --controller P1 --src <oid>`（srcは省略可、1回につき1登録）→ 順序・対象確認後 `pending stack T1 --targets <oid>`（対象なしならtargets省略）。**自分の誘発が複数あるときは後に解決したいものを先に積む**（後入れ先出し）。積んだ後に順序を変える手段は無く`undo`で戻すしかないので、積む前に解決順を決める |
| 処理待ちの一覧 | `pending list`／`pending list --all`（linkedの帰還も参照表示。重複登録しない） |
| 使い捨てのバッチを標準入力から流す | `... run - --compact`（`-` は標準入力。構文検査を通った内容は状態ファイル横の `<state名>-NNN.mtg` に自動保存され、「操作ファイル: <パス>」と出る。既存の番号は上書きしない）。ファイルを自分で書く従来の手順もそのまま使える |
| 台帳の能力を解決 | 応答確認後 `pending resolve T1 --file <実ファイル> --part bonus`、または1〜2行なら `pending resolve T1 --do "counter 3 +1/+1 1" --part bonus`（`--do` は繰り返し可。`--file` とは併用不可。成功時に連番`.mtg`へ自動保存）（効果と完了を一括保存。結果を見て続けるなら `--pause`、続きは別part。手順は [bookkeeping.md](bookkeeping.md)）。**解決用ファイルに`sba`は書けない**（「解決用ファイルで使用できないコマンドです」で停止）ので、格闘などの死亡確認は`pending resolve`の後に別途`sba --apply-deaths`を打つ |
| 台帳の能力を取消し | `pending cancel T1 --reason "打ち消し"`（理由必須。解決途中はundoで戻す） |
| 確認メモ | `remind add "果敢を確認" --on cast --player P1 --src <oid>`（on必須：cast/enter/turn。player・src省略可。自動誘発なし）／`remind list`／`remind remove R1` |
| 能力をスタックへ | `stack push "説明" --ability --controller P1 --src <発生源oid> --ability-key etb --targets <oidまたはP2>`（発生源の世代を保存） |
| 装備先の指定 | `attach <装備品oid> --to <クリーチャーoid>` |
| 装備の解除 | `attach <装備品oid> --detach`（`--to` は取らない）。装備先が除去されたときに使う。**解除しても`fx`の定義は残る**ので、同じ装備品を別のクリーチャーへ付けるときは`attach`だけにし、`fx add`を再実行しない |
| 装備ごとの修整 | `mod <対象oid> +1/+0 --until attached --src <装備品oid>`（別装備は別行） |
| 装備品の付け替え | **`attach <装備品oid> --to <新しい対象>` だけ**を実行する。`fx add`は装備品が初めて戦場に出たときの1回きり。付け替えのたびに`fx add`を書くと同一src・同一abilityの定義が二重に残り、`fx add`は警告を出さないのでP/Tが静かに膨らむ。疑わしいときは`fx list`で同じsrcの行が2つ無いか確認し、余分な方を`fx remove E<n>` |
| 静的な全体修整（アンセム） | `fx` の `--scope` では「自軍の該当タイプ全部」を表現できない。対象1体ずつ `mod <oid> +N/+0 --until permanent` を入れ、係数の根拠を `note` に残す。**発生源の数が変わっても自動再計算されない**ので、増減したら`mod`を引き直す |
| 装備効果を自動計算へ登録 | `fx add --src <装備品oid> --ability bonus --pt +1/+0 --grant トランプル`（以後の付け替えはattachだけ。旧mod/grantがあれば定義確認後 `--replace-legacy`） |
| 複数キーワードの付与 | `fx add --src <oid> --ability bonus --pt +1/+0 --grant 速攻 "護法{1}"`（`--grant`は1回にまとめる。2回書くと後勝ちで上書きされ、消えた付与は`fx list`か攻撃時の召喚酔い警告まで気付けない） |
| 発生源のカウンターに連動 | `fx add --src <装備品oid> --ability bonus --counter charge --per-counter +1/+0`（カードの実際の条件・係数を確認）。`fx add` の確認出力にはカウンター指定が出ないため、登録確認は `fx list` の `counter=<名前>*[P, T]` で行う |
| 効果の訂正・取消し | `fx set E1 --src <oid> --ability bonus --pt +2/+0`（全定義置換）／`fx remove E1` |
| 効果と関連の確認 | `fx list`／`fx check`／`linked list`（保存・undo履歴を増やさない） |
| 解決時の対象だけ強化 | `fx add --src <oid> --ability pump --scope fixed --targets 1 2 --until eot --pt +2/+2` |
| 関連追放 | `linked exile 1 --src 5@1 --ability etb --return until-source-leaves`（発生源の世代はzoneで確認。スタックに記録済みなら `--src/--ability` の代わりに `--via <能力oid>`） |
| 帰還誘発・遅延帰還 | 追放時に `--return leave-trigger`／`--return next-end`。発生後 `linked trigger L1` → 応答確認 → `linked resolve L1`（打ち消しなら `linked counter L1`） |
| 追放カードのプレイ許可 | 追放時に `--play-until eot --player P1` → `linked play L1 1 --player P1`（呪文は詠唱回数・castメモに反映、土地は含めない。支払い・対象・追加制限は別途確認） |
| 旧方式の能力付与 | `grant <対象oid> トランプル --until attached --src <付与元oid>`（`attached` は省略時も既定で `--src` 必須。実際の装着先と一致が必要。fx管理中は使用不可） |
| 旧付与を発生源ごと取消し | `grant <対象oid> --clear --src <付与元oid>`（src省略は他の発生源分も全削除。fxはfx removeで取消し） |
| サーチ（カード名で探す） | `search P2 "Chrome Dome" --to battlefield`（名前は位置引数。`--name`は無い。候補が複数なら未完了で停止する）。**`--tapped`は無い**ので「タップ状態で戦場に出す」効果は`search`の次の行に`tap <oid>`を書く。寓話の小道のように直後にアンタップする効果なら、タップ行を省いてその旨を`note`に残す |
| サーチの選択 | `search P2 --oid <候補oid> --to hand`（既定でシャッフル） |
| 複数手の復旧 | `undo 3`（3保存分。指定不足・超過は変更せず停止） |
| Classレベルの記帳 | `effect add "[oid] Class level 2: 適用する能力"`（表示用メモ。カードの能力・支払いは別途確認） |
| 土地をクリーチャー化（土の技・ミシュラランド） | `card set --oid <土地oid> --types Land/Creature --power 0 --toughness 0` → `counter <oid> +1/+1 N` → 説明を`effect add`。**P/Tを登録しないと攻撃・ブロック宣言は通り、`combat damage`で「P/T未登録」と出て全体停止する** |
| 盤面の説明メモ | `effect add "現在の適用条件" --until eot`（showに常時表示。期限省略はpermanent）。場面ごとの確認はremind、確定した誘発はpending、数値効果はmod/grant/fxへ記帳 |
| 応答なしの解決 | `pass P1` → `pass P2` → 通常は`stack resolve` → 効果適用 → カードは`move`、能力は`stack pop`。台帳の能力は`pending resolve`、帰還能力は`linked resolve`で完了まで処理 |
| マナ | `tap <oid...>` → `mana P1 add WR` → `mana P1 spend WR`（生成色・量・用途制限は確認） |
| SBA | `sba --apply`（トークン消滅・装着先のない通常オーラ）／裁定確認後の `sba --apply-deaths`（同時死亡後も再評価。授与・置換等は先に裁定） |
| 土地を置く | `move <oid> battlefield`（タップインは`--tapped`。`play P1`ではない） |
| クリーチャーへのダメージ | `damage <oid> <点数>`（**発生源を渡すオプションは無い**。格闘・火力の発生源や絆魂・接死の判定は`note`と手動処理で補う） |
| ライフ変更 | `life P2 -2`（`--reason`はない。必要な理由だけnote） |
| 攻撃宣言 | `attack <oid...> --target <相手の席>`（P1が攻めるなら`--target P2`、P2が攻めるなら`--target P1`。**攻撃側自身の席を指定してもCLIは警告せず、自分にダメージが入る**。席を先頭に置かない。警戒は`--no-tap`） |
| 戦闘ダメージ | 応答・誘発・ブロックを確認し`combat.damage`へ進めてから`combat damage`。別ステップや非空スタックでは停止する。先制・二段攻撃は`combat damage --step first --trample`→`sba --apply-deaths`→未決着なら`combat damage --step regular --trample`。ブロック済みでブロッカー不在なら非トランプルは0点、トランプルは全点を自動適用。`combat show`でもブロック済みと表示する。超過があるのに`--trample`を省略すると全体未適用で停止 |
| 接死と軽減 | 接死は割り振りと実被ダメージを自動記録→`sba --apply-deaths`。全軽減は`combat damage --trample --prevent <発生源oid>:<受け手oid/P1/P2>`。プロテクション非適用・軽減禁止の裁定は`--unprevented <発生源oid>:<受け手>`。複数組は繰り返す。検出されたプロテクションの判定未指定・矛盾・割り振りにない組は全体未適用で停止。各ステップで指定し直す。部分軽減・置換は手動 |
| 次ターン | `turn next --to precombat_main --draw`（途中の誘発があるならそこで区切る） |
| 攻撃中のノーム | `token P1 Gnome --types Artifact/Creature --subtypes Gnome --power 1 --toughness 1 --attacking -n 2` |
| トークンのoid | 採番は生成時の出力（`トークン生成: 英雄(125)`）でしか分からない。**生成行の直後でバッチを区切り**、oidを読んでから`attach`／`fx add`／`stack push --src`を書く（推測すると`oid ... は存在しません`で停止する） |
| 装備品トークンを生成してつける | `token P1 "斧" --types Artifact --subtypes Equipment --oracle "装備しているクリーチャーは＋１/＋０の修整を受ける。装備{2}"` → `fx add --src <トークンoid> --ability bonus --pt +1/+0`（カウンター連動があれば `--counter <名前> --per-counter +1/+0` を併記）→ `attach <トークンoid> --to <クリーチャーoid>`。`--power/--toughness` は非クリーチャーなので指定しない |
| 可変収支のピザ反復 | 1周検証後だけ [pizza-recipe.md](pizza-recipe.md) を読む |
| 決着後の消化ターンを畳む | `concede P2 --reason "勝ち手順・アウト枚数・間に合わない根拠" --tag T`（基準は上の節） |

フェイズ名：`beginning.untap` → `beginning.upkeep` → `beginning.draw` → `precombat_main` → `combat.begin` → `combat.attackers` → `combat.blockers` → `combat.damage` → `combat.end` → `postcombat_main` → `ending.end` → `ending.cleanup`。`phase to <名前>`で指定する。`combat.declare_attackers`・`end.end`は無効。

同じseedはAIの選択を固定しない。初期配置の比較と操作列の再実行を区別する。現行は初期シャッフルが席別、対局中の乱数連番は両席共有。マリガン判断や乱数操作の順序を変えると引き直しも変わる。再現試行ではデッキの入力順・実装／乱数方式・操作列も揃える。
