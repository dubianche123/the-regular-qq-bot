import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from voice_budget import Budget

class Tests(unittest.TestCase):
    def test_durable_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'budget.db'
            a=Budget(p,'provider',1).reserve(120,512)
            self.assertIsNotNone(a)
            self.assertIsNone(Budget(p,'provider',1).reserve(120,512))
            a.release()
            b=Budget(p,'provider',1).reserve(120,512)
            self.assertIsNotNone(b); b.commit()
            self.assertIsNone(Budget(p,'provider',1).reserve(120,512))
            self.assertIsNotNone(Budget(p,'delivery',1).reserve())
            self.assertEqual(p.stat().st_mode & 0o777,0o600)

    def test_atomic_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'budget.db'
            def take(_): return Budget(p,'provider',3).reserve()
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                acquired=list(pool.map(take, range(12)))
            self.assertLessEqual(sum(x is not None for x in acquired),3)
            self.assertGreater(sum(x is not None for x in acquired),0)
