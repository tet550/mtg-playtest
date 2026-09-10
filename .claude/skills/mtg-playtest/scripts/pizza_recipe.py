"""Generate the verified growing-power Food cycle; never mutate game state.

Caller verifies priority, continuous effects, legal activations and no responses.
Inputs come from show. The generated file retains every operation and save.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cardcache  # noqa: E402  force_utf8() を import 時に実行する

cardcache.force_utf8()


def generate(player, mona, chrome, pizza, power, next_oid, count, proof,
             *, recover=True, color="G", pool=0):
    if player not in ("P1", "P2") or power < 1 or not 1 <= count <= 1000:
        raise ValueError("player=P1/P2, power>=1, 1<=count<=1000 が必要です")
    if min(mona, chrome, pizza) < 1 or len({mona, chrome, pizza}) != 3:
        raise ValueError("異なる3つの正のoidが必要です")
    if next_oid <= max(mona, chrome, pizza) or not proof.strip():
        raise ValueError("未使用next-oidと検証済み手順のproofが必要です")
    cost = 7 if recover else 5
    if color not in ("W", "U", "B", "R", "G", "C") or pool < 0 or pool + power < cost:
        raise ValueError("有効な色、非負pool、初周のコストを満たすpower+poolが必要です")
    available = pool
    for i in range(count):
        available += power + 2*i - cost
        if available < 0:
            raise ValueError("途中の周回でマナが不足します")
    opponent = "P1" if player == "P2" else "P2"
    lines = ["# Verified growing-power Pizza cycle; regenerate if any prerequisite changed.",
             "# " + proof.replace("\n", " ").replace("\r", " ")]
    passes = [f"pass {player}", f"pass {opponent}", "stack resolve"]
    for i in range(count):
        token = next_oid + (4 if recover else 3) * i + 1
        p = power + 2 * i
        lines += [f"tap {mona}", f"mana {player} add " + color * p,
                  f"mana {player} spend " + color * 5,
                  f'stack push "Chrome {chrome} copy Pizza" --ability --controller {player} --targets {pizza}',
                  *passes, f'token {player} "Guac & Marshmallow Pizza"',
                  f"grant {token} Haste --until permanent", "stack pop", "sba --apply",
                  f'stack push "Pizza {token} ETB" --ability --controller {player} --targets {mona}',
                  *passes, f"mod {mona} +2/+2 --until eot", f"untap {mona}",
                  "stack pop", "sba --apply"]
        if recover:
            lines += [f"mana {player} spend " + color * 2,
                  f"tap {token}", f"move {token} graveyard",
                  f'stack push "Food gain3" --ability --controller {player}', "sba --apply",
                  *passes, f"life {player} 3", "stack pop", "sba --apply"]
    if not recover:
        tokens = ",".join(str(next_oid + 3*i + 1) for i in range(count))
        lines.append(f'effect add "Pizza copies [{tokens}]: sacrifice at next end step" --until eot')
    # 対人モードでは相手の手札が平文で出てしまうので、両者の手札は出さない。
    # 自分の手札は呼び出し側が `hand <player>` で見る（ログに残らない）。
    lines.append("show")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--player", choices=["P1", "P2"], required=True)
    ap.add_argument("--source", "--mona", dest="mona", type=int, required=True)
    ap.add_argument("--no-recover", action="store_true", help="コピーを残し回復を行わない")
    ap.add_argument("--color", choices=list("WUBRGC"), default="G")
    ap.add_argument("--pool", type=int, default=0, help="開始時の同色・同用途マナ残量")
    for name in ("chrome", "pizza", "power", "next-oid", "count"):
        ap.add_argument("--" + name, type=int, required=True)
    ap.add_argument("--proof", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    try:
        recipe = generate(args.player, args.mona, args.chrome, args.pizza,
                          args.power, args.next_oid, args.count, args.proof,
                          recover=not args.no_recover, color=args.color, pool=args.pool)
    except ValueError as error:
        ap.error(str(error))
    # Exclusive creation prevents accidentally replacing a reviewed batch.
    with pathlib.Path(args.out).open("x", encoding="utf-8") as out:
        out.write(recipe)
    gain = args.count * (args.power - (5 if args.no_recover else 7)) + args.count * (args.count - 1)
    print("生成しました: %s / %d周 / ライフ+%d / P/T+%d/+%d / %sマナ純増%d / 最終プール%d" %
          (args.out, args.count, 0 if args.no_recover else 3 * args.count,
           2 * args.count, 2 * args.count, args.color, gain, args.pool + gain))



if __name__ == "__main__":
    main()
