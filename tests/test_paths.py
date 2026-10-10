"""置き場所（paths.py）: リポジトリから動く時は今までの場所、MTGTABLE_HOME・MTG_CARDS_DIR の優先、パッケージとして入れた時。"""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from mtgtable import carddb, lobby, paths, prompt  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        for k in ("MTGTABLE_HOME", "MTG_CARDS_DIR"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_checkout_keeps_the_repo_layout(self):
        self.assertEqual(paths.checkout(), REPO)
        self.assertEqual(paths.data_home(), REPO)
        self.assertEqual(paths.cards_dir(), REPO / "cards")
        self.assertEqual(paths.secrets_dir(), REPO / "secrets")
        self.assertEqual(paths.games_dir(), REPO / "playtest")
        self.assertEqual(paths.decklists_dir(), REPO / "decklists")
        self.assertEqual(paths.references_dir(), REPO / ".claude" / "skills" / "mtg-playtest" / "references")

    def test_home_and_cards_dir_from_the_environment(self):
        os.environ["MTGTABLE_HOME"] = self.tmp.name
        home = pathlib.Path(self.tmp.name)
        self.assertEqual(paths.data_home(), home)
        self.assertEqual(paths.cards_dir(), home / "cards")
        self.assertEqual(carddb.cache_dir(), home / "cards")
        self.assertEqual(paths.secrets_dir(), home / "secrets")
        self.assertEqual(paths.games_dir(), home / "playtest")
        self.assertEqual(paths.decklists_dir(), REPO / "decklists")  # 読むだけのものは MTGTABLE_HOME に依らない
        os.environ["MTG_CARDS_DIR"] = str(home / "elsewhere")
        self.assertEqual(paths.cards_dir(), home / "elsewhere")

    def test_installed_package_uses_the_os_data_dir_and_bundled_assets(self):
        with mock.patch.object(paths, "checkout", return_value=None), \
                mock.patch.object(pathlib.Path, "home", return_value=pathlib.Path(self.tmp.name)):
            home = paths.data_home()
            self.assertTrue(str(home).startswith(self.tmp.name), home)
            self.assertEqual(home.name, "mtgtable")
            self.assertEqual(paths.cards_dir(), home / "cards")
            self.assertEqual(paths.decklists_dir(), paths.ASSETS / "decklists")
            self.assertEqual(paths.references_dir(), paths.ASSETS / "references")

    def test_os_data_dir_per_platform(self):
        home = pathlib.Path(self.tmp.name)
        with mock.patch.object(pathlib.Path, "home", return_value=home):
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(paths._os_data_dir(), home / "Library" / "Application Support" / "mtgtable")
            with mock.patch.object(sys, "platform", "win32"):
                os.environ["APPDATA"] = str(home / "Roaming")
                self.assertEqual(paths._os_data_dir(), home / "Roaming" / "mtgtable")
            with mock.patch.object(sys, "platform", "linux"):
                os.environ.pop("XDG_DATA_HOME", None)
                self.assertEqual(paths._os_data_dir(), home / ".local" / "share" / "mtgtable")
                os.environ["XDG_DATA_HOME"] = str(home / "xdg")
                self.assertEqual(paths._os_data_dir(), home / "xdg" / "mtgtable")

    def test_read_only_files_the_code_needs_are_there(self):
        """ビルドで assets/ に写すもの（段階 2）が、リポジトリの今の場所にそろっている。"""
        for name in ("cli.md", "rules.md", "patterns.md"):
            self.assertTrue((prompt.REFERENCES / name).is_file(), name)
        self.assertTrue(lobby.repo_decks())
        self.assertTrue(prompt.STRATEGY.is_dir())


if __name__ == "__main__":
    unittest.main()
