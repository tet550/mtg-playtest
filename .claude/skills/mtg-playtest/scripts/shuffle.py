#!/usr/bin/env python3
"""デッキリストを実際にシャッフルし、引き順を固定する。

テストプレイの結論は引きの偏りに強く左右されるため、シャッフルは必ず機械的に行う。
出力された library の順に上から引くこと。ゲーム後に seed と引き順を提示できる。

使い方:
    python shuffle.py deck.txt                 # 乱数シードは自動
    python shuffle.py piza --seed 12345        # 登録済みデッキ名でもよい／再現したいとき
    python shuffle.py deck.txt --games 20 --out playtest/<対局>/   # 複数ゲーム分を一括生成

出力はプレイ記録なので playtest 配下に置く（既定は playtest/）。対局に紐づくものは
その対局フォルダを --out で明示する。規定は references/storage-layout.md。

デッキの解釈は decks.py と共通なので、Arena 形式（"4 Lightning Bolt (2XM) 129"）、
"デッキ"/"サイドボード" の見出し、# コメントをそのまま読み、カード名は英語名に揃う。
mtg.py と同じ名前で出力されないと、引き順を突き合わせられないため。
"""
import argparse
import json
import pathlib
import random
import sys

import cardcache
import decks

cardcache.force_utf8()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("deck", help="登録名 または デッキリストのファイルパス")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--out", default="playtest",
                    help="出力先。既定は playtest/。対局に紐づくなら playtest/<対局フォルダ>/ を指定する")
    ap.add_argument("--decks-dir", default=decks.DEFAULT_DIR)
    ap.add_argument("--cards-dir", default=cardcache.DEFAULT_DIR)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    deck = decks.resolve_source(args.deck, args.decks_dir, args.cards_dir,
                                offline=args.offline)
    for w in deck.get("warnings", []):
        print("警告: %s" % w, file=sys.stderr)
    cards = decks.card_names(deck)
    if len(cards) < 60:
        print(f"注意: {len(cards)}枚です（構築は60枚以上）。", file=sys.stderr)

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    base = deck.get("key") or deck.get("name") or pathlib.Path(args.deck).stem

    for g in range(args.games):
        seed = (args.seed + g) if args.seed is not None else random.randrange(2**32)
        lib = cards[:]
        random.Random(seed).shuffle(lib)
        data = {"deck": base, "seed": seed, "count": len(lib),
                "opening_hand": lib[:7], "library": lib}
        name = f"{base}.shuffled.json" if args.games == 1 else f"{base}.game{g+1}.json"
        (outdir / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{name}  seed={seed}  初手: {' / '.join(lib[:7])}")


if __name__ == "__main__":
    main()
