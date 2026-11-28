import unittest
from research_v30.pde_generator import generate,freeze,BASE,CASH

class GeneratorTests(unittest.TestCase):
    def test_unique_search(self):
        self.assertEqual(len(generate()),48)
        self.assertEqual(len(set(generate())),48)
    def test_reject_low_coverage(self):
        self.assertEqual(freeze([(BASE,dict(trades=11,unknown=0,terminal_return_pct=20))],0),(CASH,0))
        self.assertEqual(freeze([(BASE,dict(trades=20,unknown=2,terminal_return_pct=20))],0),(CASH,0))
    def test_strict_improvement(self):
        self.assertEqual(freeze([(BASE,dict(trades=20,unknown=1,terminal_return_pct=2))],0),(BASE,1))
        self.assertEqual(freeze([(BASE,dict(trades=20,unknown=0,terminal_return_pct=2))],1),(CASH,0))
