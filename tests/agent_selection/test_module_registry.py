"""The package registry has to stay in step with the files on disk.

``discover -s tests`` hands the whole ``tests.agent_selection`` package to
``load_tests``, which only runs the modules named in ``_TEST_MODULES``. A test
module that exists on disk but is missing from that tuple therefore still runs
under ``discover -s tests/agent_selection`` and silently vanishes from the full
run; that is how ``test_model_identity`` was dropped after #170.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.agent_selection import _TEST_MODULES  # noqa: E402

PACKAGE_DIR = Path(__file__).resolve().parent


class ModuleRegistryTests(unittest.TestCase):
    def test_every_test_file_is_registered(self):
        on_disk = {path.stem for path in PACKAGE_DIR.glob('test_*.py')}
        unregistered = sorted(on_disk - set(_TEST_MODULES))
        self.assertEqual(unregistered, [], 'test modules missing from _TEST_MODULES')

    def test_model_identity_is_registered(self):
        self.assertIn('test_model_identity', _TEST_MODULES)


if __name__ == '__main__':
    unittest.main()
