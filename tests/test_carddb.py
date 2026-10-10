"""オラクルのキャッシュ（1枚ごと・デッキごと）と表示。"""
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import carddb, parse_decklist  # noqa: E402

DECK = """Deck
4 Grizzly Bears
20 Forest
Sideboard
1 Naturalize
"""
BEARS = {"name": "Grizzly Bears", "mana_cost": "{1}{G}", "type_line": "Creature — Bear",
         "power": "2", "toughness": "2", "oracle_text": ""}
FOREST = {"name": "Forest", "type_line": "Basic Land — Forest", "oracle_text": "({T}: Add {G}.)"}


class DeckCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("MTG_CARDS_DIR")
        os.environ["MTG_CARDS_DIR"] = self.tmp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("MTG_CARDS_DIR", None)
        else:
            os.environ["MTG_CARDS_DIR"] = self.old
        self.tmp.cleanup()

    def _put(self, rec):
        p = carddb._path(rec["name"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec), encoding="utf-8")

    def test_offline_build_reports_missing_then_completes(self):
        deck = parse_decklist(DECK, "green")
        data = carddb.build_deck_cache(deck, fetch=False)
        self.assertEqual(sorted(data["missing"]), ["Forest", "Grizzly Bears", "Naturalize"])
        self._put(BEARS)
        self._put(FOREST)
        self._put({"name": "Naturalize", "mana_cost": "{1}{G}", "type_line": "Instant",
                   "oracle_text": "Destroy target artifact or enchantment."})
        data = carddb.build_deck_cache(deck, fetch=False)
        self.assertEqual(data["missing"], [])
        self.assertEqual(carddb.load_deck_cache(deck)["cards"]["Grizzly Bears"]["power"], "2")
        sheet = carddb.format_deck(data)
        self.assertIn("4 <Grizzly Bears> {1}{G} — Creature — Bear 2/2", sheet)
        self.assertIn("## Sideboard (1)", sheet)
        self.assertNotIn("Naturalize", carddb.format_deck(data, sideboard=False))

    def test_hash_follows_decklist_contents(self):
        a = parse_decklist(DECK, "green")
        b = parse_decklist(DECK.replace("20 Forest", "19 Forest"), "green")
        self.assertNotEqual(carddb.deck_cache_path(a), carddb.deck_cache_path(b))
        self.assertEqual(carddb.deck_hash(a), carddb.deck_hash(parse_decklist(DECK, "green")))

    def test_brief_drops_reminder_text(self):
        self.assertEqual(carddb.format_card_compact(FOREST), "<Forest> — Basic Land — Forest\n    ({T}: Add {G}.)")
        self.assertEqual(carddb.format_card_compact(FOREST, brief=True), "<Forest> — Basic Land — Forest")


if __name__ == "__main__":
    unittest.main()


class JapaneseTest(unittest.TestCase):
    """画面の表示だけに使う日本語版（design/i18n_plan.md 段階 4）。卓と AI は英語のまま。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("MTG_CARDS_DIR")
        os.environ["MTG_CARDS_DIR"] = self.tmp.name
        self.fetched = []
        self.real_fetch_ja = carddb.fetch_ja
        carddb.fetch_ja = lambda name, retries=5: self.fetched.append(name) or self.prints.get(name, {"none": True})
        self.prints = {}

    def tearDown(self):
        carddb.fetch_ja = self.real_fetch_ja
        if self.old is None:
            os.environ.pop("MTG_CARDS_DIR", None)
        else:
            os.environ["MTG_CARDS_DIR"] = self.old
        self.tmp.cleanup()

    def test_merge_takes_each_field_from_the_newest_print_that_has_it(self):
        img = {"normal": "https://cards.scryfall.io/normal/front/x.jpg"}
        rec = carddb._merge_ja([
            {"printed_text": "新しい文", "image_status": "placeholder", "image_uris": img},  # 名前の無い新しい印刷
            {"printed_name": "縫（ぬ）い目（め）破（やぶ）り", "printed_type_line": "インスタント", "printed_text": "古い文",
             "image_status": "highres_scan", "image_uris": img}])
        self.assertEqual(rec, {"text": "新しい文", "name": "縫い目破り", "type_line": "インスタント", "image": img["normal"]})
        self.assertEqual(carddb._merge_ja([{"printed_text": "x"}]), {"none": True})  # 名前が無ければ日本語版は無い
        self.assertEqual(carddb._ruby_off("クリーチャー１体（それはタップ状態になる）"), "クリーチャー１体（それはタップ状態になる）")

    def test_names_and_text_for_a_card_and_its_faces(self):
        carddb._save("Ice", {"name": "Fire // Ice", "faces": [  # 片方の面の名前で引いた（正式な名前でも置かれる）
            {"name": "Fire", "mana_cost": "{1}{R}", "type_line": "Instant", "oracle_text": "Fire deals 2 damage."},
            {"name": "Ice", "mana_cost": "{1}{U}", "type_line": "Instant", "oracle_text": "Tap target permanent."}]})
        self.prints["Fire // Ice"] = carddb._merge_ja([{"card_faces": [
            {"printed_name": "火", "printed_type_line": "インスタント", "printed_text": "火は２点のダメージを与える。"},
            {"printed_name": "氷", "printed_type_line": "インスタント", "printed_text": "パーマネント１つを対象とし、それをタップする。"}]}])
        self.assertEqual(carddb.ja_name("Fire // Ice", offline=False), "火 // 氷")
        self.assertEqual(carddb.ja_name("Ice"), "氷")  # 片方の面の名前なら、その面
        self.assertEqual(carddb.format_card_ja("Ice"), "<氷>  {1}{U}\nインスタント\nパーマネント１つを対象とし、それをタップする。")
        self.assertEqual(self.fetched, ["Fire // Ice"])  # 引いた結果はキャッシュに残り、引き直さない

    def test_only_cards_in_the_english_cache_are_looked_up(self):
        self.assertIsNone(carddb.ja_name("Anything At All", offline=False))
        self.assertEqual(self.fetched, [])  # 画面から好きな名前で Scryfall に問い合わせさせない
        carddb._save("Forest", FOREST)
        self.assertIsNone(carddb.ja_name("Forest", offline=False))  # 日本語版が無い
        self.assertEqual(carddb.lookup_ja("Forest", offline=True), {"none": True})  # 無いことも残す
        self.assertIsNone(carddb.format_card_ja("Forest"))
