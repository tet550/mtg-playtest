import contextlib
import copy
import io
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg
import pizza_recipe
import check_report


class Sim2Tests(unittest.TestCase):
    def test_generated_nonrecover_cycle_actual_state(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = pathlib.Path(tmp)
            state = root / 'game.json'
            def call(parts):
                mtg.dispatch(['--state', str(state), '--offline', *parts])
            call(['init', '--seed', '3', '--first', 'P2'])
            call(['token', 'P2', 'Source', '--types', 'Creature', '--power', '4', '--toughness', '5'])
            call(['token', 'P2', 'Chrome', '--types', 'Artifact'])
            call(['token', 'P2', 'Pizza', '--types', 'Artifact'])
            st = json.loads(state.read_text(encoding='utf-8'))
            ids = {o['name']: o['oid'] for o in st['objects'].values()}
            call(['mana', 'P2', 'add', 'RR'])
            batch = root / 'cycle.mtg'
            batch.write_text(pizza_recipe.generate('P2', ids['Source'], ids['Chrome'], ids['Pizza'],
                4, st['next_oid'], 9, 'verified fixture', recover=False, color='R', pool=2), encoding='utf-8')
            call(['run', str(batch), '--compact'])
            st = json.loads(state.read_text(encoding='utf-8'))
            self.assertEqual(st['players']['P2']['pool'], {'R': 65})
            self.assertEqual(st['players']['P2']['life'], 20)
            self.assertEqual(mtg.pt(st, st['objects'][str(ids['Source'])]), (22, 23))
            self.assertFalse(st['objects'][str(ids['Source'])]['tapped'])
            self.assertEqual(st['zones']['stack'], [])
            self.assertEqual(st['next_oid'], 31)
            self.assertEqual(len(st['zones']['P2:battlefield']), 12)
            self.assertIn('sacrifice at next end step', st['effects'][-1]['text'])

    def test_buffer_must_cover_all_cycles(self):
        with self.assertRaises(ValueError):
            pizza_recipe.generate('P1', 1, 2, 3, 1, 4, 2, 'verified', recover=False, pool=4)

    def test_attack_typo_does_not_advance(self):
        st = {'phase': 'combat.begin'}
        before = copy.deepcopy(st)
        with self.assertRaisesRegex(SystemExit, 'attack は席'):
            mtg.cmd_attack(NS(refs=['P2', '96']), st)
        self.assertEqual(st, before)

    def test_damage_wrong_phase_does_not_mutate(self):
        st = {'phase': 'combat.blockers', 'zones': {'stack': []}, 'combat': {'attackers': {}, 'blocks': {}}}
        before = copy.deepcopy(st)
        with self.assertRaisesRegex(SystemExit, 'combat.damage'):
            mtg.cmd_combat(NS(op='damage'), st)
        self.assertEqual(st, before)

    def test_report_bad_reference_and_arithmetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root/'run.jsonl').write_text('{}\n', encoding='utf-8')
            report = root/'report.md'
            report.write_text('［R1:1-2］\nR1 = run.jsonl\n検算: power=6 pool=1 count=8 cost=5 final_power=22 final_pool=50\n', encoding='utf-8')
            self.assertEqual(len(check_report.check(report)), 2)
            report.write_text(report.read_text(encoding='utf-8').replace('1-2', '1-1').replace('final_pool=50', 'final_pool=65'), encoding='utf-8')
            self.assertEqual(check_report.check(report), [])

    def test_report_results_and_game_local_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root/'short.jsonl').write_text('{}\n', encoding='utf-8')
            (root/'long.jsonl').write_text('{}\n{}\n', encoding='utf-8')
            records = [dict(seed=i, winner='P2', turn=6, life={'P1': -2, 'P2': 16}) for i in (1, 2)]
            (root/'results.jsonl').write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')
            report = root/'report.md'
            body = ''.join(f'### G0{i}\n条件: seed={i}\n［R1:1-{i}］\n結果: P2勝利 | T6 P2 | ライフ=P1 -2/P2 16\nR1 = {name}.jsonl\n'
                for i, name in [(1, 'short'), (2, 'long')])
            report.write_text(body, encoding='utf-8')
            self.assertEqual(check_report.check(report), [])
            report.write_text(body.replace('P2勝利', 'P1勝利').replace('T6 P2', 'T7 P2').replace('P2 16', 'P2 20'), encoding='utf-8')
            self.assertEqual(len(check_report.check(report)), 6)


if __name__ == "__main__":
    unittest.main()
