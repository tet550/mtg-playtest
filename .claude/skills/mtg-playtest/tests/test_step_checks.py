"""Timing barriers preserve ordering, reservations, and undo."""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg


class StepTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / 'g.json'
        self.call('init', '--seed', '7')
        st = self.read()
        st['phase'] = 'ending.cleanup'
        for oid, pid, zone in [(1, 'P2', 'battlefield'), (2, 'P2', 'library')]:
            name = 'fixture' + str(oid)
            st['objects'][str(oid)] = mtg.new_object(oid, name, pid)
            st['cards'][name] = dict(token=True, types=['Creature'], subtypes=[], power='2', toughness='2', oracle='')
            st['zones'][pid + ':' + zone].append(oid)
        st['next_oid'] = 3
        self.write(st)

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(['--state', str(self.path), '--offline', *args])
        return out.getvalue()

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def write(self, st):
        self.path.write_text(json.dumps(st), encoding='utf-8')

    def text(self, value, phase=None):
        st = self.read()
        st['cards']['fixture1']['oracle'] = value
        if phase:
            st.update(phase=phase, active='P2')
        self.write(st)

    def test_upkeep_stops_before_draw_and_blocks_bypass(self):
        self.text('At the beginning of your upkeep, you may mill a card.')
        out = self.call('turn', 'next', '--to', 'precombat_main', '--draw')
        self.assertIn('T1 要確認', out)
        self.assertEqual(self.read()['phase'], 'beginning.upkeep')
        self.assertEqual(self.read()['zones']['P2:library'], [2])
        before = self.path.read_bytes()
        for args in [('draw', 'P2', '1'), ('phase', 'set', 'precombat_main'), ('pass', 'P2'), ('turn', 'next')]:
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.call(*args)
            self.assertEqual(before, self.path.read_bytes())
        self.call('pending', 'confirm', 'T1', '--reason', '誘発する')
        self.call('pending', 'stack', 'T1')
        self.call('pending', 'resolve', 'T1', '--do', 'mill P2 1', '--part', 'mill')
        self.assertEqual(self.read()['zones']['P2:graveyard'], [2])
        self.call('phase', 'to', 'precombat_main')
        self.assertEqual(len(self.read()['pending']), 1)

    def test_draw_step_trigger_follows_normal_draw(self):
        self.text('At the beginning of your draw step, gain 1 life.')
        self.call('turn', 'next', '--to', 'precombat_main', '--draw')
        self.assertEqual(self.read()['phase'], 'beginning.draw')
        self.assertEqual(self.read()['zones']['P2:hand'], [2])

    def test_phase_draw_requires_normal_draw_before_trigger_stack(self):
        self.text('At the beginning of your draw step, gain 1 life.')
        self.call('turn', 'next')
        self.call('phase', 'to', 'precombat_main')
        with self.assertRaises(SystemExit):
            self.call('pending', 'confirm', 'T1', '--reason', '成立')
        self.call('draw', 'P2', '1')
        self.call('pending', 'confirm', 'T1', '--reason', '成立')
        self.call('pending', 'stack', 'T1')
        self.assertEqual(self.read()['zones']['P2:hand'], [2])

    def test_ability_word_prefix_is_detected(self):
        self.text('Survival — At the beginning of your second main phase, if this creature is tapped, gain 1 life.', 'precombat_main')
        self.call('phase', 'to', 'ending.end')
        self.assertEqual(self.read()['phase'], 'postcombat_main')

    def test_opponents_upkeep_and_quoted_effect_name(self):
        self.text('At the beginning of your opponent\'s upkeep, create a token named "Test".', 'ending.cleanup')
        self.call('turn', 'next', '--to', 'precombat_main')
        self.assertEqual(self.read()['phase'], 'beginning.upkeep')

    def test_granted_trigger_text_is_not_source_trigger(self):
        self.text('This creature has "At the beginning of your upkeep, gain 1 life."')
        self.call('turn', 'next', '--to', 'precombat_main', '--draw')
        self.assertFalse(self.read().get('pending'))

    def test_no_trigger_preserves_fast_path(self):
        self.text('This creature gets +1/+1 until end of turn.')
        self.call('turn', 'next', '--to', 'precombat_main', '--draw')
        self.assertEqual(self.read()['phase'], 'precombat_main')
        self.assertEqual(self.read()['zones']['P2:hand'], [2])

    def test_japanese_end_step_cannot_be_skipped_by_turn(self):
        self.text('あなたの終了ステップの開始時に、カード１枚を引いてもよい。', 'precombat_main')
        self.call('turn', 'next')
        self.assertEqual(self.read()['phase'], 'ending.end')
        self.assertEqual(self.read()['turn'], 1)
        self.call('pending', 'cancel', 'T1', '--reason', 'テスト用に不成立と裁定')
        self.call('turn', 'next')
        self.assertEqual(self.read()['turn'], 2)

    def test_combat_start_and_end_set_stop_at_first_candidate(self):
        self.text('At the beginning of combat on your turn, gain 1 life.\nAt end of combat, gain 2 life.', 'precombat_main')
        self.call('phase', 'set', 'postcombat_main')
        self.assertEqual(self.read()['phase'], 'combat.begin')
        self.call('pending', 'cancel', 'T1', '--reason', '不成立')
        self.call('phase', 'to', 'postcombat_main')
        self.assertEqual(self.read()['phase'], 'combat.end')

    def test_delayed_text_does_not_trigger_without_reservation(self):
        self.text('Mobilize 2 (Whenever this creature attacks, create two tokens. Sacrifice them at the beginning of the next end step.)', 'precombat_main')
        self.call('phase', 'to', 'ending.end')
        self.assertFalse(self.read().get('pending'))

    def test_attack_shortcut_stops_before_declaring(self):
        self.text('At the beginning of combat on your turn, gain 1 life.', 'precombat_main')
        self.call('attack', '1', '--target', 'P1')
        self.assertEqual(self.read()['phase'], 'combat.begin')
        self.assertEqual(self.read()['combat']['attackers'], {})

    def test_opponents_turn_does_not_trigger_your_upkeep(self):
        self.text('At the beginning of your upkeep, gain 1 life.', 'ending.cleanup')
        self.call('turn', 'next', '--to', 'precombat_main')
        self.assertFalse(self.read().get('pending'))

    def test_multiple_sources_are_distinct_and_do_not_retrigger_on_reentry(self):
        self.text('At the beginning of each upkeep, gain 1 life.', 'beginning.untap')
        st = self.read()
        st['zones']['P2:library'].remove(2)
        st['zones']['P2:battlefield'].append(2)
        st['cards']['fixture2']['oracle'] = st['cards']['fixture1']['oracle']
        self.write(st)
        self.call('phase', 'next')
        self.assertEqual(len(self.read()['pending']), 2)
        for identity in ('T1', 'T2'):
            self.call('pending', 'cancel', identity, '--reason', '不成立')
        self.call('phase', 'set', 'beginning.upkeep')
        self.assertEqual(len(self.read()['pending']), 2)

    def test_reservation_survives_source_leaving_and_can_be_undone(self):
        self.text('', 'precombat_main')
        self.call('pending', 'schedule', 'トークンを生け贄', '--controller', 'P2', '--src', '1', '--at', 'ending.end')
        self.call('move', '1', 'graveyard')
        before = self.read()
        self.call('phase', 'to', 'ending.cleanup')
        self.assertEqual(self.read()['phase'], 'ending.end')
        self.assertEqual(self.read()['pending'][0]['status'], 'review')
        self.call('undo')
        self.assertEqual(self.read(), before)

    def test_schedule_during_end_step_waits_for_next_end_step(self):
        self.text('', 'ending.end')
        self.call('pending', 'schedule', '遅延', '--controller', 'P2', '--at', 'ending.end')
        self.call('turn', 'next', '--to', 'precombat_main')
        self.assertEqual(self.read()['pending'][0]['status'], 'scheduled')
        self.call('phase', 'to', 'ending.cleanup')
        self.assertEqual(self.read()['pending'][0]['status'], 'review')

    def test_saga_requires_action_confirmation(self):
        st = self.read()
        st['cards']['fixture1']['subtypes'] = ['Saga']
        self.write(st)
        self.call('turn', 'next', '--to', 'precombat_main', '--draw')
        self.assertTrue(self.read()['pending'][0]['turn_action'])
        self.call('counter', '1', 'lore', '1')
        self.call('pending', 'add', '第I章', '--controller', 'P2', '--src', '1')
        self.call('pending', 'confirm', 'T1', '--reason', '伝承を追加しT2に章能力を登録')
        self.assertEqual(self.read()['pending'][0]['status'], 'done')

    def test_batch_stops_at_checkpoint_even_with_keep_going(self):
        self.text('At the beginning of your upkeep, gain 1 life.')
        batch = self.path.with_suffix('.mtg')
        batch.write_text('turn next --to precombat_main --draw\nlife P2 -7\n', encoding='utf-8')
        self.call('run', str(batch), '--compact', '--keep-going')
        self.assertEqual(self.read()['players']['P2']['life'], 20)

    def test_scheduling_inside_resolution_is_atomic(self):
        self.text('', 'precombat_main')
        self.call('pending', 'add', '動員', '--controller', 'P2')
        self.call('pending', 'stack', 'T1')
        self.call('pending', 'resolve', 'T1', '--part', 'create', '--do',
                  'pending schedule "生け贄" --controller P2 --at ending.end')
        self.assertEqual(self.read()['pending'][1]['status'], 'scheduled')
        self.call('undo')
        self.assertEqual(len(self.read()['pending']), 1)
        self.assertEqual(self.read()['pending'][0]['status'], 'stack')


if __name__ == '__main__':
    unittest.main()
