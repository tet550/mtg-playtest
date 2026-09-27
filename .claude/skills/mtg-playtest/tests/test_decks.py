"""デッキ検証が未解決の情報で保存済みデータを壊さないこと。"""
import contextlib
import io
from unittest.mock import patch

from support import DECK, TableCase
import decks


class DeckVerifyTest(TableCase):
    def test_verify_does_not_write_unresolved_cards(self):
        deck = decks.build("green", DECK, str(self.cards), offline=True)
        path = decks.save(str(self.root / "decks"), deck)
        before = path.read_text(encoding="utf-8")
        for extra in ([], ["--write"]):
            with self.subTest(extra=extra):
                argv = ["decks.py", "--dir", str(self.root / "decks"), "--cards-dir",
                        str(self.root / "empty-cards"), "--offline", "verify", "green"] + extra
                with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()):
                    decks.main()
                self.assertEqual(before, path.read_text(encoding="utf-8"))
                if extra:
                    self.assertIn("書き込みません", out.getvalue())
