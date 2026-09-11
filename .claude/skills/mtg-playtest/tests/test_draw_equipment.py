import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg


class DrawEquipmentTests(unittest.TestCase):
    def call(self, state, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            mtg.dispatch(['--state', str(state), '--offline', *args])
        return output.getvalue()

    def test_incomplete_draw_in_batch_is_rejected_before_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root/'g.json'
            self.call(state, 'init', '--seed', '1', '--first', 'P1')
            before = state.read_bytes()
            batch = root/'b.mtg'
            batch.write_text('life P1 -2\nturn next --draw\n', encoding='utf-8')
            with self.assertRaises(SystemExit):
                self.call(state, 'run', str(batch), '--compact')
            self.assertEqual(state.read_bytes(), before)

    def test_phase_warns_without_drawing_and_turn_draws_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)/'g.json'
            self.call(state, 'init', '--seed', '1', '--first', 'P1')
            st = json.loads(state.read_text(encoding='utf-8'))
            st['objects'] = {str(i): mtg.new_object(i, 'Fixture', 'P2') for i in (1, 2)}
            st['zones']['P2:library'] = [1, 2]
            state.write_text(json.dumps(st), encoding='utf-8')
            original = state.read_bytes()
            self.call(state, 'turn', 'next')
            out = self.call(state, 'phase', 'to', 'precombat_main')
            self.assertIn('phaseはドローしません', out)
            self.assertEqual(json.loads(state.read_text(encoding='utf-8'))['zones']['P2:hand'], [])
            state.write_bytes(original)
            self.call(state, 'turn', 'next', '--to', 'precombat_main', '--draw')
            st = json.loads(state.read_text(encoding='utf-8'))
            self.assertEqual(st['zones']['P2:hand'], [1])
            self.assertEqual(st['zones']['P2:library'], [2])

    def test_mod_set_is_idempotent_and_preserves_other_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)/'g.json'
            self.call(state, 'init', '--seed', '1', '--first', 'P1')
            self.call(state, 'token', 'P1', 'Fixture', '--power', '1', '--toughness', '1')
            for name in ('EquipmentA', 'EquipmentB'):
                self.call(state, 'token', 'P1', name, '--types', 'Artifact', '--subtypes', 'Equipment')
            self.call(state, 'attach', '2', '--to', '1')
            self.call(state, 'attach', '3', '--to', '1')
            self.call(state, 'mod', '1', '+2/+0', '--until', 'attached', '--src', '2')
            self.call(state, 'mod', '1', '+1/+1', '--until', 'attached', '--src', '3')
            for _ in range(2):
                self.call(state, 'mod', '1', '+4/+0', '--until', 'attached', '--src', '2', '--set')
            st = json.loads(state.read_text(encoding='utf-8'))
            self.assertEqual(len(st['objects']['1']['mods']), 2)
            self.assertEqual(mtg.pt(st, st['objects']['1']), (6, 2))

    def test_blocked_double_striker_with_no_remaining_blockers(self):
        for keyword in (None, 'Trample', 'トランプル'):
            for trample_flag in ((), ('--trample',)):
                with self.subTest(keyword=keyword, flag=trample_flag), tempfile.TemporaryDirectory() as tmp:
                    state = pathlib.Path(tmp)/'g.json'
                    self.call(state, 'init', '--seed', '1', '--first', 'P1')
                    self.call(state, 'token', 'P1', 'DS Attacker', '--types', 'Creature',
                              '--power', '5', '--toughness', '5', '--oracle', 'Double strike')
                    self.call(state, 'token', 'P2', 'Wall', '--types', 'Creature',
                              '--power', '0', '--toughness', '1')
                    self.call(state, 'phase', 'to', 'combat.attackers')
                    self.call(state, 'attack', '1', '--target', 'P2')
                    self.call(state, 'phase', 'to', 'combat.blockers')
                    self.call(state, 'block', '2', '1')
                    self.call(state, 'phase', 'to', 'combat.damage')
                    self.call(state, 'combat', 'damage', '--step', 'first')
                    self.call(state, 'sba', '--apply-deaths')
                    st = json.loads(state.read_text(encoding='utf-8'))
                    self.assertNotIn(2, st['zones']['P2:battlefield'])
                    self.assertEqual(st['players']['P2']['life'], 20)
                    # Evaluate keywords at the regular step, including newly granted trample.
                    if keyword:
                        self.call(state, 'grant', '1', keyword, '--until', 'permanent')
                    self.assertIn('ブロック済み（ブロッカー不在）', self.call(state, 'combat', 'show'))
                    out = self.call(state, 'combat', 'damage', '--step', 'regular', *trample_flag)
                    st = json.loads(state.read_text(encoding='utf-8'))
                    self.assertEqual(st['players']['P2']['life'], 15 if keyword else 20)
                    if not keyword:
                        self.assertNotIn('→ P2 に5点', out)
                    self.call(state, 'undo', '1')
                    st = json.loads(state.read_text(encoding='utf-8'))
                    self.assertEqual(st['players']['P2']['life'], 20)

    def test_unblocked_double_striker_still_hits_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)/'g.json'
            self.call(state, 'init', '--seed', '1', '--first', 'P1')
            self.call(state, 'token', 'P1', 'DS Attacker', '--types', 'Creature',
                      '--power', '5', '--toughness', '5', '--oracle', 'Double strike')
            self.call(state, 'phase', 'to', 'combat.attackers')
            self.call(state, 'attack', '1', '--target', 'P2')
            self.call(state, 'phase', 'to', 'combat.damage')
            for step in ('first', 'regular'):
                self.call(state, 'combat', 'damage', '--step', step)
            self.assertEqual(json.loads(state.read_text(encoding='utf-8'))['players']['P2']['life'], 10)

    def test_double_strike_trample_can_use_explicit_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = pathlib.Path(tmp)/'g.json'
            self.call(state, 'init', '--seed', '1', '--first', 'P1')
            self.call(state, 'token', 'P1', 'Attacker', '--types', 'Creature', '--power', '7', '--toughness', '2')
            self.call(state, 'token', 'P2', 'Blocker', '--types', 'Creature', '--power', '1', '--toughness', '3')
            for kw in ('Double strike', 'Trample'):
                self.call(state, 'grant', '1', kw, '--until', 'permanent')
            self.call(state, 'phase', 'to', 'combat.attackers')
            self.call(state, 'attack', '1', '--target', 'P2')
            self.call(state, 'phase', 'to', 'combat.blockers')
            self.call(state, 'block', '2', '1')
            self.call(state, 'phase', 'to', 'combat.damage')
            self.call(state, 'combat', 'damage', '--step', 'first', '--trample')
            self.assertEqual(json.loads(state.read_text(encoding='utf-8'))['players']['P2']['life'], 16)
            self.call(state, 'sba', '--apply-deaths')
            self.call(state, 'combat', 'damage', '--step', 'regular', '--trample')
            self.assertEqual(json.loads(state.read_text(encoding='utf-8'))['players']['P2']['life'], 9)


if __name__ == "__main__":
    unittest.main()
