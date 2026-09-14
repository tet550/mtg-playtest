#!/usr/bin/env python3
"""jund-sacrifice vs piza ベンチマークの再現性チェック（A群）。

同じ seed で init し直したときに初手が cases.json と一致するかだけを見る。
対局データ（playtest/ 配下）には依存しない。カード名は --en で英語名に固定する。一時状態は OS の一時領域に作り、
実行後に削除する。プロジェクト直下から実行すること（cards/ の既定パスが相対のため）。

    python benchmarks/20260914-jund-vs-piza/verify.py
    python benchmarks/20260914-jund-vs-piza/verify.py --update   # 期待値を作り直す
"""
import argparse, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MTG = ROOT / ".claude/skills/mtg-playtest/scripts/mtg.py"
CASES = Path(__file__).with_name("cases.json")
HAND_LINE = re.compile(r"\[(\d+)\](.+?)\s*／")


def hand(state: Path, seat: str) -> list[str]:
    out = subprocess.run([sys.executable, str(MTG), "--state", str(state), "--offline", "--en",
                          "hand", seat], capture_output=True, text=True,
                         encoding="utf-8", cwd=ROOT)
    if out.returncode != 0:
        raise SystemExit(f"hand {seat} に失敗: {out.stderr.strip() or out.stdout.strip()}")
    return [m.group(2).strip() for m in (HAND_LINE.search(l) for l in out.stdout.splitlines()) if m]


def opening(seed: int, first: str) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="mtg-bench-"))
    try:
        state = tmp / "g01.json"
        base = [sys.executable, str(MTG), "--state", str(state), "--offline", "--en"]
        init = subprocess.run(base + ["init", "--deck1", "jund-sacrifice", "--deck2", "piza",
                                      "--seed", str(seed), "--first", first],
                              capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        if init.returncode != 0:
            raise SystemExit(f"init に失敗: {init.stderr.strip() or init.stdout.strip()}")
        for seat in ("P1", "P2"):
            subprocess.run(base + ["draw", seat, "7", "--quiet"], capture_output=True, cwd=ROOT)
        return {"P1": hand(state, "P1"), "P2": hand(state, "P2")}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="期待値を現在の実装で作り直す")
    args = ap.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))
    failed = 0
    for c in cases:
        got = opening(c["seed"], c["first"])
        if args.update:
            c["opening_hands"] = got
            print(f"seed {c['seed']}: 期待値を更新")
            continue
        want = c["opening_hands"]
        for seat in ("P1", "P2"):
            if sorted(got[seat]) == sorted(want[seat]):
                print(f"seed {c['seed']} {seat}: OK")
            else:
                failed += 1
                print(f"seed {c['seed']} {seat}: NG\n  期待: {want[seat]}\n  実際: {got[seat]}")
    if args.update:
        CASES.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    print("\n" + ("すべて一致" if not failed else f"{failed}件が不一致"))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
