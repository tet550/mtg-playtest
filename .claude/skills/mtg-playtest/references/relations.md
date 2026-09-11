# 装着効果・関連追放の記帳

装備・オーラの継続効果、同じ発生源による追放、期限や適用先を管理するときに読む。
ここに掲載した構文は実装済み。コスト・対象の適正・応答・誘発順はAIが確認してから実行する。

帰還誘発の未処理・スタック登録済み項目は `pending list` にもL番号で表示する。処理は引き続き `linked trigger/resolve/counter` を使い、同じ誘発を `pending add` へ重複登録しない。一般の誘発の記帳は [bookkeeping.md](bookkeeping.md) を参照。

`linked play` による呪文の詠唱は、通常の `stack push --cast` と共通の処理で詠唱回数・cast確認メモ・パス状態を更新する。土地のプレイは詠唱回数に含めない。

## 装備・オーラ: 一度登録し、付け替えはattachだけ

```text
attach 3 --to 1
fx add --src 3 --ability equipped-bonus --pt +1/+0 --grant トランプル
attach 3 --to 2
```

E番号が効果ID。`fx` の既定は `--scope attached --until source`。装着先のP/T・キーワードを計算するので、付け替えの際にmod/grantを書き直さない。別の装備から来る効果は残る。発生源が戦場を離れるとその常在型効果は終了し、明滅して戻っても古い効果を再利用しない。新しいオブジェクトとしてテキストを確認して登録する。

同じ発生源・世代・abilityの`attached/source`効果を再度`fx add`すると、既存E番号を示して変更前に停止する。訂正は`fx set`、本当に別の能力なら別のability名を使う。`fixed/eot`の複数回解決は加算できる。装着先が戦場を離れると自動解除されるが、戦場に残った装備品の定義は保持されるため、再装備は`attach`だけでよい。

カウンターに依存するなら、カードテキストを確認して式を登録する。次の例は「基本+1/+0に加え、発生源のchargeカウンター1個につき+2/+1」。任意のカウンターが自動で能力を持つわけではない。

```text
fx add --src 3 --ability counter-bonus --pt +1/+0 --counter charge --per-counter +2/+1
counter 3 charge 4 --set
```

`fx set` は同じIDの**全定義を置換**し、適用順は維持する。省略した付与キーワード等は残らない。新しい能力の解決は `fx add` で別IDにする。別の対象へ付け替えると、その装着効果は新しいタイムスタンプとして扱う。同じ対象へのattachでは順序を変えない。

```text
fx set E1 --src 3 --ability equipped-bonus --pt +2/+0 --grant トランプル
fx remove E1
fx list
fx check
```

負の修整は `--pt=-1/-1` のように `=` で指定する。

旧 `mod/grant --until attached --src 3` が残っている場合は、移行する効果の合計を確認して、完全な定義に `--replace-legacy` を付ける。同じ発生源の旧attached修整・付与を削除するので二重計上しない。明滅履歴・発生源不明の修整は推測で移行しない。

旧方式も使用できるが、付与元は戦場に存在し、実際にその対象に付いている必要がある。`fx` に移行した発生源へ旧方式で追加する操作は拒否する。`grant 1 --clear --src 3` はその発生源の旧付与だけを消す。`--src` を省くと他の発生源分も消える。**付け替えの後始末にgrant --clearを使わない。**

`attach` は発生源のタイプと戦場、装備先がクリーチャーか、城砦の装着先が土地かを確認する。オーラ固有のエンチャント条件・プロテクション・授与等はAIが判断する。プレイヤーに付くオーラは `attach 6 --to P2`。そのプレイヤーに対する特殊効果は現時点では自動計算しない。

## 他の継続効果

```text
# 発生源のコントローラーのクリーチャーを常に再評価
fx add --src 5 --ability anthem --scope controller-creatures --pt +1/+1
# 解決時の対象集合を固定。後から出たクリーチャーへは広がらない
fx add --src 5 --ability team-pump --scope fixed --targets 1 2 --until eot --pt +2/+2
# 基本P/T設定は加算修整・カウンターの前に適用
fx add --src 5 --ability base --scope fixed --targets 1 --until eot --base-pt 0/1
```

`fixed --until eot/permanent` は発生源が離れても存続する。対象自身が領域移動すると終了する。パーマネント呪文のスタック→戦場で引き継ぐ特性修整は追跡する。`--until source` は発生源が戦場にいる間だけ。`eot` はクリンナップで終了し、次の終了ステップの誘発とは別。

