"""A game must have usable card definitions before its state is saved."""
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import mtg
import cardcache


class InitCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.cards = self.root / 'cards'
        self.state = self.root / 'game.json'
        self.deck = {'name': 'fixture', 'legal': True, 'problems': [],
                     'main': [{'name': 'Forest', 'count': 60}],
                     'sideboard': [{'name': 'Island', 'count': 15}]}

    def cache(self, name='Forest', unresolved=False):
        rec = cardcache.blank_record(name)
        rec.update(types=[] if unresolved else ['Land'], unresolved=unresolved)
        cardcache.save(self.cards, rec)
        return rec

    def start(self, *flags):
        with patch.object(mtg.decks, 'resolve_source', return_value=self.deck), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            mtg.dispatch(['--state', str(self.state), '--cards-dir', str(self.cards),
                          *flags, 'init', '--deck1', 'fixture', '--deck2', 'fixture'])
        return output.getvalue()

    def test_cached_manual_card_needs_no_network_including_offline(self):
        self.cache()
        self.cache('Island')
        with patch.object(cardcache, 'fetch_scryfall') as fetch:
            self.assertIn('2種確認済み / 0種不足', self.start('--offline'))
        fetch.assert_not_called()
        self.assertTrue(self.state.exists())

    def test_main_and_sideboard_fetched_once_without_adding_sideboard_to_library(self):
        def fetch(name, *args, **kwargs):
            return self.cache(name), 'network'
        with patch.object(cardcache, 'get', side_effect=fetch) as get:
            self.start()
        self.assertEqual(get.call_args_list, [
            call(name, str(self.cards), refresh=True, offline=False)
            for name in ('Forest', 'Island')])
        state = json.loads(self.state.read_text())
        for pid in ('P1', 'P2'):
            self.assertEqual(len(state['zones'][pid + ':library']), 60)
        self.assertEqual({o['name'] for o in state['objects'].values()}, {'Forest'})
        self.assertTrue(self.state.exists())
        self.assertIsNotNone(cardcache.load(self.cards, 'Forest'))

    def test_unresolved_placeholder_is_retried(self):
        self.cache(unresolved=True)
        self.cache('Island')
        with patch.object(cardcache, 'get', side_effect=lambda *a, **k: (self.cache(), 'network')) as get:
            self.start()
        self.assertTrue(get.call_args.kwargs['refresh'])
        self.assertFalse(cardcache.load(self.cards, 'Forest')['unresolved'])

    def test_network_failure_preserves_existing_state(self):
        self.state.write_text('existing state')
        with patch.object(cardcache, 'get', return_value=(cardcache.blank_record('Forest'), 'offline')):
            with self.assertRaisesRegex(SystemExit, '状態未保存.*Forest'):
                self.start()
        self.assertEqual(self.state.read_text(), 'existing state')

    def test_offline_missing_sideboard_stops_without_network_or_state(self):
        self.cache('Forest')
        with patch.object(cardcache, 'fetch_scryfall') as fetch:
            with self.assertRaisesRegex(SystemExit, '状態未保存.*Island'):
                self.start('--offline')
        fetch.assert_not_called()
        self.assertFalse(self.state.exists())


if __name__ == '__main__':
    unittest.main()
