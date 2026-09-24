#!/usr/bin/env python3
"""実行済みのベンチマーク1回ぶんを期待値と突き合わせる（採点フェーズ専用）。

**実行フェーズではこのスクリプトを走らせない。** 出力に期待値が含まれるので、対局の前に読むと
その回は測定にならない。走らせるのは全ゲームの `end` / `concede` が済んでから。

見るもの:
  実行フォルダの manifest.json / results.jsonl / <game>-NNN.mtg / output/<game>/run-*.jsonl と、
  expected/ の cases.json・conditions.json。対局データは読むだけで書き換えない。

    python benchmarks/20260914-jund-vs-piza/score.py playtest/_checks/<実行フォルダ>
    python benchmarks/20260914-jund-vs-piza/score.py <実行フォルダ> --skip-a   # A群の再init を省く

終了コード: 0=すべて期待どおり / 1=同一性・A群・B群のいずれかが不一致 / 2=B群までは通りC群だけ未達。
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# 子プロセスの標準出力をロケール（Windowsではcp932）任せにすると、日本語の出力が
# デコードできずに stdout が丸ごと失われる。空の出力を「異常なし」と読むと誤って合格する。
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXPECTED = HERE / "expected"
GAMES_SPEC = HERE / "run" / "games.json"

# 盤面を変えない（判断点にもならない）コマンド。バッチ末尾の確認表示はここに入る。
READONLY = {"show", "hand", "zone", "view", "note", "log", "stats", "glossary"}
OID = re.compile(r"--src[= ](\d+)")


def fail(message):
    raise SystemExit("採点できない: " + message)


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def batches(run: Path, game: str):
    """書かれたバッチ（<game>-NNN.mtg）を番号順に。行はコメント・空行を除いた実コマンド。"""
    out = []
    for path in sorted(run.glob(f"{game}-*.mtg"),
                       key=lambda p: int(re.search(r"-(\d+)\.mtg$", p.name).group(1))):
        lines = [l.strip() for l in path.read_text(encoding="utf-8-sig").splitlines()]
        out.append((path.name, [l for l in lines if l and not l.startswith("#")]))
    return out


def traces(run: Path, game: str):
    """実行の証跡。1ファイル＝1バッチで、作成順（mtime 昇順）に並べる。"""
    out = []
    for path in sorted((run / "output" / game).glob("run-*.jsonl"),
                       key=lambda p: p.stat().st_mtime):
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        out.append((path.name, records))
    return out


def head(line: str) -> str:
    try:
        words = shlex.split(line)
    except ValueError:
        words = line.split()
    return words[0] if words else ""


def process_metrics(run: Path, game: str) -> dict:
    """B群の生の数値。判定は呼び出し側で conditions.json と突き合わせる。"""
    written, trace = batches(run, game), traces(run, game)
    m = {"batches_written": len(written), "batches_run": len(trace)}

    # B1 / B2 / B3 / B7 は「実際に走った内容」から数える。
    corrections = undo = remind_late = oid_blind = 0
    landed, listed = {}, {}
    for index, (_, records) in enumerate(trace):
        for record in records:
            command = record.get("command", "")
            words = shlex.split(command) if command else []
            if not words:
                continue
            if words[0] == "note" and re.search(r"訂正|補正|運用ミス", command):
                corrections += 1
            if words[0] == "undo":
                undo += 1
            if words[0] == "search" and len(words) > 1 and words[1] in ("P1", "P2"):
                if "--oid" in command:
                    if index <= listed.get(words[1], 10**9):
                        oid_blind += 1
                else:
                    listed.setdefault(words[1], index)
            if words[0] == "remind" and len(words) > 1 and words[1] == "add":
                found = OID.search(command)
                landing = landed.get(found.group(1)) if found else None
                if landing is not None and landing < index:
                    remind_late += 1
            # 起点は「戦場に出たバッチ」。手札やスタックで oid を見かけたバッチを起点にすると、
            # 手札から唱えたパーマネントは着地と同じバッチで登録しても必ず後付け扱いになる。
            if (words[0] == "move" and len(words) > 2
                    and words[1].isdigit() and words[2] == "battlefield"):
                landed[words[1]] = index      # 再着地は上書き。新しい世代の誘発を登録し直すため
    history = run / "output" / game / "turn-starts.md"
    if history.exists():
        undo += len(re.findall(r"巻き戻し", history.read_text(encoding="utf-8")))
    m.update(correction_notes=corrections, undo=undo, remind_late=remind_late,
             oid_without_candidates=oid_blind)

    # B4 / B5 / B6 は「書かれたバッチ」の形を見る。実行が途中で止まった行も規定の対象。
    not_last = mixed = attack_late = attack_no_begin = 0
    prev_tail = ""
    for _, lines in written:
        heads = [head(l) for l in lines]
        play = [i for i, h in enumerate(heads) if h and h not in READONLY]
        for i, line in enumerate(lines):
            if heads[i] == "turn" and " next" in " " + line:
                if any(j > i for j in play):
                    not_last += 1
                    if any(j < i for j in play) and any(
                            heads[j] in ("phase", "attack", "turn", "block", "combat")
                            for j in play if j > i):
                        mixed += 1
        for i, h in enumerate(heads):
            if h == "attack":
                before = [j for j in play if j < i]
                if any(heads[j] != "phase" for j in before):
                    attack_late += 1          # B6 盤面を動かしてから宣言している
                elif before and "combat.begin" not in prev_tail:
                    # B8 先行はフェーズ移行だけ。直前のバッチが combat.begin で終わって
                    # いれば戦闘開始時の応答窓は開いているので不問。
                    attack_no_begin += 1
                break
        if play:
            prev_tail = lines[play[-1]]
    m.update(turn_next_not_last=not_last, mixed_turn_batches=mixed,
             attack_not_batch_head=attack_late,
             attack_without_begin_step=attack_no_begin)
    if not written and trace:
        for key in ("turn_next_not_last", "mixed_turn_batches", "attack_not_batch_head",
                    "attack_without_begin_step"):
            m[key] = None          # 走ったのに .mtg が無い＝バッチの形を測れない
    return m


def integrity(manifest: dict, spec: dict, results: list) -> list:
    """デッキ・seed・先手が基準どおりで回されたかの確認。ここが崩れると他の比較は無意味。"""
    rows = []
    for seat, deck in manifest["decks"].items():
        path = resolve(deck["list"])
        got = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "(無し)"
        rows.append((f"{seat} デッキ {deck['name']}", deck["sha256"][:12], got[:12],
                     got == deck["sha256"]))
    by_seed = {int(g["seed"]): g for g in spec["games"]}
    for game in manifest["games"]:
        record = next((r for r in results if r.get("seed") == game["seed"]), None)
        if record is None:
            rows.append((f"{game['game']} seed {game['seed']} の結果", "1件", "0件", False))
            continue
        want = by_seed.get(game["seed"], {}).get("first", game["first"])
        rows.append((f"{game['game']} 先手", want, record.get("first"), record.get("first") == want))
        names = sorted((record.get("decks") or {}).values())
        expect = sorted(d["name"] for d in manifest["decks"].values())
        rows.append((f"{game['game']} 使用デッキ", "/".join(expect), "/".join(str(n) for n in names),
                     names == expect))
    return rows


def group_a(skip: bool) -> tuple[bool, str]:
    if skip:
        return True, "省略（--skip-a）"
    out = subprocess.run([sys.executable, str(HERE / "verify.py")], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=CHILD_ENV)
    tail = (out.stdout or "").strip().splitlines()
    if not tail:
        return False, "verify.py の出力が読めなかった: " + ((out.stderr or "").strip() or "出力なし")
    return out.returncode == 0, tail[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="setup.py が作った実行フォルダ")
    ap.add_argument("--skip-a", action="store_true", help="A群（同じseedの初手の再現性）を省く")
    ap.add_argument("--json", action="store_true", help="score.md ではなく機械可読な JSON を出す")
    args = ap.parse_args()

    run = resolve(args.run_dir)
    manifest_path = run / "manifest.json"
    if not manifest_path.exists():
        fail(f"manifest.json が無い（{run}）。setup.py が作ったフォルダを指定すること。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = json.loads(GAMES_SPEC.read_text(encoding="utf-8"))
    cases = {c["seed"]: c for c in json.loads((EXPECTED / "cases.json").read_text(encoding="utf-8"))}
    conditions = json.loads((EXPECTED / "conditions.json").read_text(encoding="utf-8"))

    results_file = resolve(manifest["results"])
    results = []
    if results_file.exists():
        results = [json.loads(l) for l in results_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    observations = {}
    obs_file = run / "observations.json"
    if obs_file.exists():
        observations = json.loads(obs_file.read_text(encoding="utf-8"))

    checks = integrity(manifest, spec, results)
    a_ok, a_note = group_a(args.skip_a)

    per_game, b_fail = {}, 0
    for game in manifest["games"]:
        metrics = process_metrics(run, game["game"])
        per_game[game["game"]] = metrics
    b_rows = []
    for rule in conditions["process"]:
        total = 0
        unknown = False
        for metrics in per_game.values():
            value = metrics.get(rule["metric"])
            if value is None:
                unknown = True
            else:
                total += value
        ok = (not unknown) and total == rule["expect"]
        b_fail += 0 if ok else 1
        b_rows.append((rule["id"], rule["desc"], rule["expect"],
                       "測定不能" if unknown else total, ok, rule["baseline_20260914"]))

    c_rows, c_fail = [], 0
    for game in manifest["games"]:
        seed = game["seed"]
        case = cases.get(seed, {})
        record = next((r for r in results if r.get("seed") == seed), None)
        obs = observations.get(str(seed), {})
        line = conditions["combo"]["line"].get(str(seed))
        combo = obs.get("combo_turn")
        combo_ok = None if combo is None or line is None else combo <= line
        if combo_ok is False:
            c_fail += 1
        c_rows.append({
            "game": game["game"], "seed": seed,
            "winner": record.get("winner") if record else None,
            "turn": record.get("turn") if record else None,
            "concede": record.get("concede") if record else None,
            "life": record.get("life") if record else None,
            "mulligans": record.get("mulligans") if record else None,
            "baseline_ai": case.get("ai_both_seats"),
            "human": case.get("human_piza"),
            "combo_turn": combo, "combo_line": line, "combo_ok": combo_ok,
            "checks": obs.get("checks", {}),
        })

    integrity_ok = all(row[3] for row in checks)
    verdict = 0 if (integrity_ok and a_ok and b_fail == 0 and c_fail == 0) else (
        2 if (integrity_ok and a_ok and b_fail == 0) else 1)

    if args.json:
        print(json.dumps({"run_dir": manifest["run_dir"], "condition": manifest["condition"],
                          "integrity": [{"item": i, "expect": e, "got": g, "ok": o} for i, e, g, o in checks],
                          "group_a": {"ok": a_ok, "note": a_note},
                          "group_b": [{"id": i, "expect": e, "got": g, "ok": o} for i, _, e, g, o, _ in b_rows],
                          "per_game_metrics": per_game, "group_c": c_rows,
                          "verdict": verdict}, ensure_ascii=False, indent=2))
        return verdict

    mark = {True: "OK", False: "NG", None: "未記入"}
    out = [f"# ベンチマーク採点 {manifest['benchmark']}", "",
           f"- 実行フォルダ: `{manifest['run_dir']}`",
           f"- 条件: {manifest['condition']} / tag: {manifest['tag']}",
           f"- 実行日時: {manifest['created_at']}", "",
           "## 0. 実行の同一性", "", "| 項目 | 期待 | 実際 | 判定 |", "|---|---|---|---|"]
    out += [f"| {i} | {e} | {g} | {mark[o]} |" for i, e, g, o in checks]
    out += ["", "## A. 再現性（同じseedの初手）", "",
            f"- {mark[a_ok]} … {a_note}", "",
            "## B. プロセス回帰", "", "| ID | 判定 | 期待 | 実測 | 結果 | 2026-09-14時点 |", "|---|---|---|---|---|---|"]
    out += [f"| {i} | {d} | {e} | {g} | {mark[o]} | {b} |" for i, d, e, g, o, b in b_rows]
    out += ["", "### ゲーム別の生値", "", "| game | バッチ数(書/実行) | " +
            " | ".join(r["metric"] for r in conditions["process"]) + " |",
            "|---|---|" + "---|" * len(conditions["process"])]
    for game, metrics in per_game.items():
        cells = " | ".join(str(metrics.get(r["metric"])) for r in conditions["process"])
        out.append(f"| {game} | {metrics['batches_written']}/{metrics['batches_run']} | {cells} |")
    out += ["", "## C. プレイ品質", "",
            "| seed | 結果 | 投了 | ライフ | マリガン | AI両席(基準) | 人間piza | コンボ成立 | 合格ライン | 判定 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for row in c_rows:
        def brief(d):
            return "—" if not d else f"{d['winner']} T{d['turn']}"
        def pairs(d):
            return "—" if not d else "/".join(f"{k}:{v}" for k, v in sorted(d.items()))
        got = f"{row['winner']} T{row['turn']}" if row["winner"] else "未決着"
        out.append(f"| {row['seed']} | {got} | {row['concede'] or '—'} | {pairs(row['life'])} "
                   f"| {pairs(row['mulligans'])} | {brief(row['baseline_ai'])} "
                   f"| {brief(row['human'])} | {row['combo_turn'] or '未記入'} | "
                   f"{row['combo_line'] or '—'} | {mark[row['combo_ok']]} |")
    out += ["", "### 個別プレイの合否（採点者が証跡を読んで記入する）", "",
            "| seed | " + " | ".join(r["id"] for r in conditions["play"]) + " |",
            "|---|" + "---|" * len(conditions["play"])]
    for row in c_rows:
        cells = " | ".join(row["checks"].get(r["id"], "未記入") for r in conditions["play"])
        out.append(f"| {row['seed']} | {cells} |")
    out += ["", "条件の本文は `expected/conditions.json` の `play`。記入は実行フォルダの "
            "`observations.json` に `{\"9141\": {\"combo_turn\": 12, \"checks\": {\"C6\": \"OK\"}}}` "
            "の形で置き、このスクリプトを再実行する。", "",
            "## 判定", "",
            f"- 同一性: {mark[integrity_ok]} / A群: {mark[a_ok]} / B群: {len(b_rows) - b_fail}/{len(b_rows)} 達成"
            f" / C群コンボ成立: {sum(1 for r in c_rows if r['combo_ok'])}/"
            f"{sum(1 for r in c_rows if r['combo_ok'] is not None)} 達成",
            f"- 終了コード {verdict}"]
    text = "\n".join(out) + "\n"
    (run / "score.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"→ {(run / 'score.md').relative_to(ROOT).as_posix()}")
    return verdict


if __name__ == "__main__":
    raise SystemExit(main())
