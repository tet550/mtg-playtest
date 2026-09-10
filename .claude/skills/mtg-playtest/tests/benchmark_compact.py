"""Replay an existing recorded game twice in temporary dirs, never its live state."""
import contextlib
import io
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import mtg
import pizza_recipe


def main():
    source = pathlib.Path(sys.argv[1]).resolve()
    report = pathlib.Path(sys.argv[2]).resolve()
    batches = sorted(source.glob("[0-9][0-9].mtg"))
    starting = source / "history/game/0000.json"
    metrics, finals = {}, {}
    with tempfile.TemporaryDirectory() as temp:
        for mode, flags in (("quiet", ["--quiet"]),
                            ("compact", ["--compact", "--delta"])):
            root = pathlib.Path(temp) / mode
            root.mkdir()
            state = root / "game.json"
            shutil.copyfile(starting, state)
            output = io.StringIO()
            for batch in batches:
                with contextlib.redirect_stdout(output):
                    mtg.dispatch(["--state", str(state), "--offline", "run", str(batch), *flags])
            finals[mode] = json.loads(state.read_text(encoding="utf-8"))
            metrics[mode] = dict(characters=len(output.getvalue()),
                                 lines=len(output.getvalue().splitlines()),
                                 snapshots=len(list((root / "history/game").glob("*.json"))))
            if mode == "compact":
                logs = sorted((root / "output/game").glob("*.jsonl"))
                metrics[mode]["transcript_commands"] = sum(
                    len(p.read_text(encoding="utf-8").splitlines()) for p in logs)
            # Isolated output is retained as a review artifact; no hidden JSON is printed.
            (report.parent / (mode + "-output.txt")).write_text(output.getvalue(), encoding="utf-8")
        # Check generated recipe against the observed hand-authored growing cycle.
        def replay_cycle(body, name):
            root = pathlib.Path(temp) / name
            root.mkdir()
            state = root / "game.json"
            shutil.copyfile(starting, state)
            with contextlib.redirect_stdout(io.StringIO()):
                for batch in batches[:16]:
                    mtg.dispatch(["--state", str(state), "--offline", "run", str(batch), "--quiet"])
                path = root / "cycle.mtg"
                path.write_text(body, encoding="utf-8")
                mtg.dispatch(["--state", str(state), "--offline", "run", str(path), "--compact"])
            st = json.loads(state.read_text(encoding="utf-8"))
            st.pop("log", None)  # Ability labels differ; all game data must agree.
            return st
        authored = (source / "17.mtg").read_text(encoding="utf-8")
        generated = pizza_recipe.generate("P2", 70, 71, 92, 7, 145, 5, "verified recorded fixture")
        metrics["recipe_state_equal"] = replay_cycle(authored, "authored") == replay_cycle(generated, "generated")
    # end records a timestamp; compare gameplay state and log without that metadata.
    metrics["state_equal"] = finals["quiet"] == finals["compact"]
    metrics["character_reduction_percent"] = round(
        100 * (1 - metrics["compact"]["characters"] / metrics["quiet"]["characters"]), 1)
    report.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if not metrics["state_equal"] or not metrics["recipe_state_equal"]:
        sys.exit("Replay state mismatch")


if __name__ == "__main__":
    main()
