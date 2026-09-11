import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg


class CombatDamageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / 'game.json'
        self.call('init', '--seed', '1', '--first', 'P1')

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mtg.dispatch(['--state', str(self.path), '--offline', *args])
        return out.getvalue()

    def state(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def setup_combat(self, attack='Deathtouch Trample', defense='', power='5', back='0'):
        self.call('token', 'P1', 'Attacker', '--types', 'Creature', '--power', power,
                  '--toughness', '5', '--oracle', attack)
        self.call('token', 'P2', 'Blocker', '--types', 'Creature', '--power', back,
                  '--toughness', '2', '--oracle', defense)
        self.call('phase', 'to', 'combat.attackers')
        self.call('attack', '1', '--target', 'P2')
        self.call('phase', 'to', 'combat.blockers')
        self.call('block', '2', '1')
        self.call('phase', 'to', 'combat.damage')

    def damage(self, *args):
        return self.call('combat', 'damage', '--trample', *args)

    def test_protection_prevents_touch_but_not_trample_assignment(self):
        self.setup_combat(defense='Protection from green')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(SystemExit, 'プロテクション'):
            self.damage()
        self.assertEqual(before, self.path.read_bytes())
        self.damage('--prevent', '1:2')
        st = self.state()
        self.assertEqual(st['players']['P2']['life'], 16)
        self.assertEqual(st['objects']['2']['damage'], 0)
        self.assertFalse(st['objects']['2'].get('deathtouch_damage'))
        self.call('sba', '--apply-deaths')
        self.assertIn(2, self.state()['zones']['P2:battlefield'])

    def test_protection_without_touch_requires_full_toughness(self):
        self.setup_combat(attack='Trample', defense='プロテクション（緑）')
        self.damage('--prevent', '1:2')
        self.assertEqual(self.state()['players']['P2']['life'], 17)

    def test_unprevented_touch_destroys_and_undo_restores(self):
        self.setup_combat(defense='Protection from red')
        self.damage('--unprevented', '1:2')
        st = self.state()
        self.assertEqual(st['objects']['2']['damage'], 1)
        self.assertTrue(st['objects']['2']['deathtouch_damage'])
        self.call('sba', '--apply-deaths')
        self.assertNotIn(2, self.state()['zones']['P2:battlefield'])
        self.call('undo', '1')
        self.assertTrue(self.state()['objects']['2']['deathtouch_damage'])
        self.call('undo', '1')
        self.assertFalse(self.state()['objects']['2'].get('deathtouch_damage'))
        self.assertEqual(self.state()['players']['P2']['life'], 20)

    def test_blocker_touch_is_applied_simultaneously(self):
        self.setup_combat(attack='', defense='接死', back='1')
        self.damage()
        self.assertTrue(self.state()['objects']['1']['deathtouch_damage'])
        self.call('sba', '--apply-deaths')
        self.assertEqual(self.state()['zones']['P1:battlefield'], [])
        self.assertEqual(self.state()['zones']['P2:battlefield'], [])

    def test_indestructible_consumes_touch_at_sba(self):
        self.setup_combat(defense='Indestructible')
        self.damage()
        self.call('sba', '--apply-deaths')
        st = self.state()
        self.assertIn(2, st['zones']['P2:battlefield'])
        self.assertFalse(st['objects']['2'].get('deathtouch_damage'))
        self.call('card', 'set', '--oid', '2', '--oracle', '')
        self.call('sba', '--apply-deaths')
        self.assertIn(2, self.state()['zones']['P2:battlefield'])

    def test_invalid_decisions_and_missing_trample_leave_state_unchanged(self):
        self.setup_combat()
        before = self.path.read_bytes()
        for options in [('--prevent', '1:2', '--unprevented', '1:2'),
                        ('--prevent', '2:P1'), ('--prevent', 'bad')]:
            with self.subTest(options=options), self.assertRaises(SystemExit):
                self.damage(*options)
            self.assertEqual(before, self.path.read_bytes())
        with self.assertRaisesRegex(SystemExit, '未適用'):
            self.call('combat', 'damage')
        self.assertEqual(before, self.path.read_bytes())

    def test_zero_power_never_marks_touch_or_gains_life(self):
        self.setup_combat(power='-1')
        self.damage()
        st = self.state()
        self.assertEqual(st['players']['P2']['life'], 20)
        self.assertEqual(st['objects']['2']['damage'], 0)
        self.assertFalse(st['objects']['2'].get('deathtouch_damage'))

    def test_multiple_blockers_each_need_one_touch_damage(self):
        self.setup_combat()
        self.call('token', 'P2', 'Second', '--types', 'Creature', '--power', '0', '--toughness', '8')
        self.call('block', '3', '1')
        self.damage()
        st = self.state()
        self.assertEqual(st['players']['P2']['life'], 17)
        self.assertEqual([st['objects'][i]['damage'] for i in ('2', '3')], [1, 1])

    def test_nontrample_assigns_remaining_to_last_blocker(self):
        self.setup_combat(attack='Deathtouch')
        self.call('token', 'P2', 'Second', '--types', 'Creature', '--power', '0', '--toughness', '8')
        self.call('block', '3', '1')
        self.damage()
        st = self.state()
        self.assertEqual(st['players']['P2']['life'], 20)
        self.assertEqual([st['objects'][i]['damage'] for i in ('2', '3')], [1, 4])

    def test_cleanup_clears_touch(self):
        self.setup_combat()
        self.damage()
        st = self.state()
        mtg.relations.cleanup(st)
        self.assertFalse(st['objects']['2'].get('deathtouch_damage'))

    def test_zone_change_clears_touch(self):
        self.setup_combat()
        self.damage()
        self.call('move', '2', 'hand')
        self.assertFalse(self.state()['objects']['2'].get('deathtouch_damage'))

    def test_prevention_can_protect_player(self):
        self.setup_combat()
        self.damage('--prevent', '1:P2')
        self.assertEqual(self.state()['players']['P2']['life'], 20)

    def test_indestructible_does_not_save_zero_toughness(self):
        self.setup_combat(defense='Indestructible')
        self.call('card', 'set', '--oid', '2', '--toughness', '0')
        self.call('sba', '--apply-deaths')
        self.assertNotIn(2, self.state()['zones']['P2:battlefield'])

    def test_protection_on_attacker_requires_reverse_pair(self):
        self.setup_combat(attack='Protection from black', defense='Deathtouch', back='1')
        self.damage('--prevent', '2:1')
        self.assertEqual(self.state()['objects']['1']['damage'], 0)
        self.assertFalse(self.state()['objects']['1'].get('deathtouch_damage'))

    def test_damage_to_planeswalker_changes_loyalty(self):
        self.setup_combat()
        self.call('combat', 'clear')
        self.call('token', 'P2', 'Walker', '--types', 'Planeswalker')
        self.call('counter', '3', 'loyalty', '7', '--set')
        self.call('phase', 'set', 'combat.attackers')
        self.call('attack', '1', '--target', '3')
        self.call('phase', 'to', 'combat.damage')
        self.damage()
        self.assertEqual(self.state()['objects']['3']['counters']['loyalty'], 2)

    def test_missing_later_stats_does_not_apply_earlier_packet(self):
        self.setup_combat()
        st = self.state()
        st['cards'][st['objects']['2']['card_key']].pop('toughness')
        before = json.loads(json.dumps(st))
        with self.assertRaisesRegex(SystemExit, 'P/T未登録'):
            mtg.combat_damage.plan(mtg, st, mtg.pairs(st), 'all', True)
        self.assertEqual(st, before)


if __name__ == '__main__':
    unittest.main()
