"""Check saved report references, result fields and explicit growing-cycle arithmetic.

Read-only. This does not validate prose, card rules, or the legality of a game.
"""
import argparse
import json
import pathlib
import re
import sys


def check(path):
    path = pathlib.Path(path)
    text = path.read_text(encoding="utf-8")
    errors = []
    # R identifiers are local to each game. Reports without games can still
    # use the reference and arithmetic checks.
    for scope in re.split(r"^### G", text, flags=re.M):
        references = dict(re.findall(r"^(R\d+) = (.+)$", scope, re.M))
        cache = {}
        for key, start, end in re.findall(r"［(R\d+):(\d+)-(\d+)］", scope):
            if key not in references:
                errors.append(f"{key}: 参照先未定義")
                continue
            target = path.parent / references[key].strip()
            if key not in cache:
                try:
                    cache[key] = target.read_text(encoding="utf-8").splitlines()
                except OSError:
                    cache[key] = []
            if not 1 <= int(start) <= int(end) <= len(cache[key]):
                errors.append(f"{key}:{start}-{end}: 範囲外またはファイルなし（{len(cache[key])}行）")
    # Results are matched by seed; duplicate seeds are deliberately rejected.
    result_path = path.parent / "results.jsonl"
    records = [json.loads(x) for x in result_path.read_text(encoding="utf-8").splitlines() if x.strip()] if result_path.exists() else []
    for section in re.split(r"^### G", text, flags=re.M)[1:]:
        seed = re.search(r"seed=(\d+)", section)
        line = re.search(r"^結果: (.+)$", section, re.M)
        if not seed or not line:
            errors.append("ゲームのseedまたは結果欄なし")
            continue
        matches = [r for r in records if r.get("seed") == int(seed[1])]
        if len(matches) != 1:
            errors.append(f"seed={seed[1]}: 結果レコードが一意でない")
            continue
        record, value = matches[0], line[1].replace("−", "-")
        life = re.search(r"ライフ=P1\s+(-?\d+)/P2\s+(-?\d+)", value)
        turn = re.search(r"\bT(\d+)\s+P[12]", value)
        winner = re.match(r"(P[12])", value)
        if not life or [int(life[1]), int(life[2])] != [record["life"]["P1"], record["life"]["P2"]]:
            errors.append(f"seed={seed[1]}: 最終ライフ不一致または書式不明")
        if not turn or int(turn[1]) != record["turn"]:
            errors.append(f"seed={seed[1]}: 決着ターン不一致または書式不明")
        if record.get("winner") in ("P1", "P2") and (not winner or winner[1] != record["winner"]):
            errors.append(f"seed={seed[1]}: 勝者不一致")
    for row in re.findall(r"^検算: (.+)$", text, re.M):
        data = dict(re.findall(r"(\w+)=(-?\d+)", row))
        required = {"power", "pool", "count", "cost", "final_power", "final_pool"}
        if set(data) != required:
            errors.append("検算: 必須6項目を指定してください")
            continue
        d = {k: int(v) for k, v in data.items()}
        n = d["count"]
        if n < 1 or d["power"] < 1 or d["pool"] < 0 or d["cost"] not in (5, 7) or d["pool"] + d["power"] < d["cost"]:
            errors.append("検算: 初期条件が不正")
        if n >= 1 and any(d["pool"] + j*(d["power"]-d["cost"]) + j*(j-1) < 0 for j in range(1, n+1)):
            errors.append("検算: 途中の周回でマナ不足")
        if d["final_power"] != d["power"] + 2*n or d["final_pool"] != d["pool"] + n*(d["power"]-d["cost"]) + n*(n-1):
            errors.append("検算: 最終パワーまたはマナ収支不一致")
    return errors


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=pathlib.Path)
    args = parser.parse_args()
    problems = check(args.report)
    print("\n".join(problems) if problems else "OK: 参照・結果・明示された検算（文章とルールは別途確認）")
    sys.exit(bool(problems))
