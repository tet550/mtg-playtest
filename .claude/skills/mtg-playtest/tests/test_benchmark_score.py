"""benchmarks/ の採点スクリプト（score.py）の数え方を固定する。

対局を回さずに、証跡と書かれたバッチだけを合成して B 群の指標を確かめる。
実物の対局で回帰に気づくのは遅すぎるため、数え方そのものをここで押さえる。
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[4]
SCORE = ROOT / "benchmarks" / "20260914-jund-vs-piza" / "score.py"


def load_score():
    spec = importlib.util.spec_from_file_location("bench_score", SCORE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


score = load_score()


def build(run: pathlib.Path, game: str, batches: list[list[str]]):
    """1バッチ＝1つの .mtg と1つの run-*.jsonl。証跡の並びは mtime で決まる。"""
    out = run / "output" / game
    out.mkdir(parents=True, exist_ok=True)
    for number, lines in enumerate(batches, 1):
        (run / f"{game}-{number:03d}.mtg").write_text("\n".join(lines) + "\n", encoding="utf-8")
        records = [{"command": line, "stdout": "", "source_line": i}
                   for i, line in enumerate(lines, 1)]
        path = out / f"run-{number:03d}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                        encoding="utf-8")
        # mtime はバッチの順序として読まれるので、作成順どおりに明示しておく。
        import os
        os.utime(path, (number, number))


class ProcessMetrics(unittest.TestCase):
    def metrics(self, batches):
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp)
            build(run, "g01", batches)
            return score.process_metrics(run, "g01")

    def test_clean_run_counts_nothing(self):
        m = self.metrics([
            ["draw P1 7", "draw P2 7", 'note "方針"', "show --hand both"],
            ["attack P1 5", "show --ids --packed"],
            ["turn next --to precombat_main --draw", "show --ids --packed --hand both"],
        ])
        self.assertEqual(0, m["correction_notes"])
        self.assertEqual(0, m["undo"])
        self.assertEqual(0, m["turn_next_not_last"])
        self.assertEqual(0, m["mixed_turn_batches"])
        self.assertEqual(0, m["attack_not_batch_head"])
        self.assertEqual(0, m["attack_without_begin_step"])
        self.assertEqual(0, m["oid_without_candidates"])

    def test_trailing_readonly_after_turn_next_is_allowed(self):
        """バッチ末尾の確認表示（show/note）は「turn next が最終行」を壊さない。"""
        m = self.metrics([["turn next --to precombat_main --draw",
                           'note "T3開始"', "show --ids --packed --hand both"]])
        self.assertEqual(0, m["turn_next_not_last"])

    def test_play_after_turn_next_is_counted(self):
        m = self.metrics([["tap P1 12", "turn next --to precombat_main --draw",
                           "phase to combat.declare_attackers", "attack P2 30"]])
        self.assertEqual(1, m["turn_next_not_last"])
        self.assertEqual(1, m["mixed_turn_batches"])

    def test_attack_must_start_its_batch(self):
        m = self.metrics([["mana P1 add R", "attack P1 5"]])
        self.assertEqual(1, m["attack_not_batch_head"])
        m = self.metrics([["attack P1 5", "show --ids"]])
        self.assertEqual(0, m["attack_not_batch_head"])

    def test_phase_advance_before_attack_is_its_own_finding(self):
        """フェーズ移行だけの先行は盤面を動かしてからの宣言とは別物なので B6 と分ける。"""
        m = self.metrics([["phase to combat.attackers", "attack P1 5"]])
        self.assertEqual(0, m["attack_not_batch_head"])
        self.assertEqual(1, m["attack_without_begin_step"], "戦闘開始時で切れていない")

        opened = self.metrics([["phase to combat.begin"],
                               ["phase to combat.attackers", "attack P1 5"]])
        self.assertEqual(0, opened["attack_without_begin_step"], "応答窓は前バッチで開いている")

        m = self.metrics([["mana P1 add R", "phase to combat.attackers", "attack P1 5"]])
        self.assertEqual(1, m["attack_not_batch_head"])
        self.assertEqual(0, m["attack_without_begin_step"], "重い方の B6 だけを数える")

    def test_corrections_and_undo(self):
        m = self.metrics([['note "訂正: ライフを補正" --event E09', "undo"]])
        self.assertEqual(1, m["correction_notes"])
        self.assertEqual(1, m["undo"])

    def test_oid_needs_candidates_from_an_earlier_batch(self):
        same = self.metrics([["search P1 Swamp", "search P1 Swamp --oid 12"]])
        self.assertEqual(1, same["oid_without_candidates"], "同一バッチでは出力を読めていない")
        split = self.metrics([["search P1 Swamp"], ["search P1 Swamp --oid 12"]])
        self.assertEqual(0, split["oid_without_candidates"])

    def test_remind_must_ride_along_with_the_landing_batch(self):
        together = self.metrics([["move 21 battlefield",
                                  "remind add アップキープ確認 --on turn --player P2 --src 21"]])
        self.assertEqual(0, together["remind_late"])
        later = self.metrics([["move 21 battlefield"],
                              ["remind add アップキープ確認 --on turn --player P2 --src 21"]])
        self.assertEqual(1, later["remind_late"])

    def test_seeing_the_oid_in_hand_is_not_the_landing(self):
        """手札表示やスタックでの初出を起点にすると、正しく書いたバッチまで後付け扱いになる。"""
        m = self.metrics([["show --ids --packed --hand both"],
                          ["stack push 21 --cast --controller P2"],
                          ["move 21 battlefield",
                           "remind add アップキープ確認 --on turn --player P2 --src 21"]])
        self.assertEqual(0, m["remind_late"])

    def test_relanding_resets_the_landing_batch(self):
        """割られて出し直したパーマネントは、新しい世代の着地バッチが起点になる。"""
        m = self.metrics([["move 21 battlefield",
                           "remind add 確認 --on turn --player P2 --src 21"],
                          ["move 21 graveyard"],
                          ["move 21 battlefield",
                           "remind add 確認 --on turn --player P2 --src 21"]])
        self.assertEqual(0, m["remind_late"])

    def test_batch_shape_is_unmeasurable_without_written_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = pathlib.Path(tmp)
            build(run, "g01", [["draw P1 7"]])
            for path in run.glob("g01-*.mtg"):
                path.unlink()
            m = score.process_metrics(run, "g01")
        self.assertIsNone(m["turn_next_not_last"])


class ExpectationFiles(unittest.TestCase):
    """目隠しの前提：実行フェーズが読む run/ に期待値を置かない。"""

    bench = ROOT / "benchmarks" / "20260914-jund-vs-piza"

    def test_run_side_has_no_expected_values(self):
        for path in (self.bench / "run").rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("cases.json", text, f"{path.name} が期待値を参照している")
                self.assertNotIn("conditions.json", text, f"{path.name} が合否条件を参照している")

    def test_games_and_cases_agree(self):
        games = json.loads((self.bench / "run" / "games.json").read_text(encoding="utf-8"))
        cases = json.loads((self.bench / "expected" / "cases.json").read_text(encoding="utf-8"))
        self.assertEqual([(g["seed"], g["first"]) for g in games["games"]],
                         [(c["seed"], c["first"][:2]) for c in cases])


if __name__ == "__main__":
    unittest.main()
