"""Synthetic host test (fixture only)."""
import unittest

from src.app import main


class HostTest(unittest.TestCase):
    def test_main(self):
        self.assertEqual(main(), 'host')


if __name__ == '__main__':
    unittest.main()
