# 軽量な処理待ち管理

<!-- toc -->
- [登録から解決](#登録から解決)
- [結果を見て続ける解決](#結果を見て続ける解決)
- [短い確認メモと詠唱申告](#短い確認メモと詠唱申告)
<!-- /toc -->

ルール判断はAI、保存と重複適用の防止はスクリプトが担当する。果敢・上陸の有無、回数、対象、順序、応答、置換効果を確認してから記帳する。自動検出・自動解決はしない。

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
