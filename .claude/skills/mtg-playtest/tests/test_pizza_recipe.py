"""pizza_recipe.py の生成物が対人モードで安全かを固定する。

対人モードでは相手の手札が平文で出た時点でテストプレイの前提が壊れる。
生成器が吐く1行がそれをやってしまう事故を実際に踏んだので、回帰を張る。
"""
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts'))
import pizza_recipe


KW = dict(player='P1', mona=7, chrome=8, pizza=2, power=7, next_oid=126,
          count=3, proof='verified: 1 cycle by hand, opponent cannot respond')


class RecipeTests(unittest.TestCase):
    def test_no_hidden_hand_in_output(self):
        text = pizza_recipe.generate(**KW)
        self.assertNotIn('--hand', text)
        self.assertNotIn('hand P1', text)
        self.assertNotIn('hand P2', text)
        self.assertEqual(text.strip().splitlines()[-1], 'show')

    def test_token_oids_are_consecutive_with_four_objects_per_cycle(self):
        # 1周で stack push 3回 + token 1回 = 4 oid を消費する。ずれると
        # grant/tap/move が別のオブジェクトに当たって静かに盤面が壊れる。
        text = pizza_recipe.generate(**KW)
        granted = [int(m) for m in re.findall(r'^grant (\d+) Haste', text, re.M)]
        self.assertEqual(granted, [127, 131, 135])
        for oid in granted:
            self.assertIn('tap %d' % oid, text)
            self.assertIn('move %d graveyard' % oid, text)

    def test_stdout_is_utf8(self):
        # cp932 のままだと生成サマリが化けて周回数・収支が読めなくなる。
        self.assertEqual((sys.stdout.encoding or '').lower().replace('-', ''),
                         'utf8')

    def test_rejects_unverified_or_reused_oid(self):
        for bad in (dict(proof='  '), dict(next_oid=8), dict(power=5),
                    dict(count=0)):
            with self.assertRaises(ValueError):
                pizza_recipe.generate(**{**KW, **bad})


if __name__ == '__main__':
    unittest.main()
