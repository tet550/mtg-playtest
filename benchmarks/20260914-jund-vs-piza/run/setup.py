#!/usr/bin/env python3
"""ベンチマーク1回ぶんの実行フォルダを用意する（単体実行の入口）。

このスクリプトは**期待値に触れない**。読むのは同じ `run/` の games.json と同梱の decklists/ だけで、
`../expected/` は開かない。実行フェーズがここから先に読む情報を、そのまま測定条件にできるようにしてある。

やること:
  1. `playtest/_checks/<日時>-<topic>/` を作る（既存名なら連番）
  2. 同梱デッキのカードキャッシュを一時領域の init で確認・補充する（--offline なら確認のみ）
  3. 各ゲームの状態ファイルを session 短縮名に登録する
  4. manifest.json を書き、実行フェーズが打つコマンドを表示する

    python benchmarks/20260914-jund-vs-piza/run/setup.py
    python benchmarks/20260914-jund-vs-piza/run/setup.py --condition b --seeds 9141,9143
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
ROOT = BENCH.parents[1]
MTG = ROOT / ".claude/skills/mtg-playtest/scripts/mtg.py"
GAMES = HERE / "games.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mtg(*args, **kw):
    # 子の標準出力をロケール任せにすると Windows で日本語がデコードできず、
    # 失敗の理由が読めないまま握り潰される。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, str(MTG), *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=env, **kw)


def run_dir(topic: str, out: str | None) -> Path:
    if out:
        path = Path(out)
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    base = ROOT / "playtest" / "_checks"
    path = base / f"{stamp}-{topic}"
    n = 2
    while path.exists():
        path = base / f"{stamp}-{topic}-{n:02d}"
        n += 1
    path.mkdir(parents=True)
    return path


def check_cache(deck1: Path, deck2: Path, seed: int, offline: bool) -> None:
    """一時領域で1回だけ init して、デッキが読めることとキャッシュの充足を確かめる。

    本番の実行フォルダでは試さない（init は既存の状態ファイルを警告なく上書きするため）。
    """
    tmp = Path(tempfile.mkdtemp(prefix="mtg-bench-setup-"))
    try:
        base = ["--state", str(tmp / "probe.json")] + (["--offline"] if offline else [])
        out = mtg(*base, "init", "--deck1", str(deck1), "--deck1-name", "jund-sacrifice",
                  "--deck2", str(deck2), "--deck2-name", "piza",
                  "--seed", str(seed), "--first", "P1")
        if out.returncode != 0:
            raise SystemExit("デッキ／カードキャッシュの確認に失敗した。実行フォルダは作っていない。\n"
                             + (out.stderr.strip() or out.stdout.strip()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="実行フォルダを明示する（既定は playtest/_checks/<日時>-<topic>）")
    ap.add_argument("--seeds", help="回す seed をカンマ区切りで絞る（既定は games.json の全部）")
    ap.add_argument("--condition", default="a",
                    help="測定条件の札。何を変えて測るかの自由記述でよい（既定 a＝現行のまま）")
    ap.add_argument("--offline", action="store_true", help="カードを取りに行かない（キャッシュ完備のときだけ）")
    ap.add_argument("--no-session", action="store_true", help="session 短縮名を登録しない")
    args = ap.parse_args()

    spec = json.loads(GAMES.read_text(encoding="utf-8"))
    games = spec["games"]
    if args.seeds:
        want = {int(s) for s in args.seeds.split(",")}
        games = [g for g in games if g["seed"] in want]
        missing = want - {g["seed"] for g in games}
        if missing:
            raise SystemExit(f"games.json に無い seed: {sorted(missing)}")
    if not games:
        raise SystemExit("回す game が無い。")

    decks = {seat: (HERE / d["list"]).resolve() for seat, d in spec["decks"].items()}
    for path in decks.values():
        if not path.exists():
            raise SystemExit(f"同梱デッキリストが見つからない: {path}")
    for seat, d in spec["decks"].items():
        live = ROOT / "decklists" / Path(d["list"]).name
        if live.exists() and live.read_bytes() != decks[seat].read_bytes():
            print(f"注意: decklists/{Path(d['list']).name} は同梱スナップショットと異なる。"
                  "この実行は同梱版で回す（基準値を作った時点の構築）。")

    check_cache(decks["P1"], decks["P2"], games[0]["seed"], args.offline)

    out = run_dir(spec["topic"], args.out)
    rel = out.relative_to(ROOT).as_posix()
    tag = f"{spec['decks']['P1']['short']}-vs-{spec['decks']['P2']['short']}-bench"
    stamp = datetime.datetime.now().strftime("%m%d%H%M")

    entries, lines = [], []
    for g in games:
        state = f"{rel}/{g['game']}.json"
        session = None if args.no_session else f"bench-{stamp}-{g['game']}"
        if session:
            reg = mtg("--state", str(ROOT / state), "--results", str(out / "results.jsonl"),
                      "session", session)
            if reg.returncode != 0:
                print(f"session 登録を飛ばす（{session}）: {reg.stdout.strip() or reg.stderr.strip()}")
                session = None
        entries.append({"game": g["game"], "seed": g["seed"], "first": g["first"],
                        "state": state, "session": session})
        where = f"--session {session}" if session else f"--state {state} --results {rel}/results.jsonl"
        lines.append(f"python {MTG.relative_to(ROOT).as_posix()} {where} init "
                     f"--deck1 {decks['P1'].relative_to(ROOT).as_posix()} --deck1-name jund-sacrifice "
                     f"--deck2 {decks['P2'].relative_to(ROOT).as_posix()} --deck2-name piza "
                     f"--seed {g['seed']} --first {g['first']} --history-hand both")

    manifest = {
        "benchmark": spec["benchmark"],
        "created_at": datetime.datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "condition": args.condition,
        "tag": tag,
        "run_dir": rel,
        "results": f"{rel}/results.jsonl",
        "decks": {seat: {"name": spec["decks"][seat]["name"],
                         "list": decks[seat].relative_to(ROOT).as_posix(),
                         "sha256": sha256(decks[seat])} for seat in decks},
        "games": entries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")

    print(f"実行フォルダ: {rel}")
    print(f"ブリーフ    : {(HERE / 'brief.md').relative_to(ROOT).as_posix()}")
    print(f"tag         : {tag} / 条件: {args.condition}")
    print(f"ゲーム数    : {len(entries)}")
    print("\n各ゲームの開始コマンド（1ゲームずつ、終わってから次へ）:\n")
    for entry, line in zip(entries, lines):
        print(f"# {entry['game']} seed {entry['seed']} 先手 {entry['first']}")
        print(line + "\n")
    print("初手は init の次のバッチで draw P1 7 / draw P2 7 から。以降は ai-vs-ai.md の手順どおり。")
    print("採点は全ゲームの end 後に score.py で行う。実行中は ../expected/ を開かない。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
