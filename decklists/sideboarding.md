# サイドボード計画

マッチアップごとのゲーム2以降（G2）の入れ替え。**ここは計画だけを持つ**。
実際に使った60枚は対局ごとに変わる記録なので、`playtest/<対局フォルダ>/g02-<略称>.txt`
に書き、`decks/` には登録しない（規定は
[`references/storage-layout.md`](../.claude/skills/mtg-playtest/references/storage-layout.md)）。

サイド後の75枚は **素の構築 ± IN/OUT** で一意に決まる。

- メイン60 = 素の構築のメイン ＋ IN − OUT
- サイド15 = 素の構築のサイド − IN ＋ OUT

## 使い方（G2の開始手順）

1. この表の IN/OUT を素の構築に適用した60枚を、対局フォルダに `g02-<略称>.txt` として書く。
   書式は [`README.md`](README.md) と同じ（英語名・クリーチャー→呪文→土地）。
2. `init` にはそのファイルを渡し、**集計用のデッキ名は素の構築の登録名に寄せる**。

```bash
python .claude/skills/mtg-playtest/scripts/mtg.py \
  --state playtest/<対局>/g02.json --results playtest/<対局>/results.jsonl \
  init --deck1 playtest/<対局>/g02-dw.txt   --deck1-name boros-dwarves \
       --deck2 playtest/<対局>/g02-piza.txt --deck2-name piza \
       --seed <seed> --first <敗者が選んだ席>
```

`--deckN-name` を省くとファイル名がデッキ名として記録され、同じ物理デッキが
G1とG2で別行になって `stats` の勝率が読めなくなる。実際に読んだファイルは
`results.jsonl` の `deck_sources` に残る。

3. report.md には素の構築の登録名と、この計画からの差分があればそれを書く。

## piza

素の構築: [`piza.txt`](piza.txt) ／ プレイ方針: [`strategy/piza.md`](strategy/piza.md)

### vs ボロスドワーフ（dw）

- **IN  (9)**: 2 Fire Magic / 3 Into the Flood Maw / 2 Pawpatch Formation / 2 Snakeskin Veil
- **OUT (9)**: 2 Agatha's Soul Cauldron / 1 Courier of Comestibles / 2 Does Machines /
  1 Nature's Rhythm / 1 Patchwork Beastie / 1 Rapid Rescue / 1 Spectral Sailor

盤面に触らない札と、アグロ相手に間に合わない札を抜く。Patchwork Beastie は昂揚が付くまで
ブロックできない。Fire Magic のファイラ（{2}）は Dwarven Mauler 2/1・Dwalin 2/1・
Dáin's Company 2/2・Kíli 1/2 を流し、こちらの Chrome Dome 1/3 と Mona Lisa 1/3 は
生き残る一方的な掃除。Into the Flood Maw は装備したクリーチャーを戻して装備コストごと
損させる。Snakeskin Veil と Pawpatch Formation は、G1後に相手が入れてくる Disenchant・
Abrade・Sheltered by Ghosts への保険。

### vs 緑単上陸（green）

- **IN  (4)**: 2 Flashfreeze / 2 Into the Flood Maw
- **OUT (4)**: 1 Courier of Comestibles / 1 Nature's Rhythm / 1 Oblivious Bookworm /
  1 Patchwork Beastie

この計画は piza のサイドを調整する前の75枚に対して作られていた（当時の15枚には
Pick Your Poison と2枚目の Annul が入り、Fire Magic が無かった）。**メインの IN/OUT は
そのまま使えるが、サイド15枚は現在の `piza.txt` から再計算した値を正とする。**

### vs ボロストークン（tokens）

- **IN  (2)**: 2 Fire Magic
- **OUT (2)**: 1 Courier of Comestibles / 1 Nature's Rhythm

根拠は記録に残っていない。次に当たったときに書き足す。

## boros-dwarves

素の構築: [`boros-dwarves.txt`](boros-dwarves.txt)

### vs piza

- **IN  (8)**: 1 Abrade / 2 Disenchant / 2 Exorcise / 3 Sheltered by Ghosts
- **OUT (8)**: 3 Doc Ock's Tentacles / 3 Ghostfire Blade / 2 Skateboard

抜いた3種はいずれも「先にクリーチャーが要る」装備品。G1では盤面に装備品4個・
クリーチャー0体の時間が3ターン続いた。Doc Ock's Tentacles は装備{5}、Ghostfire Blade は
有色クリーチャー相手に装備{3}で、いちばん置きにくい。G1ではメインに置物への解答が
0枚だったので、Chrome Dome／Mona Lisa／Guac & Marshmallow Pizza に触れる札を8枚入れる。
Sheltered by Ghosts は「除去＋クロックの継続」を1枚で満たすのでアグロの手を止めない。

## boros-tokens

素の構築: [`boros-tokens.txt`](boros-tokens.txt)

### vs piza

- **IN  (5)**: 2 Requisition Raid / 3 Rest in Peace
- **OUT (5)**: 3 Anim Pakal, Thousandth Moon / 2 The Last Ronin's Technique

根拠は記録に残っていない。次に当たったときに書き足す。

## mono-green-landfall

素の構築: [`mono-green-landfall.txt`](mono-green-landfall.txt)

### vs piza

- **IN  (9)**: 1 Leatherhead, Swamp Stalker / 3 Meltstrider's Resolve / 4 Mossborn Hydra /
  1 Surrak, Elusive Hunter
- **OUT (9)**: 3 Esper Origins // Summon: Esper Maduin / 1 Forest / 2 Glimpse the Core /
  1 Lumbering Worldwagon / 2 Sapling Nursery

piza 側は6〜7ターンに無限マナで詰めてくる純コンボ。こちらに干渉手段がほぼ無いのが
G1の敗因なので、「盤面に触れる呪文」と「1〜2ターン速いクロック」に寄せる。
