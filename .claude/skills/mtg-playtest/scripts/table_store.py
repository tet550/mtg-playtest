"""状態ファイル・履歴・結果の読み書きと取り消し。"""
import json
import os
import pathlib

from table_model import SCHEMA, Stop


def state_path(args):
    p = args.state or os.environ.get("MTG_STATE")
    if not p:
        raise Stop("--state <パス> か環境変数 MTG_STATE で状態ファイルを指定してください。")
    return pathlib.Path(p)


def history_dir(path):
    return path.with_name(path.stem + ".history")


def load(args):
    p = state_path(args)
    if not p.exists():
        raise Stop("状態ファイルがありません: %s（init で作ります）" % p)
    st = json.loads(p.read_text(encoding="utf-8"))
    if st.get("schema") != SCHEMA:
        raise Stop("%s は %s の状態ではありません。" % (p, SCHEMA))
    return st


def save(path, st):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        hist = history_dir(path)
        hist.mkdir(parents=True, exist_ok=True)
        n = len(list(hist.glob("*.json")))
        (hist / ("%04d.json" % n)).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


def read_results(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cmd_undo(args, _st):
    path = state_path(args)
    hist = history_dir(path)
    files = sorted(hist.glob("*.json")) if hist.exists() else []
    if not 1 <= args.n <= len(files):
        raise Stop("戻せるのは1〜%d回分です（状態は変えていません）。" % len(files))
    target = files[-args.n]
    json.loads(target.read_text(encoding="utf-8"))
    path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    for f in files[-args.n:]:
        f.unlink()
    print("%d回分戻しました（残り履歴 %d）。" % (args.n, len(files) - args.n))
    return "readonly"
