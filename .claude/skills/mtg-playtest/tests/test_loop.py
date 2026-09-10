import contextlib
import copy
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg


class LoopTests(unittest.TestCase):
    def state(self):
        return dict(turn=1, active='P1', phase='precombat_main', log=[],
                    players={'P1': {'life': 20, 'pool': {}}},
                    zones={'stack': [], 'P1:library': [], 'P1:hand': []})

    def args(self, *extra):
        return mtg.build_parser().parse_args(
            ['loop', 'P1', '1000000000', '--life', '3', '--mana', 'GG',
             '--proof', 'verified and agreed', *extra])

    def test_large_repeat_and_undo(self):
        st = self.state()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            args = self.args()
            args.state = str(pathlib.Path(d) / 'state.json')
            mtg.save(args, st, snapshot=False)
            mtg.cmd_loop(args, st)
            mtg.save(args, st)
            self.assertEqual(st['players']['P1']['life'], 3000000020)
            self.assertEqual(st['players']['P1']['pool'], {'G': 2000000000})
            mtg.cmd_undo(args, None)
            self.assertEqual(mtg.load(args), self.state())

    def test_invalid_input_does_not_mutate(self):
        for extra in [('--draw', '1'), ('--life', '-1'), ('--mana', 'X'),
                      ('--proof', ' ')]:
            st = self.state()
            before = copy.deepcopy(st)
            with self.assertRaises(SystemExit):
                mtg.cmd_loop(self.args(*extra), st)
            self.assertEqual(st, before)

    def test_pending_stack_rejected(self):
        st = self.state()
        st['zones']['stack'] = [1]
        before = copy.deepcopy(st)
        with self.assertRaises(SystemExit):
            mtg.cmd_loop(self.args(), st)
        self.assertEqual(st, before)


if __name__ == '__main__':
    unittest.main()
