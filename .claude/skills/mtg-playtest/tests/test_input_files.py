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
import input_files


class InputTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.state = self.root / 'g01.json'
        override = patch.object(input_files, 'ROOT', self.root)
        override.start()
        self.addCleanup(override.stop)
        self.call('init', '--seed', '7')

    def dispatch(self, argv, text=''):
        with patch('sys.stdin', io.StringIO(text)), contextlib.redirect_stdout(io.StringIO()) as output:
            mtg.dispatch(argv)
        return output.getvalue()

    def call(self, *args, text=''):
        return self.dispatch(['--state', str(self.state), '--offline', *args], text)

    def read(self):
        return json.loads(self.state.read_text(encoding='utf-8'))

    def test_stdin_saves_exact_source_and_retains_full_show(self):
        body = '# 日本語\nlife P1 -2\nshow --hand both\n'
        out = self.call('run', '-', '--compact', text=body)
        self.assertEqual((self.root/'g01-001.mtg').read_text(encoding='utf-8'), body)
        self.assertIn('ライフ 18', out)
        self.assertIn('P1 P1 の手札', out)
        self.assertNotIn('盤面差分', out)
        records = [json.loads(line) for line in next((self.root/'output/g01').glob('*.jsonl')).read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['source_line'] for r in records], [2, 3])
        self.call('undo', '1')
        self.assertEqual(self.read()['players']['P1']['life'], 20)

    def test_invalid_stdin_neither_changes_state_nor_allocates_file(self):
        before = self.state.read_bytes()
        with self.assertRaises(SystemExit):
            self.call('run', '-', '--compact', text='life P1 -2\nunknown-command\n')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(list(self.root.glob('*.mtg')), [])

    def test_runtime_failure_retains_source_and_prior_undo_units(self):
        with self.assertRaises(SystemExit):
            self.call('run', '-', '--compact', text='life P1 -2\nmove 999 hand\n')
        self.assertTrue((self.root/'g01-001.mtg').exists())
        self.assertEqual(self.read()['players']['P1']['life'], 18)
        self.call('undo', '1')
        self.assertEqual(self.read()['players']['P1']['life'], 20)

    def test_generated_file_never_overwrites_existing_or_fills_gap(self):
        (self.root/'g01-010.mtg').write_text('old', encoding='utf-8')
        self.call('run', '-', text='life P1 -1\n')
        self.assertEqual((self.root/'g01-010.mtg').read_text(), 'old')
        self.assertTrue((self.root/'g01-011.mtg').exists())

    def test_named_session_is_explicit_and_paths_are_fixed(self):
        self.call('session', 'green')
        out = self.dispatch(['--session', 'green', '--offline', 'run', '-', '--compact'], 'life P1 -1\nshow\n')
        self.assertIn('ライフ 19', out)
        self.assertEqual(self.read()['players']['P1']['life'], 19)
        with self.assertRaisesRegex(SystemExit, '登録済み'):
            self.call('session', 'green')
        for argv in [['--session', 'missing', 'show'], ['--session', '../green', 'show'],
                     ['--session', 'green', '--state', 'other.json', 'show']]:
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                self.dispatch(argv)

    def test_session_keeps_seat_restriction(self):
        self.call('session', 'green')
        with self.assertRaisesRegex(SystemExit, '隠匿情報'):
            self.dispatch(['--session', 'green', '--as', 'P1', 'show', '--hand', 'both'])

    def pending(self):
        self.call('pending', 'add', '回復', '--controller', 'P1')
        self.call('pending', 'stack', 'T1')

    def test_inline_resolution_preserves_atomic_undo_and_source(self):
        self.pending()
        self.call('pending', 'resolve', 'T1', '--do', 'life P1 2', '--do', 'life P2 -1', '--part', 'bonus')
        st = self.read()
        self.assertEqual(st['players']['P1']['life'], 22)
        self.assertEqual(st['players']['P2']['life'], 19)
        self.assertEqual((self.root/'g01-001.mtg').read_text(), 'life P1 2\nlife P2 -1\n')
        self.call('undo', '1')
        self.assertEqual(self.read()['players']['P1']['life'], 20)
        self.assertEqual(self.read()['players']['P2']['life'], 20)
        self.assertTrue(self.read()['zones']['stack'])

    def test_inline_failure_is_atomic_and_does_not_write_source(self):
        self.pending()
        before = self.state.read_bytes()
        for operation in ['move 999 hand', 'sba --apply-deaths']:
            with self.subTest(operation=operation), self.assertRaises(SystemExit):
                self.call('pending', 'resolve', 'T1', '--do', 'life P1 2', '--do', operation, '--part', 'bonus')
            self.assertEqual(self.state.read_bytes(), before)
            self.assertEqual(list(self.root.glob('*.mtg')), [])

    def test_inline_observation_must_be_last_and_sources_exclusive(self):
        self.pending()
        before = self.state.read_bytes()
        with self.assertRaisesRegex(SystemExit, '区間の最後'):
            self.call('pending', 'resolve', 'T1', '--do', 'look P1 1', '--do', 'life P1 2', '--part', 'a')
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.call('pending', 'resolve', 'T1', '--do', 'life P1 2', '--file', 'x', '--part', 'a')
        self.assertEqual(self.state.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
