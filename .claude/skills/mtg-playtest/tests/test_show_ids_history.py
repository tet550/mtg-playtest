"""Compact identities preserve decisions; turn history is separate and readable."""
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
import cardcache


class ShowIdsHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / 'g01.json'
        self.cards = self.root / 'cards'
        self.archive = self.root / 'output/g01/turn-starts.md'
        self.call('init', '--seed', '8', '--history-hand', 'both')
        st = json.loads(self.state.read_text())
        for oid, name, owner, zone, types in (
            (1, 'Long Named Flying Creature', 'P1', 'battlefield', ['Creature']),
            (2, 'Long Named Equipment', 'P1', 'battlefield', ['Artifact']),
            (3, 'Private Hand Card', 'P2', 'hand', ['Creature']),
            (4, 'Undrawn Library Secret', 'P2', 'library', ['Creature']),
            (5, 'Graveyard Creature', 'P1', 'graveyard', ['Creature']),
            (6, 'Different Flying Creature', 'P1', 'battlefield', ['Creature']),
        ):
            rec = cardcache.blank_record(name)
            rec.update(types=types, unresolved=False, mana_cost='{2}{U}',
                       subtypes=['Equipment'] if oid == 2 else [],
                       power=None if oid == 2 else '2', toughness=None if oid == 2 else '3',
                       keywords=[] if oid == 2 else ['Flying'])
            cardcache.save(self.cards, rec)
            ob = mtg.new_object(oid, name, owner)
            st['objects'][str(oid)] = ob
            st['zones'][owner + ':' + zone].append(oid)
        st['objects']['1'].update(tapped=True, sick=True, damage=1, counters={'+1/+1': 1})
        st['objects']['2']['attached_to'] = 1
        st['next_oid'] = 7
        self.state.write_text(json.dumps(st))
        mtg._CTX['mem'] = {}

    def call(self, *args, seat=None):
        argv = ['--state', str(self.state), '--cards-dir', str(self.cards), '--offline']
        if seat:
            argv += ['--as', seat]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(argv + list(args))
        return out.getvalue()

    def test_ids_preserve_state_and_decision_fields(self):
        before = self.state.read_bytes()
        full = self.call('show', '--hand', 'both')
        ids = self.call('show', '--ids', '--hand', 'both')
        self.assertIn('Long Named Flying Creature', full)
        for name in ('Long Named Flying Creature', 'Long Named Equipment', 'Private Hand Card', 'Graveyard Creature'):
            self.assertNotIn(name, ids)
        for field in ('[1]3/4', 'dmg1', '+1/+1 x1', '(T)', '(酔)', 'Flying', '[2]→[1]', '[3]', '手札(1):', '墓地(1): [5]'):
            self.assertIn(field, ids)
        for field in ('{2}{U}', 'コスト', 'oid ／ タイプ', 'の手札'):
            self.assertNotIn(field, ids)
        self.assertNotIn('Undrawn Library Secret', ids + full)
        self.assertEqual(before, self.state.read_bytes())
        self.assertLess(len(ids), len(full))

    def test_stack_preserves_ability_targets_and_order(self):
        self.call('stack', 'push', '3', '--controller', 'P2', '--targets', '1')
        self.call('stack', 'push', 'Untap target creature', '--ability',
                  '--controller', 'P1', '--src', '2', '--targets', '1')
        out = self.call('show', '--ids')
        self.assertIn('Untap target creature', out)
        self.assertIn('対象=1', out)
        self.assertIn('src=2@', out)
        self.assertNotIn('Private Hand Card', out)

    def test_history_renderer_is_readonly_and_offline(self):
        st = json.loads(self.state.read_text())
        before = json.dumps(st, sort_keys=True)
        with patch.object(cardcache, 'fetch_scryfall') as fetch:
            view = mtg.human_turn_view(st)
        fetch.assert_not_called()
        self.assertIn('Private Hand Card', view)
        self.assertNotIn('Undrawn Library Secret', view)
        self.assertEqual(before, json.dumps(st, sort_keys=True))

    def test_different_cards_do_not_merge_when_names_are_hidden(self):
        st = json.loads(self.state.read_text())
        st['objects']['1'].update(tapped=False, sick=False, damage=0, counters={})
        self.state.write_text(json.dumps(st))
        out = self.call('show', '--ids')
        self.assertIn('[1]2/3', out)
        self.assertIn('[6]2/3', out)
        self.assertNotIn('[1,6]', out)

    def test_first_turn_and_next_turn_capture_before_draw_without_extra_output(self):
        self.assertFalse(self.archive.exists())
        out = self.call('phase', 'to', 'precombat_main')
        self.assertNotIn('Long Named Flying Creature', out)
        self.assertIn('Long Named Flying Creature', self.archive.read_text())
        out = self.call('turn', 'next', '--to', 'precombat_main', '--draw')
        history = self.archive.read_text()
        self.assertEqual(history.count('## ターン開始'), 2)
        self.assertIn('Turn 2 / P2 / beginning.untap', history)
        self.assertNotIn('Undrawn Library Secret', history)
        self.assertIn('Undrawn Library Secret', out)
        self.assertIn('P2 ドロー: [4]Undrawn Library Secret  （残り0枚）', out)
        self.assertNotIn('_turn_start_view', self.state.read_text())
        self.call('show', '--ids')
        self.assertEqual(history, self.archive.read_text())

    def test_public_default_and_seat_restriction(self):
        st = json.loads(self.state.read_text())
        st.pop('history_hand')  # old games and the default are public-only
        self.state.write_text(json.dumps(st))
        self.call('phase', 'next')
        self.assertNotIn('Private Hand Card', self.archive.read_text())
        with self.assertRaises(SystemExit):
            self.call('show', '--ids', '--hand', 'both', seat='P1')
        with self.assertRaises(SystemExit):
            self.call('init', '--history-hand', 'both', seat='P1')

    def test_history_does_not_leak_other_hand_under_seat(self):
        self.call('phase', 'next', seat='P1')
        self.assertNotIn('Private Hand Card', self.archive.read_text())

    def test_failed_progression_adds_no_history_and_undo_marks_correction(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.call('turn', 'next', '--draw')
        self.assertFalse(self.archive.exists())
        self.call('phase', 'to', 'precombat_main')
        self.call('turn', 'next')
        self.call('undo')
        history = self.archive.read_text()
        self.assertIn('巻き戻し', history)
        self.assertIn('旧記録は無効', history)
        self.call('turn', 'next')
        self.assertEqual(self.archive.read_text().count('## ターン開始'), 3)

    def test_implicit_turn_via_pass_is_recorded(self):
        self.call('phase', 'to', 'ending.cleanup')
        self.call('pass', 'P1')
        self.call('pass', 'P2')
        self.assertIn('Turn 2 / P2 / beginning.untap', self.archive.read_text())

    def test_compact_transcript_preserves_named_view_and_delta_mode_switch(self):
        batch = self.root / 'batch.mtg'
        batch.write_text('show --ids --hand P1\nshow --hand P1\n')
        out = self.call('run', str(batch), '--compact', '--delta')
        rows = [json.loads(line) for line in next((self.root / 'output/g01').glob('run-*.jsonl')).read_text().splitlines()]
        self.assertNotIn('Long Named Flying Creature', rows[0]['stdout'])
        self.assertIn('Long Named Flying Creature', rows[0]['named_stdout'])
        self.assertNotIn('Private Hand Card', rows[0]['named_stdout'])
        self.assertIn('=== Turn 1', out)
        self.assertEqual(len(rows), 2)


if __name__ == '__main__':
    unittest.main()
