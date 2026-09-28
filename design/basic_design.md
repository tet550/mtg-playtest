# AIによるMTG対戦基盤 全体設計

## 1. 目的

MTGの対戦をAIによって実行できるデジタル卓上環境を構築する。

本システムでは、MTGの総合ルールやカード効果をスクリプト側で完全実装しない。

MTGが本来、カード、カウンター、メモ、配置、宣言などの比較的単純な道具によって紙上でプレイできるゲームであることを利用し、スクリプト側はそれらに相当するコンポーネントと操作手段を提供する。

カードテキストやルールを解釈し、何を行うべきか判断する責務はAI側に置く。

---

# 2. 基本方針

本システムはRules Engineではなく、Table Engineとして設計する。

Table Engineが扱うのは主として、

- オブジェクトの存在
- オブジェクトの位置
- カードの状態
- プレイヤーの状態
- カウンターやメモ
- オブジェクト間の関係
- スタック
- 戦闘配置
- ターン進行
- 情報公開範囲
- プレイヤーの宣言

である。

一方、以下は原則としてAIが判断する。

- 呪文や能力を使用できるか
- 対象が適正か
- コストを支払えるか
- 誘発条件を満たしているか
- 継続的効果
- 置換効果
- 状況起因処理
- Layer処理
- 戦闘ダメージ処理
- カード固有効果

---

# 3. Paper First

新しい状態や効果を扱う場合は、

> 紙のMTGではプレイヤーがどのように管理するか

を基準とする。

紙上で、

- カウンターを置く
- メモを付ける
- カード同士を関連付ける
- カードの配置を変える
- 口頭で宣言する

ことで管理できるものは、それに対応する汎用コンポーネントで表現する。

カードごとの専用処理は原則として持たない。

---

# 4. システム構成

システムは次の責務に分離する。

## AI

- カードテキストの解釈
- ルール判断
- 戦略判断
- 操作内容の決定

## Table Engine

- ゲーム状態の保持
- AIから指定された操作の適用
- 情報公開範囲の管理
- ゲーム進行上の基本構造の保持

## GUI

- Game Stateの表示
- 人間による操作
- Stack、Combat、Link、Note等の可視化

## Infrastructure

- ログ
- Undo
- Replay
- Snapshot
- デバッグ情報

これらはゲームモデルとは分離する。

---

# 5. Player

Playerはプレイヤー自身の基本状態を表す。

主な状態は、

- 識別情報
- Life
- Mana Pool

とする。

Lifeはプレイヤーそのものが持つ基本状態として扱う。

Poison、Energy、ExperienceなどのカウンターはPlayer固有プロパティにはせず、Playerを対象とするCounterとして扱う。

---

# 6. Card

Cardは実際のゲーム中に存在するカードを表す。

主として、

- カード定義
- Owner
- Controller
- Zone
- 表裏
- タップ状態

などの物理的・盤面的状態を持つ。

カードテキストの意味や能力の処理はCard自身には持たせない。

---

# 7. Zone

カードが存在する領域を表す。

代表的なZoneは、

- Library
- Hand
- Battlefield
- Graveyard
- Exile
- Command

である。

Zoneは必要に応じて、

- 順序
- 所有者
- 公開範囲

を持つ。

---

# 8. Counter

CounterはゲームオブジェクトまたはPlayerに置かれる数値的な目印を表す。

例：

- +1/+1 counter
- Stun counter
- Loyalty counter
- Poison counter
- Energy counter
- Experience counter

Counterの意味はTable Engineでは解釈しない。

基本原則は、

> ゲーム上「カウンター」として扱われる数値はCounterで表現する。

---

# 9. Note

Noteは、単一の対象について覚えておく必要がある情報を表す。

対象にはCard、Player、Manaなどを指定できる。

例：

- ターン終了時まで+3/+3
- この能力はこのターン使用済み
- 選ばれたクリーチャー・タイプはDragon
- このマナはクリーチャー呪文にのみ使用できる

Noteは紙の付箋やメモに相当する。

基本原則は、

> 一つの対象について記憶する情報はNoteで表現する。

---

# 10. Link

Linkは複数のゲームオブジェクト間に存在する意味的な関係を表す。

例：

- AuraとEnchant対象
- Equipmentと装備先
- SpellとTarget
- あるカードと、それによって追放されたカード
- 能力と参照対象

