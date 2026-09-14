"""Regressions from the Jund playtest: shortcuts keep evidence and rule boundaries."""
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
import check_report
import report_draft


class FollowupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        self.state = self.root / 'g01.json'
        self.cards = self.root / 'cards'
        self.call('init', '--seed', '19')
        self.addCleanup(mtg._CTX['mem'].clear)
        mtg._CTX['mem'].clear()

    def call(self, *args, text='', seat=None):
        argv = ['--state', str(self.state), '--cards-dir', str(self.cards), '--offline']
        if seat:
            argv += ['--as', seat]
        with patch('sys.stdin', io.StringIO(text)), contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(argv + list(args))
        return out.getvalue()

    def read(self):
        return json.loads(self.state.read_text())

    def batch(self, body, **kw):
        return self.call('run', '-', '--compact', text=body, **kw)

    def rows(self):
        return [json.loads(line) for path in (self.root/'output/g01').glob('run-*.jsonl')
                for line in path.read_text().splitlines()]

    def creature(self, oracle, keywords=()):
        self.call('token', 'P1', 'Runner', '--types', 'Creature', '--power', '2', '--toughness', '2', '--oracle', oracle)
        st = self.read()
        st['cards']['#1']['keywords'] = list(keywords)
        self.state.write_text(json.dumps(st))
        self.call('phase', 'to', 'combat.attackers')

    def test_native_haste_keyword_lines_and_granted_haste(self):
        self.creature('Flying, haste\nWhen this creature enters, create a Treasure token.', ['Flying', 'Haste'])
        self.assertNotIn('召喚酔い', self.call('attack', '1', '--target', 'P2'))
        self.call('undo', '1')
        self.call('card', 'set', '--oid', '1', '--oracle', '速攻（これは出たターンに攻撃できる。）')
        self.assertNotIn('召喚酔い', self.call('attack', '1', '--target', 'P2'))
        self.call('undo', '1')
        self.call('card', 'set', '--oid', '1', '--oracle', 'No keyword line.')
        self.call('grant', '1', 'Haste', '--until', 'eot')
        self.assertNotIn('召喚酔い', self.call('attack', '1', '--target', 'P2'))

    def test_vigilance_is_per_attacker_and_undo_restores_declaration(self):
        self.creature('Flying, vigilance\nWhen this creature enters, create a Treasure token.',
                      ['Flying', 'Vigilance', 'Treasure'])
        self.call('token', 'P1', 'Ordinary', '--types', 'Creature', '--power', '2', '--toughness', '2')
        before = self.read()
        self.call('attack', '1', '2', '--target', 'P2')
        state = self.read()
        self.assertFalse(state['objects']['1']['tapped'])
        self.assertTrue(state['objects']['2']['tapped'])
        self.assertEqual(state['combat']['attackers'], {'1': 'P2', '2': 'P2'})
        self.call('undo')
        self.assertEqual(self.read(), before)

    def test_japanese_and_granted_vigilance_and_expiry(self):
        self.creature('警戒（攻撃してもタップしない。）')
        self.call('attack', '1')
        self.assertFalse(self.read()['objects']['1']['tapped'])
        self.call('undo')
        self.call('card', 'set', '--oid', '1', '--oracle', 'No keyword line.')
        self.call('grant', '1', 'Vigilance', '--until', 'eot')
        self.call('attack', '1')
        self.assertFalse(self.read()['objects']['1']['tapped'])
        self.call('undo')
        self.call('turn', 'next')
        self.assertFalse(mtg.has_unconditional_keyword(self.read(), 1, 'vigilance', '警戒'))

    def test_conditional_or_other_creature_vigilance_requires_override(self):
        self.creature('This creature has vigilance as long as you control a Mountain.', ['Vigilance'])
        for oracle in ('This creature has vigilance as long as you control a Mountain.',
                       'Other creatures you control have vigilance.',
                       '{2}: Target creature gains vigilance until end of turn.'):
            with self.subTest(oracle=oracle):
                self.call('card', 'set', '--oid', '1', '--oracle', oracle)
                self.call('attack', '1')
                self.assertTrue(self.read()['objects']['1']['tapped'])
                self.call('undo')
                self.call('attack', '1', '--no-tap')
                self.assertFalse(self.read()['objects']['1']['tapped'])
                self.call('undo')

    def test_ids_only_show_native_or_explicitly_granted_abilities(self):
        self.creature('Flying, vigilance\nCreate a Treasure token.\n'
                      'You may cast artifact spells as though they had flash.\nTarget player mills three cards.',
                      ['Flying', 'Vigilance', 'Treasure', 'Flash', 'Mill'])
        self.call('grant', '1', 'Haste', '--until', 'eot')
        before = self.state.read_bytes()
        out = self.call('show', '--ids')
        self.assertIn('Flying・vigilance', out)
        self.assertIn('+Haste', out)
        for absent in ('Treasure', 'Flash', 'Mill', '+Flying', '+vigilance'):
            self.assertNotIn(absent, out)
        self.assertEqual(self.state.read_bytes(), before)
        self.call('card', 'set', '--oid', '1', '--oracle', 'Flash')
        self.assertIn('Flash', self.call('show', '--ids'))
        self.call('card', 'set', '--oid', '1', '--oracle', '')
        out = self.call('show', '--ids')
        self.assertIn('Vigilance', out)
        self.assertNotIn('Treasure', out)
        self.assertNotIn('Mill', out)

    def test_cancelled_pending_guidance_and_labels_avoid_reused_ids(self):
        self.call('pending', 'add', 'Old trigger', '--controller', 'P1')
        self.call('pending', 'cancel', 'T1', '--reason', 'Incorrect registration')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(SystemExit, '--label trigger'):
            self.call('pending', 'stack', 'T1')
        self.assertEqual(self.state.read_bytes(), before)
        self.batch('pending add "New trigger" --controller P1 --label trigger\n'
                   'pending stack $trigger\n')
        state = self.read()
        self.assertEqual([p['status'] for p in state['pending']], ['cancelled', 'stack'])
        self.assertEqual(state['objects'][str(state['zones']['stack'][-1])]['pending_id'], 'T2')

    def test_mentions_and_conditional_haste_do_not_bypass_warning(self):
        self.creature('This creature has haste as long as you control a Mountain.', ['Haste'])
        for oracle in ('This creature has haste as long as you control a Mountain.',
                       'Other creatures you control have flying, haste.',
                       '{2}: Target creature gains haste until end of turn.',
                       '山をコントロールしているかぎり、これは速攻を持つ。'):
            self.call('card', 'set', '--oid', '1', '--oracle', oracle)
            self.assertIn('召喚酔い', self.call('attack', '1', '--target', 'P2'))
            self.call('undo', '1')

    def test_labels_resolve_actual_ids_inside_inline_effects_and_keep_undo(self):
        st = self.read()
        st['next_oid'] = 250
        self.state.write_text(json.dumps(st))
        self.batch('''pending add "Clue ETB" --controller P1 --label etb
pending stack $etb
pass-both P1
pending resolve $etb --do "token P1 --preset clue" --part create --label clue
pending add "Tap clue" --controller P1 --label tap_it
pending stack $tap_it --targets $clue
pass-both P1
pending resolve $tap_it --do "tap $clue" --part tap
show --ids --packed --hand both
''')
        self.assertTrue(self.read()['objects']['251']['tapped'])
        rows = self.rows()
        self.assertTrue(any(r.get('binding') == {'clue': '251'} for r in rows))
        self.assertTrue(any('tap 251' in r.get('expanded_command', '') for r in rows))
        self.assertEqual(len([r for r in rows if r['command'].startswith('pass ')]), 4)
        self.call('undo', '1')
        self.assertFalse(self.read()['objects']['251']['tapped'])
        self.assertEqual(self.read()['pending'][-1]['status'], 'stack')

    def test_label_syntax_errors_leave_state_and_input_files_untouched(self):
        before = self.state.read_bytes()
        for body in (
            'life P1 -1\ntap $unknown\n',
            'token P1 --preset clue --label c\ntoken P1 --preset clue --label c\n',
            'token P1 --preset clue -n 2 --label c\n',
            'token P1 --preset clue --label c\nundo\n',
            'life P1 -1\npending resolve T1 --part tap --do "tap $future"\n',
            'token P1 --preset clue --label c\ninit\n',
        ):
            with self.subTest(body=body), self.assertRaises(SystemExit):
                self.batch(body)
            self.assertEqual(self.state.read_bytes(), before)
            self.assertFalse(list(self.root.glob('*.mtg')))

    def test_alias_scope_does_not_escape_batch(self):
        self.batch('token P1 --preset clue --label clue\n')
        before = self.state.read_bytes()
        with self.assertRaises(SystemExit):
            self.batch('tap $clue\n')
        self.assertEqual(self.state.read_bytes(), before)

    def test_failed_labelled_resolution_does_not_save_tokens_or_effects(self):
        self.batch('pending add "Clues" --controller P1\npending stack T1\n')
        before = self.state.read_bytes()
        with self.assertRaises(SystemExit):
            self.batch('pending resolve T1 --do "token P1 --preset clue -n 2" --part create --label clue\n')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertTrue(any('error' in r for r in self.rows()))
        self.assertFalse(any('トークン生成' in r['stdout'] for r in self.rows()))

    def test_keep_going_records_failed_binding_dependencies(self):
        self.call('run', '-', '--compact', '--keep-going', text='''pending add "Clues" --controller P1
pending stack T1
pending resolve T1 --do "token P1 --preset clue -n 2" --part create --label clue
tap $clue
''')
        failed = [r for r in self.rows() if 'error' in r]
        self.assertEqual(len(failed), 2)
        self.assertIn('未定義', failed[-1]['error'])
        self.assertFalse(self.read()['zones']['P1:battlefield'])

    def test_failed_operation_stops_before_dependent_actions(self):
        with self.assertRaises(SystemExit):
            self.batch('token P1 --preset clue --label clue\nmove 999 hand\ntap $clue\n')
        self.assertFalse(self.read()['objects']['1']['tapped'])
        self.assertEqual(len(self.rows()), 2)

    def test_presets_are_artifacts_and_undo_as_single_operations(self):
        rec = cardcache.blank_record('Clue')
        rec.update(unresolved=False, types=['Creature'], power='9', toughness='9',
                   keywords=['Haste'], colors=['R'], mana_cost='{R}')
        cardcache.save(self.cards, rec)
        for preset in ('clue', 'treasure', 'lander'):
            self.call('token', 'P1', '--preset', preset)
        st = self.read()
        for i, name in enumerate(('Clue', 'Treasure', 'Lander'), 1):
            card = st['cards'][f'#{i}']
            self.assertEqual(card['types'], ['Artifact'])
            self.assertEqual(card['subtypes'], [name])
            self.assertIn('Sacrifice', card['oracle'])
            self.assertIsNone(card['power'])
            self.assertEqual(card['colors'], [])
            self.assertEqual(card['keywords'], [])
        self.call('undo', '1')
        self.assertEqual(len(self.read()['zones']['P1:battlefield']), 2)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.call('token', 'P1', 'Custom', '--preset', 'clue')

    def test_multiple_card_show_is_readonly_and_offline(self):
        for name in ('Alpha', 'Beta'):
            rec = cardcache.blank_record(name)
            rec.update(unresolved=False, oracle_text_en=f'{name} full text.', types=['Artifact'])
            cardcache.save(self.cards, rec)
        before = self.state.read_bytes()
        with patch.object(cardcache, 'fetch_scryfall') as fetch:
            out = self.call('card', 'show', 'Alpha', 'Beta', 'Missing')
        self.assertIn('Alpha full text.', out)
        self.assertIn('Beta full text.', out)
        self.assertIn('[missing]', out)
        fetch.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.call('card', 'set', 'Alpha', 'Beta')

    def test_packed_hand_preserves_fields_and_seat_boundary(self):
        for i in range(1, 5):
            self.call('token', 'P2', f'Hand{i}', '--types', 'Artifact/Creature', '--power', '2', '--toughness', '3')
            self.call('move', str(i), 'hand')
        regular = self.call('show', '--ids', '--hand', 'P2')
        packed = self.call('show', '--ids', '--packed', '--hand', 'P2')
        self.assertLess(len(packed), len(regular))
        for oid in range(1, 5):
            self.assertIn(f'[{oid}]', packed)
        self.assertNotIn('アーティファクト/クリーチャー', packed)
        self.assertNotIn('コスト', packed)
        with self.assertRaisesRegex(SystemExit, '隠匿情報'):
            self.call('show', '--packed', '--hand', 'P2', seat='P1')
        self.assertNotIn('Hand1', self.call('show', '--packed', seat='P1'))

    def test_event_references_and_draft_pass_report_checker(self):
        self.batch('life P2 -20\nnote "ライフ0で決着" --event E01\nshow\n')
        self.call('end', '--winner', 'P1', '--reason', 'life 0')
        event = next(r['event'] for r in self.rows() if r.get('event'))
        self.assertEqual((event['start'], event['end']), (1, 2))
        report = self.root/'report-draft.md'
        report.write_text(report_draft.draft(self.root, 'g01', 19))
        self.assertEqual(check_report.check(report), [])
        self.assertIn('草稿・ルール確認待ち', report.read_text())
        before = self.state.read_bytes()
        with self.assertRaises(SystemExit):
            self.batch('note "duplicate" --event E01\n')
        self.assertEqual(self.state.read_bytes(), before)

    def test_events_require_public_compact_batch(self):
        before = self.state.read_bytes()
        with self.assertRaises(SystemExit):
            self.call('note', 'event', '--event', 'E01')
        with self.assertRaises(SystemExit):
            self.call('run', '-', text='note "event" --event E01\n')
        with self.assertRaises(SystemExit):
            self.batch('note "event" --event E01\n', seat='P1')
        self.assertEqual(self.state.read_bytes(), before)
        self.batch('pending add "effect" --controller P1\npending stack T1\n')
        before = self.state.read_bytes()
        with self.assertRaisesRegex(SystemExit, '直下'):
            self.call('pending', 'resolve', 'T1', '--do', 'note event --event E01', '--part', 'invalid')
        self.assertEqual(self.state.read_bytes(), before)

    def test_draft_refuses_undo_and_does_not_infer_old_notes(self):
        self.batch('life P2 -20\nnote "E01 old note"\n')
        self.call('end', '--winner', 'P1')
        with self.assertRaisesRegex(ValueError, 'イベントがありません'):
            report_draft.draft(self.root, 'g01', 19)
        self.batch('note "event" --event E01\nundo\n')
        with self.assertRaisesRegex(ValueError, 'undo/init'):
            report_draft.draft(self.root, 'g01', 19)

    def test_draft_refuses_stale_events_after_direct_undo(self):
        self.batch('life P2 -20\nnote "event" --event E01\n')
        self.call('undo', '1')
        self.call('end', '--winner', 'P1')
        with self.assertRaisesRegex(ValueError, '現在のログにない'):
            report_draft.draft(self.root, 'g01', 19)

    def test_draft_never_overwrites_existing_report(self):
        self.batch('life P2 -20\nnote "event" --event E01\n')
        self.call('end', '--winner', 'P1')
        output = self.root/'report-draft.md'
        argv = ['report_draft.py', str(self.root), '--seed', '19']
        with patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()):
            report_draft.main()
        before = output.read_bytes()
        with patch('sys.argv', argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            report_draft.main()
        self.assertEqual(output.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