コピー、種類変更、能力喪失、コントロール変更の継続効果、複雑なレイヤー依存、置換・軽減、フェイズアウト、授与は自動処理の対象外。通常の `move` でフェイズアウトを表現しない。未対応の条件を省略して効果を有効にせず、例えば次のように登録して裁定を保留する。

```text
fx add --src 5 --ability unresolved --scope fixed --targets 1 --until eot --manual "能力喪失と装備の依存関係を確認"
```

要裁定がある間は戦闘ダメージ計算・自動SBA・フェイズ進行を止める。裁定して必要な操作を記帳後、このE番号を `fx set` で確定した効果へ置換、または `fx remove` で保留解除する。コマンドを通すためだけに消さない。

## 関連追放

oidはカードの識別番号、`oid@世代` はルール上のオブジェクト参照。領域移動ごとに世代が変わる。`zone P1:battlefield` 等に `[5]@1` と出ていれば、引数は `5@1`。コントロール変更による戦場の席移動は世代を変えない。

```text
# 「これが戦場を離れるまで追放」: 離脱時にスタックを使わず帰還
linked exile 1 2 --src 5@1 --ability etb-exile --return until-source-leaves
# 「これが戦場を離れたとき、追放したカードを戻す」: 別の帰還誘発
linked exile 1 --src 5@1 --ability linked-etb --return leave-trigger
# 次の終了ステップに帰還
linked exile 1 --src 5@1 --ability blink --return next-end
linked list
```

各実行は別のL番号になる。同じ発生源・同じ能力による複数回の追放は、各L番号を保持して同じsource/abilityとして確認できる。既定の帰還先は追放前の領域の所有者側。カードに別の指示がある場合は `--to battlefield/hand/graveyard` を指定する。特別な帰還コントローラーやタップ状態、戦場に出る際の選択・置換効果が必要なケースはこの自動帰還の対象外。効果に合わない既定値で登録しない。

帰還誘発が発生するとL番号がpendingになる。SBA・他の誘発を確認し、APNAPと選択した順序でスタックへ載せる。

```text
linked trigger L1
# 両席の応答を確認してから
linked resolve L1
# 帰還誘発が打ち消された場合は resolve の代わりに
linked counter L1
```

帰還と能力の除去を一操作で保存する。`stack pop` / `move` で関連帰還能力だけを消すことは拒否する。終了ステップの帰還誘発がある場合、`phase to ending.cleanup` は `ending.end` で停止する。誘発処理後に再開する。既に終了ステップに入ってから作った「次の終了ステップ」は次ターン以降を待つ。

発生源が解決前に離れる場合を取り違えないよう、スタック登録時に参照を保存できる。

```text
stack push "追放ETB" --ability --controller P1 --src 5 --ability-key etb-exile --targets 1
# この能力のoidが8の場合。発生源が先に離れていても元の世代を使う
linked exile 1 --via 8 --return until-source-leaves
stack pop
```

`--via` と `--src/--ability` は併用しない。until-source-leavesで元の発生源が離脱済みなら追放しない。leave-triggerで離脱済みの場合は旧式ETB/LTBの解決順による個別裁定が必要なので停止する。

追放カードが別の領域へ移ると、そのL番号の帰還対象・プレイ対象から外れる。同じカードが再び追放されても古い関連は復活しない。トークンは帰還させない。裏向き追放・スタック上の呪文の追放はこのコマンドでは扱わない。

## 追放カードのプレイ許可

```text
linked exile 1 --src 5 --ability impulse --play-until eot --player P1
linked play L1 1 --player P1
```

通常のタイミング・通常のコストで、土地もプレイできる許可だけを扱う。許可の席・世代・追放領域・優先権・通常のタイミング・土地プレイ回数を検証する。呪文はスタックへ移動し、コスト・対象・追加制限はAIが別途確認して記帳する。土地は直接戦場に出て土地プレイ回数を消費する。特殊な支払い、通常と異なるタイミング、土地不可、タップインや置換処理を必要とするケースは汎用move等で個別裁定する。

## 保存・互換性

新規保存はschema_version 2。旧状態を読むと世代1・空のfx/linksを補い、次の変更で保存する。旧mod/grantは勝手に効果定義へ変換しない。`fx list/check` と `linked list` は保存・undo履歴を増やさない。付け替え・関連帰還の派生変更も一操作のundoで戻る。バッチは従来どおり、途中の失敗より前の成功操作を保持する。

`sba --apply` はトークン消滅に加えて装着先のない通常のオーラを墓地へ送る。`--apply-deaths` は同時死亡をまとめて処理し、効果消失による次の死亡も安定するまで再確認する。授与等の例外・置換効果がある場合は先に裁定保留を登録する。SBAで帰還誘発が発生しても勝手に解決せず、pendingとして残す。