Linkの意味そのものはTable Engineでは解釈しない。

基本原則は、

> 複数の対象間の関係はLinkで表現する。

---

# 11. Stack

StackはMTG固有の基本ゲーム構造として専用モデルを持つ。

Stackは、

- 呪文
- 起動型能力
- 誘発型能力

など、現在スタック上に存在する要素の順序を表す。

対象などの関係はLinkで表現できる。

Stack上の要素が実際に何を行うかはAIが解釈する。

---

# 12. Combat

Combatは戦闘中の配置を表す専用モデルとする。

Combatが保持する主要情報は、

- 各攻撃クリーチャーの攻撃先
- 各ブロッカーのブロック先

である。

攻撃先には、

- Player
- Planeswalker
- Battle
- その他ルール上攻撃可能な対象

を指定できる。

複数体によるブロックや、特殊な効果による複数ブロックも、個々の攻撃・ブロック関係として保持する。

Combatは、

> 誰が何を攻撃し、誰が誰をブロックしているか

を表すものであり、戦闘ルールそのものを処理するものではない。

---

# 13. Turn State

Turn Stateはゲーム進行の基本状態を保持する。

主に、

- Active Player
- Turn
- Phase
- Step
- Priority Player

を表す。

各タイミングで何が可能かというルール判断はAI側が担当する。

---

# 14. Declaration

Declarationは、盤面上のオブジェクト操作では表現しにくいプレイヤーの宣言を扱う。

主な用途は、

- Pass
- Concede

である。

攻撃、ブロック、対象指定、カード移動など、盤面状態として明確に表現できるものはDeclarationには含めない。

---

# 15. Mana Pool

Manaは単純な数値ではなく、Playerが保持する一時的なリソースとして扱う。

Manaは、

- 色または種類
- 数量
- 必要に応じた発生源
- 存続期間
- Note

を持ち得る。

同じ色のManaであっても、

- 使用用途が異なる
- 存続期間が異なる
- 発生源の違いが意味を持つ

場合は区別して保持する。

例：

- 制限のない緑マナ2点
- クリーチャー呪文にのみ使える緑マナ1点

使用用途などの意味はNoteとして表現し、Table Engineはその内容を解釈しない。

Mana Poolは、

> Playerが現在利用可能なManaの集合

として扱う。

---

# 16. 情報公開

Game Stateそのものと、各Playerから見える情報は分離する。

AIには、そのPlayerが知ることのできる情報のみを含むPlayer Viewを提供する。

これにより、

- Hand
- Library
- 裏向きカード
- 非公開情報

の漏洩を防ぐ。

---

# 17. Known Information

現在非公開の場所に存在するカードであっても、Playerが既に内容を知っている場合は、その知識をPlayer Viewに保持する。

例：

- 占術で確認したLibrary Top
- 一度公開され、その後非公開領域へ移動したカード
- 効果によって確認した相手のHand

「実際に公開されている情報」と「そのPlayerが知っている情報」は区別する。

---

# 18. Information Policy

通常対戦とは別に、一人回しやテスト用途では情報公開範囲を変更可能とする。

例：

- 通常のMTGと同じ公開範囲
- 自分のLibraryを閲覧可能
- 全Libraryを閲覧可能
- 全情報を閲覧可能

これによりGoldfish、コンボ確認、デッキテストなどでAIが先の展開まで判断しやすくする。

---

# 19. Operation

AIやGUIは、Table EngineへOperationを送って状態を変更する。

Operationは基本的な卓上操作を表す。

主な分類は、

- カード移動
- Tap / Untap
- 表裏変更
- オブジェクト生成・削除
- Counter操作
- Note操作
- Link操作
- Stack操作
- Combat操作
- Mana操作
- Player状態変更
- Declaration

とする。

カード効果そのものを表す高レベルな処理は原則として用意しない。

---

# 20. Action Group

複数のOperationを、ゲーム上意味のある一まとまりとして扱う。

例：

- 呪文を唱えるための一連の操作
- 能力解決
- カードを引いて捨てる処理
- トークン生成とそれに伴う処理

Action Groupは原則として一体の処理として扱う。

途中で処理不能となった場合は、不完全な状態を残さない。

---

# 21. Batch

AIとTable Engine間の往復を減らすため、複数のAction Groupをまとめて送ることができる。

Batchは通信・実行効率のための単位であり、ゲーム上の意味を持つ単位ではない。

