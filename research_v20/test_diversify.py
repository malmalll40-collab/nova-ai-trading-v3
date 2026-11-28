import unittest
from diversify import select
class Tests(unittest.TestCase):
    def test_cooldown_boundary(self):
        self.assertEqual(select(['A','B','C'],{}, {'A':[10]},19,'cooldown10'),['B'])
        self.assertEqual(select(['A','B','C'],{}, {'A':[10]},20,'cooldown10'),['A','B'])
    def test_rolling_boundary(self):
        self.assertEqual(select(['A','B','C'],{}, {'A':[1,2]},20,'cap2_20'),['B'])
        self.assertEqual(select(['A','B','C'],{}, {'A':[1,2]},21,'cap2_20'),['A','B'])
    def test_diversify_at_signal(self):
        self.assertEqual(select(['A','B','C','D'],{'A':{}},{'B':[10,15]},20,'diversify10_cap2_20'),['C','D'])
    def test_baseline_unchanged(self):
        self.assertEqual(select(['A','B','C'],{'A':{}},{'A':[10,15]},20,'baseline'),['A','B'])
if __name__=='__main__':unittest.main(verbosity=2)
