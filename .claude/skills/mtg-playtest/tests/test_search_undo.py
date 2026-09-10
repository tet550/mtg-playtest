import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg


class SearchUndoTests(unittest.TestCase):
    def call(self, state, *command):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            mtg.dispatch(['--state', str(state), '--offline', *command])
        return output.getvalue()

    def setup_state(self, root):
        state = root/'game.json'
        self.call(state, 'init', '--seed', '123', '--first', 'P1')
        st = json.loads(state.read_text(encoding='utf-8'))
        st['objects'] = {str(i): mtg.new_object(i, 'Fixture', 'P1') for i in (1, 2)}
        st['zones']['P1:library'] = [1, 2]
        st['next_oid'] = 3
        state.write_text(json.dumps(st, ensure_ascii=False), encoding='utf-8')
        return state

    def test_ambiguous_search_stops_batch_without_rng_or_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = self.setup_state(root)
            before = state.read_bytes()
            batch = root/'batch.mtg'
            batch.write_text('search P1 Fixture\nlife P1 -5\n', encoding='utf-8')
            with patch.object(mtg, 'find_by_name', return_value=([1, 2], True)), \
                 patch.object(mtg, 'disp_oid', return_value='Fixture'), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    self.call(state, 'run', str(batch), '--compact')
            self.assertEqual(state.read_bytes(), before)
            self.assertFalse(list(root.glob('history/game/*.json')))
            rows = [json.loads(x) for x in next(root.glob('output/game/*.jsonl')).read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertIn('候補が複数', rows[0]['stdout'])
            self.assertIn('サーチ未完了', rows[0]['error'])
            # Resolving the selection performs exactly one shuffle.
            with patch.object(mtg, 'disp_oid', return_value='Fixture'):
                self.call(state, 'search', 'P1', '--oid', '1')
            st = json.loads(state.read_text(encoding='utf-8'))
            self.assertEqual(st['zones']['P1:hand'], [1])
            self.assertEqual(st['rng_seq'], 1)

    def test_multi_undo_matches_repeated_undo_and_restores_rng(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            states = [self.setup_state(pathlib.Path(x)) for x in (a, b)]
            for state in states:
                self.call(state, 'life', 'P1', '-2')
                self.call(state, 'shuffle', 'P1')
                self.call(state, 'life', 'P2', '-3')
            self.call(states[0], 'undo', '2')
            self.call(states[1], 'undo')
            self.call(states[1], 'undo')
            self.assertEqual(states[0].read_bytes(), states[1].read_bytes())
            restored = json.loads(states[0].read_text(encoding='utf-8'))
            self.assertEqual(restored['rng_seq'], 0)
            self.assertEqual(restored['players']['P1']['life'], 18)
            for state in states:
                self.assertEqual(len(list((state.parent/'history/game').glob('*.json'))), 1)
                self.call(state, 'life', 'P2', '-1')
                self.call(state, 'undo')
                self.assertEqual(json.loads(state.read_text(encoding='utf-8')), restored)

    def test_invalid_undo_count_keeps_state_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = self.setup_state(root)
            self.call(state, 'life', 'P1', '-2')
            before = state.read_bytes()
            history = {p.name:p.read_bytes() for p in root.glob('history/game/*.json')}
            for count in ('0', '-1', '2'):
                with self.assertRaises(SystemExit):
                    self.call(state, 'undo', count)
                self.assertEqual(state.read_bytes(), before)
                self.assertEqual({p.name:p.read_bytes() for p in root.glob('history/game/*.json')}, history)


if __name__ == "__main__":
    unittest.main()