Action Groupはゲーム上の一まとまり、Batchは複数のAction Groupをまとめた実行単位として区別する。

---

# 22. Batchの継続条件

Batch処理を途中で止めるかどうかは、

> 新しい情報が公開されたか

ではなく、

> AIによる新しい意思決定が必要になったか

で判断する。

例えば、

- Library Topが既知
- 引くカードが既知
- その後捨てるカードも既に決定可能

であれば、「1枚引き、その後1枚捨てる」という処理を途中で止める必要はない。

一方、

- 未知のカードを引く
- その内容を見て次の選択を変える

場合はAIへ制御を戻す。

---

# 23. 事前条件

AIが既知情報を前提として先の処理までまとめる場合、その前提が現在も成立していることを確認できるようにする。

これにより、

- Library Top
- Hand内のカード
- 対象のZone
- 現在の盤面状態

などがAIの判断時点から変化していた場合に、誤った一括処理を防ぐ。

これはMTGルールの合法性判定ではなく、状態の整合性確認として扱う。

---

# 24. GUI

GUIはGame Stateを人間が理解できる形で可視化する。

主要表示対象は、

- Hand
- Battlefield
- Library
- Graveyard
- Exile
- Player状態
- Mana Pool
- Stack
- Combat
- Counter
- Note
- Link
- Turn / Phase / Priority

である。

ゲームモデルと表示方法は分離する。

例えばLinkは、内容に応じて、

- 矢印
- 近接配置
- Inspector表示
- ラベル

など異なる方法で表示できる。

---

# 25. GUI専用状態

以下はゲーム状態には含めず、GUI側のみで管理する。

- 画面上の座標
- アニメーション状態
- カードの折り畳み表示
- 同名Tokenのまとめ表示
- Visual Group
- ハイライト
- 選択状態

表示上の整理とゲーム上の意味を分離する。

---

# 26. Human操作

HumanによるGUI操作もAIと同じ基本操作系を利用する。

これにより、

- AI vs AI
- Human vs AI
- Human vs Human
- AI対戦観戦
- 手動介入

を同じゲーム基盤で扱う。

---

# 27. Judge / Orchestrator

必要に応じて、Player AIとは別にJudgeまたはOrchestratorを配置する。

主な役割は、

- ゲーム進行管理
- Priority管理
- ルール確認
- 操作内容の検証
- AI間の調停

である。

ただし、Table Engineそのものにはカード固有ルールを持たせない。

---

# 28. Rule Reference

AIは必要に応じて、

- Card Oracle Text
- Comprehensive Rules
- Card Database

を参照できる。

これらはRules Engineではなく、AIが判断するための参照情報として扱う。

---

# 29. Infrastructure

ゲームモデルとは別に、スクリプト基盤として以下を提供する。

- Operation Log
- Transaction
- Undo
- Redo
- Replay
- Snapshot
- State Diff
- Debug Log

AIはこれらの管理を行わない。

---

# 30. 中核ゲームモデル

最終的な主要モデルは以下とする。

```text
Player
Card
Zone

ManaPool
Mana

Counter
Note
Link

Stack
StackItem

Combat
AttackAssignment
BlockAssignment

TurnState

Declaration
```

補助概念：

```text
GameState
PlayerView
KnownInformation
InformationPolicy
```

操作概念：

```text
Operation
ActionGroup
Batch
Precondition
```

---

# 31. 設計判断の基準

設計上の分類は以下を基準とする。

```text
MTGそのものに明確な構造がある
→ 専用モデル

ゲーム上のカウンター
→ Counter

単一対象について覚える情報
→ Note

複数対象間の意味的関係
→ Link

Playerが保持するMana
→ ManaPool / Mana

口頭でのみ成立する進行上の宣言
→ Declaration

ゲーム上意味のある複数操作
→ ActionGroup

往復削減のための複数処理
→ Batch

表示上だけの整理
→ GUI
```

---

# 32. 設計の中心原則

本システムはMTGをデジタルゲームとして再実装するものではない。

> **AIが紙のMTGをプレイするためのデジタル卓上を提供する。**

Table Engineは状態と操作を扱い、AIが意味を解釈する。

MTG固有の基本構造については明示的なモデルを持つが、カード固有の処理やルール解釈は可能な限りAI側へ残す。

これにより、新カードや新メカニズムへの対応をカード個別実装に依存しない構成とする。