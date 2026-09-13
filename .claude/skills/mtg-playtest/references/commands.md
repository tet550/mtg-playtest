# 操作の早見表

<!-- toc -->
- [開始と表示](#開始と表示)
- [手札と探索](#手札と探索)
- [スタックと誘発](#スタックと誘発)
- [装備と継続効果](#装備と継続効果)
- [関連追放](#関連追放)
- [戦闘とダメージ](#戦闘とダメージ)
- [その他の操作](#その他の操作)
- [フェイズと再現性](#フェイズと再現性)
<!-- /toc -->

該当する節だけ読む。不明なオプションは`mtg.py <command> -h`で確認する。

## 開始と表示

| 目的 | コマンド |
|---|---|
| 対戦開始・事前キャッシュ | `init --deck1 piza --deck2 boros-dwarves --seed 101 --first P1`。両デッキのメイン・サイドボードの不足・未解決カードを自動取得し、全件確認後に保存。取得失敗は状態未保存で停止。`--offline`はキャッシュ完備時のみ開始可。`--prefetch`は互換用で省略可 |
| OID中心の盤面表示 | `show --ids --hand both`（手札共有時）。カード名を省略し状態を保持。初手・再開・対応確認には`show --hand both`。`--packed`で手札を3枚ずつ表示（コスト・タイプ・P/Tを保持）。`--flat`・`--next-oid`・`run --compact --delta`と併用可。能力の説明と裁定メモは省略しない |
| 人向けターン開始履歴 | `init --deck1 piza --deck2 boros-dwarves --seed 101 --history-hand both`。`output/<state名>/turn-starts.md`へ名前付きshowを自動追記。手札の既定はnone、P1/P2/bothを指定可。初回はT1のアンタップ・ステップを初めて進める直前（初手・マリガン後）、以降はturn進行のアンタップ後・ドロー前。成功した操作だけ記録し、undoは訂正を追記する。対人・席分離はnoneまたは自分の席だけを選ぶ |
| 登録デッキのメイン確認 | `deck show boros-tokens --brief` |

## 手札と探索

| 目的 | コマンド |
|---|---|
| カード全文の確認 | `card show "<名前1>" "<名前2>" ...`（各名前を引用、showのみ複数可）。**未キャッシュの名前は `[missing]` と出して終了コード0で終わる**（取得しない）ので、その場合は `card fetch <名前>` で取り直す。`cards/` は.gitignore対象なのでクローン直後は全カードが未キャッシュ |
| 残りライブラリーの確認 | `zone P1:library`（P2なら `zone P2:library`。投了判断でのアウト確認用） |
| ライブラリーの上からN枚を見る | `look P1 4`（上から4枚。移動しない。効果などで見る必要があるときだけ） |
| 見た残りを無作為順で一番下へ | 例：残りが24・3・51なら`pick 24 3 51`の結果を読み、そのoidを`move <oid> library`。未選択の2枚で再度pickして同様に移し、最後の1枚も移す。対局のseed付き乱数を使い、ライブラリー全体をshuffleしない。解決中はpickを区間末尾に置いて`--pause`し、結果を読むたび別partで続ける |
| サーチ（カード名で探す） | `search P2 "Chrome Dome" --to battlefield`（名前は位置引数。`--name`は無い。候補が複数なら未完了で停止する）。**`--tapped`は無い**ので「タップ状態で戦場に出す」効果は`search`の次の行に`tap <oid>`を書く。寓話の小道のように直後にアンタップする効果なら、タップ行を省いてその旨を`note`に残す |
| サーチの選択 | `search P2 --oid <候補oid> --to hand`（既定でシャッフル） |

## スタックと誘発

| 目的 | コマンド |
|---|---|
| 呪文をスタックへ | `stack push <oid> --cast --controller P1`（詠唱を明示して回数記録・castメモ表示。`--cast`省略は詠唱扱いしない） |
| 誘発の処理待ちを記帳 | `pending add "果敢" --controller P1 --src <oid>`（srcは省略可、1回につき1登録）→ 順序・対象確認後 `pending stack T1 --targets <oid>`（対象なしならtargets省略）。**自分の誘発が複数あるときは後に解決したいものを先に積む**（後入れ先出し）。積んだ後に順序を変える手段は無く`undo`で戻すしかないので、積む前に解決順を決める |
| 処理待ちの一覧 | `pending list`／`pending list --all`（linkedの帰還も参照表示。重複登録しない） |
| 台帳の能力を解決 | 応答確認後 `pending resolve T1 --file <実ファイル> --part bonus`、または1〜2行なら `pending resolve T1 --do "counter 3 +1/+1 1" --part bonus`（`--do` は繰り返し可。`--file` とは併用不可。成功時に連番`.mtg`へ自動保存）（効果と完了を一括保存。結果を見て続けるなら `--pause`、続きは別part。手順は [bookkeeping.md](bookkeeping.md)）。**解決用ファイルに`sba`は書けない**（「解決用ファイルで使用できないコマンドです」で停止）ので、格闘などの死亡確認は`pending resolve`の後に別途`sba --apply-deaths`を打つ |
| 台帳の能力を取消し | `pending cancel T1 --reason "打ち消し"`（理由必須。解決途中はundoで戻す） |
| 確認メモ | `remind add "果敢を確認" --on cast --player P1 --src <oid>`（on必須：cast/enter/turn。player・src省略可。自動誘発なし）／`remind list`／`remind remove R1` |
| 能力をスタックへ | `stack push "説明" --ability --controller P1 --src <発生源oid> --ability-key etb --targets <oidまたはP2>`（発生源の世代を保存） |
| 両者パスの短縮 | バッチ内の `pass-both P1`（P1→P2）／`pass-both P2`（P2→P1）。席制限なし専用。AIが両席の応答なしを確認してから指定。証跡・保存は既存pass2行のまま |
| 応答なしの解決 | `pass P1` → `pass P2` → 通常は`stack resolve` → 効果適用 → カードは`move`、能力は`stack pop`。台帳の能力は`pending resolve`、帰還能力は`linked resolve`で完了まで処理 |

## 装備と継続効果

| 目的 | コマンド |
|---|---|
| 装備先の指定 | `attach <装備品oid> --to <クリーチャーoid>` |
| 装備の解除 | 装備先が戦場を離れると自動解除。効果で明示的に外す場合だけ `attach <装備品oid> --detach`（`--to` は取らない）。**解除しても装備品の`fx`定義は残る**ので、再装備は`attach`だけ |
| 装備ごとの修整 | `mod <対象oid> +1/+0 --until attached --src <装備品oid>`（別装備は別行） |
| 装備品の付け替え | **`attach <装備品oid> --to <新しい対象>` だけ**を実行する。同じ発生源・世代・abilityの装着効果を`fx add`で重複登録すると変更前に停止する。訂正は`fx set E<n>`。装備品自体が戦場を離れて戻ったら新しい世代として再登録する |
| 静的な全体修整（アンセム） | 自軍の全クリーチャーなら `fx add --src <発生源oid> --ability anthem --scope controller-creatures --pt +1/+1`。新規クリーチャー・コントローラー変更・発生源の退場を自動反映する。クリーチャー・タイプ等の条件絞り込みは未対応なのでAIが個別に管理する。移行時は同じ修整の旧modを除いて二重計上を防ぐ。ETB等の誘発は別途pendingへ |
| 装備効果を自動計算へ登録 | `fx add --src <装備品oid> --ability bonus --pt +1/+0 --grant トランプル`（以後の付け替えはattachだけ。旧mod/grantがあれば定義確認後 `--replace-legacy`） |
| 複数キーワードの付与 | `fx add --src <oid> --ability bonus --pt +1/+0 --grant 速攻 "護法{1}"`（`--grant`は1回にまとめる。2回書くと後勝ちで上書きされ、消えた付与は`fx list`か攻撃時の召喚酔い警告まで気付けない） |
| 発生源のカウンターに連動 | `fx add --src <装備品oid> --ability bonus --counter charge --per-counter +1/+0`（カードの実際の条件・係数を確認）。`fx add` の確認出力にはカウンター指定が出ないため、登録確認は `fx list` の `counter=<名前>*[P, T]` で行う |
| 効果の訂正・取消し | `fx set E1 --src <oid> --ability bonus --pt +2/+0`（全定義置換）／`fx remove E1` |
| 効果と関連の確認 | `fx list`／`fx check`／`linked list`（保存・undo履歴を増やさない） |
| 解決時の対象だけ強化 | `fx add --src <oid> --ability pump --scope fixed --targets 1 2 --until eot --pt +2/+2` |
| 旧方式の能力付与 | `grant <対象oid> トランプル --until attached --src <付与元oid>`（`attached` は省略時も既定で `--src` 必須。実際の装着先と一致が必要。fx管理中は使用不可） |
| 旧付与を発生源ごと取消し | `grant <対象oid> --clear --src <付与元oid>`（src省略は他の発生源分も全削除。fxはfx removeで取消し） |
| 装備品トークンを生成してつける | `token P1 "斧" --types Artifact --subtypes Equipment --oracle "装備しているクリーチャーは＋１/＋０の修整を受ける。装備{2}"` → `fx add --src <トークンoid> --ability bonus --pt +1/+0`（カウンター連動があれば `--counter <名前> --per-counter +1/+0` を併記）→ `attach <トークンoid> --to <クリーチャーoid>`。`--power/--toughness` は非クリーチャーなので指定しない |

## 関連追放

| 目的 | コマンド |
|---|---|
| 関連追放 | `linked exile 1 --src 5@1 --ability etb --return until-source-leaves`（発生源の世代はzoneで確認。スタックに記録済みなら `--src/--ability` の代わりに `--via <能力oid>`） |
| 帰還誘発・遅延帰還 | 追放時に `--return leave-trigger`／`--return next-end`。発生後 `linked trigger L1` → 応答確認 → `linked resolve L1`（打ち消しなら `linked counter L1`） |
| 追放カードのプレイ許可 | 追放時に `--play-until eot --player P1` → `linked play L1 1 --player P1`（呪文は詠唱回数・castメモに反映、土地は含めない。支払い・対象・追加制限は別途確認） |

## 戦闘とダメージ

| 目的 | コマンド |
|---|---|
| クリーチャーへのダメージ | `damage <oid> <点数>`（**発生源を渡すオプションは無い**。格闘・火力の発生源や絆魂・接死の判定は`note`と手動処理で補う） |
| 攻撃宣言 | `attack <oid...> --target <相手の席>`（P1が攻めるなら`--target P2`、P2が攻めるなら`--target P1`。**攻撃側自身の席を指定してもCLIは警告せず、自分にダメージが入る**。席を先頭に置かない。警戒は`--no-tap`。固有の無条件の速攻と付与された速攻は警告判定に反映する。条件付きの速攻や他者に与える速攻は本文で確認する） |
| 戦闘ダメージ | 応答・誘発・ブロックを確認し`combat.damage`へ進めてから`combat damage`。別ステップや非空スタックでは停止する。先制・二段攻撃は`combat damage --step first --trample`→`sba --apply-deaths`→未決着なら`combat damage --step regular --trample`。ブロック済みでブロッカー不在なら非トランプルは0点、トランプルは全点を自動適用。`combat show`でもブロック済みと表示する。超過があるのに`--trample`を省略すると全体未適用で停止 |
| 接死と軽減 | 接死は割り振りと実被ダメージを自動記録→`sba --apply-deaths`。全軽減は`combat damage --trample --prevent <発生源oid>:<受け手oid/P1/P2>`。プロテクション非適用・軽減禁止の裁定は`--unprevented <発生源oid>:<受け手>`。複数組は繰り返す。検出されたプロテクションの判定未指定・矛盾・割り振りにない組は全体未適用で停止。各ステップで指定し直す。部分軽減・置換は手動 |
| 攻撃中のノーム | `token P1 Gnome --types Artifact/Creature --subtypes Gnome --power 1 --toughness 1 --attacking -n 2` |

## その他の操作

| 目的 | コマンド |
|---|---|
| 使い捨てのバッチを標準入力から流す | `... run - --compact`（`-` は標準入力。構文検査を通った内容は状態ファイル横の `<state名>-NNN.mtg` に自動保存され、「操作ファイル: <パス>」と出る。既存の番号は上書きしない）。ファイルを自分で書く従来の手順もそのまま使える |
| 複数手の復旧 | `undo 3`（3保存分。指定不足・超過は変更せず停止） |
| Classレベルの記帳 | `effect add "[oid] Class level 2: 適用する能力"`（表示用メモ。カードの能力・支払いは別途確認） |
| 土地をクリーチャー化（土の技・ミシュラランド） | `card set --oid <土地oid> --types Land/Creature --power 0 --toughness 0` → `counter <oid> +1/+1 N` → 説明を`effect add`。**P/Tを登録しないと攻撃・ブロック宣言は通り、`combat damage`で「P/T未登録」と出て全体停止する** |
| 盤面の説明メモ | `effect add "現在の適用条件" --until eot`（showに常時表示。期限省略はpermanent）。場面ごとの確認はremind、確定した誘発はpending、数値効果はmod/grant/fxへ記帳 |
| マナ | `tap <oid...>` → `mana P1 add WR` → `mana P1 spend WR`（生成色・量・用途制限は確認） |
| SBA | `sba --apply`（トークン消滅・装着先のない通常オーラ）／裁定確認後の `sba --apply-deaths`（同時死亡後も再評価。授与・置換等は先に裁定） |
| トークンを生け贄に捧げる | 置換効果がなければ`move <oid> graveyard`、解決完了後に`sba --apply`。追放へ置き換えない。死亡・戦場を離れる誘発を確認して個別にpendingへ登録する |
| 土地を置く | `move <oid> battlefield`（タップインは`--tapped`。`play P1`ではない） |
| ライフ変更 | `life P2 -2`（`--reason`はない。必要な理由だけnote） |
| 次ターン | `turn next --to precombat_main --draw`（途中の誘発があるならそこで区切る） |
| 定型トークン | `token P2 --preset clue`／`--preset treasure`／`--preset lander`。名前・タイプ・本文を既定義し、個別定義との併用不可。`-n`・`--tapped`は併用可。能力の起動や誘発は別途記帳 |
| バッチ内の別名 | `pending add "ETB" --controller P2 --label etb` → `pending stack $etb`。`token P2 --preset clue --label clue` → `tap $clue`。`pending resolve $etb --do "token P2 --preset clue" --part create --label clue`でも生成1個に命名可。別名は同じrun内のみ、定義後の独立した`$name`トークンと`--do`内で使用。`--src=$name`や`--file`内の置換は不可。undo/initとの同一バッチ併用不可。シェルの展開を防ぐ引用付きheredoc等で渡す |
| イベントの証跡 | 席制限なしの`run --compact`内で `note "重要な行動と結果" --event E01`。同じゲームのID重複・秘匿指定は不可。解決用の`--do`／`--file`内では使わず、runの直下に置く。現在のバッチの先頭（前のeventがあればその直後）〜当該noteの物理行範囲を証跡へ保存。通常noteは従来どおり |
| 報告草稿 | `python .claude/skills/mtg-playtest/scripts/report_draft.py playtest/<対局> --game g01 --seed 123`。`report-draft.md`を新規作成。`--out`は同じ対局フォルダ直下の未使用パスのみ。明示的なevent・結果・現在のログを照合し、undo/init入り証跡や不整合は停止。裁定・所見・キープ枚数は記入後check_reportで検査 |
| トークンのoid | 採番は生成時の出力（`トークン生成: 英雄(125)`）でしか分からない。生成結果で判断が変わる場合は**生成行の直後でバッチを区切り**、oidを読んでから`attach`／`fx add`／`stack push --src`を書く（推測すると`oid ... は存在しません`で停止する）。生成1個で後続操作が確定している場合は上記`--label`を使える |
| 可変収支のピザ反復 | 1周検証後だけ [pizza-recipe.md](pizza-recipe.md) を読む。現行生成器の末尾は手札なしの`show`なので、軽量方式では生成ファイル末尾を実行前に`show --hand both`へ変更する（対人・席分離には適用しない）。生成器は誘発を旧式のstack操作で記帳し、pending台帳と発生源参照は作らない |
| 決着後の消化ターンを畳む | `concede P2 --reason "勝ち手順・アウト枚数・間に合わない根拠" --tag T`（[投了条件](concede.md)を先に確認） |

## フェイズと再現性

フェイズ名：`beginning.untap` → `beginning.upkeep` → `beginning.draw` → `precombat_main` → `combat.begin` → `combat.attackers` → `combat.blockers` → `combat.damage` → `combat.end` → `postcombat_main` → `ending.end` → `ending.cleanup`。`phase to <名前>`で指定する。`combat.declare_attackers`・`end.end`は無効。

同じseedはAIの選択を固定しない。初期配置の比較と操作列の再実行を区別する。現行は初期シャッフルが席別、対局中の乱数連番は両席共有。マリガン判断や乱数操作の順序を変えると引き直しも変わる。再現試行ではデッキの入力順・実装／乱数方式・操作列も揃える。
