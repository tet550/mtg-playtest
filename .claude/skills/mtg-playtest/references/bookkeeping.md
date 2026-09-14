# 軽量な処理待ち管理

<!-- toc -->
- [登録から解決](#登録から解決)
- [結果を見て続ける解決](#結果を見て続ける解決)
- [短い確認メモと詠唱申告](#短い確認メモと詠唱申告)
<!-- /toc -->

ルール判断はAI、保存と重複適用の防止はスクリプトが担当する。果敢・上陸の有無、回数、対象、順序、応答、置換効果を確認してから記帳する。ステップ開始時は候補だけを自動検出する。誘発の成立や解決は自動判定しない。

## タイミングの確認と予約

戦場のテキストから、アップキープ・ドロー・戦闘前/第1メイン・戦闘後/第2メイン・戦闘開始・戦闘終了・終了ステップの明示的な誘発候補を検出し、`pending list`へ`review`として登録する。`turn next --to ...`、`phase to/set`、両者pass、攻撃宣言への自動進行はその地点で停止する。`run`も確認が発生した行を保存して停止し、後続行は実行しない（`--keep-going`でも同じ）。出力を読んでから未実行の操作だけを続ける。

```text
pending list
pending confirm T1 --reason "アップキープ誘発の条件が成立"
pending stack T1
# 応答確認後。任意の切削を選んだ例
pending resolve T1 --do "mill P2 1" --part mill
# 切削しない場合も誘発を解決する: --do 'note "切削しないことを選択"'
```

`confirm`は既存T番号を`pending`にする。重複して`pending add`しない。不成立なら`pending cancel T1 --reason "条件不成立の根拠"`。候補ごとに成立・APNAP順・対象を判断する。確認待ちのまま次のフェイズ・通常ドロー・攻撃へは進めない。

ドロー・ステップでは通常ドローが先。`turn ... --draw`は先に引く。`phase`経由で入った場合は`draw <アクティブ席> 1`を行ってから確認する。置換やドロー省略を別途適用した場合だけ、根拠を`note`し`draw <アクティブ席> 0`で通常ドロー確認を完了する。

英雄譚は戦闘前メインで`review`を作る。AIが伝承カウンターを追加し、実際の章能力を`pending add`へ登録後、`pending confirm T1 --reason "伝承追加・章能力T2登録済み"`で確認を完了する。この確認項目自体はスタックへ積まない。

遅延誘発は生成した解決の時点で予約する。次の終了ステップの例:

```text
pending schedule "動員で生成した[12][13]を生け贄に捧げる" --controller P1 --src 3 --at ending.end
```

`--at`は必須。自分の次のアップキープなど席指定が必要なら`--at beginning.upkeep --player P1`。現在のステップと同じタイミングを予約した場合は、次の到来を待つ。発生源が退場しても予約は残る。対象・条件は本文に記録し、到来時の`review`を確認してから解決する。予約取消しは`pending cancel ... --reason ...`。

`pending resolve --do 'pending schedule ...' --part ...`も使えるため、生成操作と予約を同一区間で保存・undoできる。登録済みの`linked --return next-end`はそのまま使い、同じ帰還を二重予約しない。

検出は日英の明示的な開始時表現と`at end of combat`に限定する。任意選択・if条件はAIが裁定する。引用された付与能力、注釈文、`next/次の`の遅延効果、持続期間は自動登録しない。キーワードだけで省略された能力、戦場以外の能力、汎用的な「各メイン」、アンタップ/クリンナップの特殊処理、追加ステップは自動検出対象外。必要な予約・手動登録を行い、候補なしを「誘発なし」の保証にしない。

## 登録から解決

以下は `mtg.py` に渡す引数。oidとT番号は実際の表示に置き換える。

```text
pending add "果敢：このクリーチャーに+1/+1" --controller P1 --src 3
pending stack T1
pending list
```

誘発1回につき1つ登録する。同名能力も別IDとして保持する。`--src` は `3@1` のような世代指定も可能。規則由来など発生源がなければ省略できる。対象は `pending stack T1 --targets 4` で指定する。適正・順序はAIが確認する。

T番号は`pending add`の出力を読んでから指定し、過去の番号や推測した連番を使わない。同じrun内の確定操作には`pending add ... --label etb`→`pending stack $etb`の別名参照も使える（[バッチ入力](ai-batches.md#採番待ちを減らす入力)）。複数体のETBでそれぞれ誘発した能力を、合計ライフ変更1回の能力にまとめない。各誘発の応答・解決・SBAを保つ。

応答を確認後、UTF-8の実ファイル（例 `prowess.txt`）へ既存コマンドを書く。各行にpythonやスクリプト名は付けない。

```text
mod 3 +1/+1 --until eot
```

```text
pending resolve T1 --file prowess.txt --part bonus
```

短い解決は `pending resolve T1 --do "mod 3 +1/+1 --until eot" --part bonus` と直接書ける。`--do`は繰り返し可、`--file`とは併用不可。成功時の入力ファイル保存はCLIが行う。ID・空でない`--part`・入力元は必須で、`run`ではこれらの不足をバッチ全体の実行前に検出する。IDが現在解決可能かどうかは実行時に検査する。

全行が成功した場合だけ、効果・能力の除去・完了記録を1回保存する。途中で失敗すればその区間は未適用。完了IDや適用済み区間名の再実行は拒否する。`undo 1` は効果と区間記録を一緒に戻す。`pending list --all` で完了・取消済みも確認できる。

打ち消しや誤登録の取消しは `pending cancel T1 --reason "打ち消された"`。途中まで解決した能力は取消せないので、訂正は `undo` を使う。台帳に結び付いた能力へ `stack pop` や `move` は使わない。

取消済みIDは履歴として残り、再利用しない。新しい処理は`pending add`で登録する。事前にT番号を書いたバッチは取消しや追加登録で番号がずれるため、新規登録は次のように別名で参照する（同じrun内のみ）。別runでは実際の出力IDを使う。取消し自体が誤りなら[undoの復旧手順](cli-guide.md#undoによる復旧)で取消し前へ戻す。

```text
pending add "新しい誘発" --controller P1 --label trigger
pending stack $trigger
```

シェルに`$trigger`を展開させないよう、バッチファイルまたは引用付きの入力を使う。詳細は[バッチの別名](ai-batches.md)。

## 結果を見て続ける解決

ドロー・サーチ・look・乱数・pickはファイルの最後に置く。その結果から後続の処理を判断する場合は `--pause` を付ける。

```text
pending resolve T2 --file draw.txt --part draw --pause
pending resolve T2 --file chosen-effects.txt --part after-draw
```

区間名は同じT番号内で一意にする。解決途中は表示、メモ、誘発の登録、解決の続き、undoが可能。別の効果操作やSBA・進行は停止する。途中で気付いた誘発は `pending add` で記録し、現在の解決完了と必要なSBA後にスタックへ置く。追加の効果がなければ空のファイルを最終区間にできる。

使用可能なのは `move/tap/untap/life/counter/damage/mod/grant/mana/token/attach/pcounter/draw/mill/search/shuffle/bottom/look/roll/coin/pick/note/show/hand`、`fx add/set/remove`、`linked exile`。キャッシュ更新・外部結果出力・run・フェイズ・スタック操作は含めない。カード情報は事前に用意し、区間実行はオフラインで行う。

未登録の処理待ちがある間は、pass・フェイズ進行・通常のstack push/resolve等を止める。解決開始前の効果記帳やSBAは可能。`linked` の帰還誘発も `pending list` に表示するが、処理は `linked trigger/resolve/counter` を使い、T番号へ重複登録しない。

## 短い確認メモと詠唱申告

```text
remind add "非クリーチャー呪文なら果敢を確認" --on cast --player P1 --src 3
remind add "土地なら上陸の回数を確認" --on enter --player P1 --src 5
remind add "前ターンの詠唱数と昼夜を確認" --on turn
remind list
remind remove R1
stack push 8 --cast --controller P1
```

`cast` は `stack push --cast` の明示申告時、`enter` は戦場に出たとき、`turn` はターン開始時に表示する。enterは土地以外も含み、複数トークン等は件数をまとめて通知する。`--player` 省略は両席。`--src` 指定時はその発生源の世代が終わると通知を止める。能力の有効性・ゾーン条件までは判定しない。

`--cast` はターン・席別の `cast_counts` に加算する。`linked play` で追放から呪文を唱えた場合も同じ記録とcastメモを更新する（土地は除外）。省略したstack pushやコピーを詠唱と推定しないため、過去の未申告分を含む完全な詠唱履歴ではない。メモはpendingを自動生成しない。候補やメモが出ないことも「誘発なし」の保証にはならない。

戦場登場の候補とenterメモは同じ確認欄にまとめる。同時に複数出た場合、同じ候補の文章は登場件数を添えて1回表示する。候補の件数は確定した誘発回数ではない。発生源が同じでも別能力があるため、メモが登録されているという理由だけで候補を省略しない。

`effect` は現在の盤面の説明（showに常時表示、eot指定可）、`remind` は指定場面の確認、`pending` は確定した処理待ち、と使い分ける。同じ確認事項をeffectとremindの両方へ登録しない。いずれも数値効果の代わりにはならず、実際の修整にはmod/grant/fxを使う。

通常のrunと解決用ファイルは、UTF-8（BOM可）、空行・行頭コメント、引用符、行番号付きの構文検査を共有する。保存はrunが1行ごと、pending resolveが1区間ごとで異なる。

速度・昼夜・紋章の専用状態と自動判定は今回追加しない。常在型能力・装備・関連追放は既存の [relations.md](relations.md) の対応範囲を使う。
