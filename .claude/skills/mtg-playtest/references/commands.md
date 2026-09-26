# 操作の早見表（table.py）

`python .claude/skills/mtg-playtest/scripts/table.py [--state <パス>] [--as P1|P2] <コマンド> ...`。`--state` は環境変数 `MTG_STATE` でも指定できる。どのコマンドもルールを判定しない。止まるのは物理的に不可能な操作だけ。

oid は `[12]` の数字。`[12]` と書いても `12` と書いてもよい。

## テーブルを作る・見る

| 目的 | コマンド | 補足 |
|---|---|---|
| 開始 | `init --deck1 <登録名かパス> --deck2 <同> --seed 101 [--first P1\|P2\|random] [--life 20]` | ライブラリーをシャッフルして置く。初手は引かない。既存ファイルは `--force` がないと上書きしない |
| 一人回し | `init --deck1 <デッキ> --goldfish --seed 101` | P2 はライブラリーなしの何もしない相手 |
| 盤面 | `show [--hand none\|P1\|P2\|both]` | 何も乗っていない土地は「土地:」の1行にまとめる。そのターンに戦場に出たものには「(このターンに出た)」。期限切れの付箋・時期のメモも出る。`--as P1` では相手の手札を出せない |
| 公開領域 | `zone P1:graveyard` / `zone stack` | ライブラリーは見られない |
| カードを読む | `card <oid> ...` / `card "<名前>" ...` | テキストは切らずに全文 |
| 記録 | `log [--tail 20]` | 秘匿の行は `--as` の席にだけ出る |

## ライブラリーと手札

| 目的 | コマンド | 補足 |
|---|---|---|
| 引く | `draw P1 [N]` | 空なら引ける分だけ引き、引けなかった枚数を知らせる（敗北かどうかはAIが判断） |
| 切削 | `mill P1 N` | |
| 上から見る | `look P1 N` | 動かさない。相手のライブラリーは `--as` のとき `--by-effect` が要る |
| 探す | `search P1 ["名前の一部"]` | 名前順で表示し、積み順は伏せる。持ってくるのは `move`、切り直しは `shuffle P1` |
| 公開したことを残す | `reveal <oid...>` | |
| マリガン | `mulligan P1 [--draw 7]` | 手札を戻してシャッフルし、引き直す。ボトムは `move <oid> library --bottom` |
| シャッフル | `shuffle P1` | |

## 領域の移動

| 目的 | コマンド | 補足 |
|---|---|---|
| 移す | `move <oid...> <領域>` | 領域は `hand` `battlefield` `graveyard` `exile` `command` `library` `sideboard` `stack`、または `P2:battlefield` のように席つき。席を書かなければ、戦場は今の操作する席（なければオーナー）、それ以外はオーナーの領域 |
| ライブラリーへ | `move <oid> library --top` / `--bottom` / `--index N` | 位置の指定は必須（N=0 が一番上） |
| タップ状態・裏向きで出す | `move <oid> battlefield --tapped` / `--face-down` | |
| コントロールを移す | `move <oid> P2:battlefield` または `--controller P2` | 他人のカードを唱えるときは `move <oid> stack --controller P2` |
| テーブルから取り除く | `remove <oid...>` | トークン・コピー・能力の目印だけ。実在のカードは消せない |

戦場・スタックから別の領域へ移ったカードからは、タップ・カウンター・ダメージ・付箋・攻撃の位置が外れ、コントローラーはオーナーに戻る。そのカードの下に重ねていたものは重ね置きが外れる（記録に残る）。

## パーマネントの状態

