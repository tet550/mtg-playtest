"""補助表示の内容、席の制限、読み取り専用の契約。"""
from support import DECK, TableCase


class AidTest(TableCase):
    def test_aid_does_not_change_state(self):
        self.start()
        w = self.find("Watcher", "P1:library")
        bear = self.find("Grizzly Bears", "P1:library")
        self.cli("run", "-", stdin="move %d battlefield\nmove %d battlefield\ndamage %d +2\n" % (w, bear, bear))
        before, hist = self.state.read_text(encoding="utf-8"), self.history()
        trig = self.cli("aid", "triggers", "--phase", "beginning.upkeep")
        self.assertIn("upkeep", trig)
        self.assertNotIn("Whenever another creature", trig)
        self.assertIn("Whenever another creature", self.cli("aid", "triggers"))
        self.assertIn("ダメージがタフネス以上", self.cli("aid", "creatures"))
        self.assertIn("タフネスを超える", self.cli("aid", "check"))
        self.assertEqual(before, self.state.read_text(encoding="utf-8"))
        self.assertEqual(hist, self.history())

    def test_mana_sources_and_pregame(self):
        self.deck.write_text(DECK.replace("4 Pacifism", "4 Leyline of Testing"), encoding="utf-8")
        self.start()
        forest = self.find("Forest", "P1:library")
        other = [o for o in self.st()["zones"]["P1:library"] if self.st()["objects"][str(o)]["card"] == "Forest"][1]
        self.cli("run", "-", stdin="move %d %d battlefield\ntap %d\n" % (forest, other, other))
        out = self.cli("aid", "mana")
        self.assertIn("P1: アンタップのマナ源 1", out)
        self.assertIn("[%d] Forest" % forest, out)
        self.assertNotIn("[%d] Forest" % other, out)
        leyline = self.find("Leyline of Testing", "P1:library")
        self.cli("move", str(leyline), "hand")
        self.assertIn("opening hand", self.cli("aid", "pregame"))
        self.assertNotIn("Leyline", self.cli("--as", "P2", "aid", "pregame"))