| 目的 | コマンド | 補足 |
|---|---|---|
| タップ／アンタップ | `tap <oid...>` / `untap <oid...>` / `untap --all P1` | `--all` はその席の戦場をまとめて起こす |
| 裏向き・表向き・反対の面 | `flip <oid> face-down\|face-up\|transform` | |
| カウンター | `counter <oid\|P1> <名前> +2` / `-1` / `=3` | 0未満にはできない。プレイヤーの毒・エネルギーも同じ（`counter P2 poison +1`） |
| ダメージ用ダイス | `damage <oid> +3` / `-1` / `=0` ／ `damage clear` | 破壊の判定はしない。`clear` は全部外す |
| ライフ | `life P1 -3` / `+2` / `=20` | 0以下でも何もしない |
| マナ・ダイス | `mana P1 add RRG` / `mana P1 pay RG` / `mana P1 clear` | 色は WUBRGC。不特定コストも払う色で書く（`{1}{G}` を GG で払うなら `pay GG`） |
| 下に重ねる | `attach <oid> --to <oid>` / `attach <oid> --off` | オーラ・装備・「これで追放した」カード。戦場で重ねたカードは、重ねた先の下にだけ表示される |
| 付箋 | `note <oid> "+2/+2" [--until eot\|"説明"]` / `note rm N1 N2` | 計算しない。`eot` はトラッカーがそのターンを過ぎると「期限切れ」と表示するだけ |

## スタック・トークン・コピー

| 目的 | コマンド | 補足 |
|---|---|---|
| 呪文を唱えた位置に置く | `move <oid> stack` | 支払いやタイミングは自分で確かめる |
| 能力の目印 | `ability P1 "説明" [--src <oid>] [--label x]` | 解決したら `remove` |
| トークン | `token P1 "名前" --pt 1/1 --types "Creature Goblin" [--text "..."] [-n 2] [--tapped] [--label x]` | 定型は `--preset treasure\|clue\|food\|blood\|map\|lander` |
| コピー | `copy P1 <oid> [--to stack\|battlefield] [--label x]` | |

## 戦闘の位置

| 目的 | コマンド | 補足 |
|---|---|---|
| 攻撃 | `attack <oid...> --target P2`（またはプレインズウォーカー等の oid） | タップはしない。必要なら `tap` を別に書く |
| ブロック | `block <ブロッカー> <攻撃クリーチャー>` | |
| 位置を戻す | `combat-clear` | |

## トラッカー・マーカー・メモ

| 目的 | コマンド | 補足 |
|---|---|---|
| 次のターン | `turn next` | ターン数+1、アクティブを交代、フェイズをアンタップに置く。アンタップ・ドローはしない |
| ターンを直接置く | `turn 5 --active P2` | |
| フェイズ | `phase <名前>` | `beginning.untap` `beginning.upkeep` `beginning.draw` `precombat_main` `combat.begin` `combat.attackers` `combat.blockers` `combat.damage` `combat.end` `postcombat_main` `ending.end` `ending.cleanup` |
| マーカー | `marker 統治者 --holder P1` / `marker 昼夜 --value 夜` / `marker 統治者 --remove` | |
| メモ帳 | `memo add "内容" [--player P1] [--at ending.end] [--turn N\|next]` / `memo done M1 [--reason ...]` / `memo list [--all]` | 時期が来ると表示するだけ |

## 乱数・記録・まとめて実行

| 目的 | コマンド | 補足 |
|---|---|---|
| ダイス・コイン・無作為の選択 | `roll 2d6` / `coin` / `pick A B C` | seed から決まり、記録に `rng#N` が残る |
| 記録に残す | `say "内容" [--private P1]` | |
| まとめて実行 | `run -`（標準入力）または `run <ファイル>` | 全行成功で1回保存。途中で止まれば何も適用しない。`$x` で同じ run 内の `--label x` を参照。`init` `run` `undo` `end` `stats` は中に書けない |
| 戻す | `undo [N]` | 保存N回分。`run` 1回が1回分 |
| 補助 | `aid triggers [--phase <名前>]` / `aid creatures` / `aid check` / `aid mana` / `aid pregame` | 読むだけのヒント。`mana` は席ごとのアンタップのマナ源、`pregame` は開始時の手札で確かめるカード |
| 決着 | `end --winner P1\|P2\|draw --reason "..." [--tag T] [--results <パス>]` | 既定は状態ファイルと同じフォルダの `results.jsonl`。同じ対局は2回記録できない |
| 集計 | `stats <results.jsonl> [--tag T]` | 一人回しは自ターンの分布、対戦は勝ち数 |
