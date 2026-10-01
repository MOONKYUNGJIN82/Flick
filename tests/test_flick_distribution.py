import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.flick.distribution import is_app_store_build


class DistributionTests(unittest.TestCase):
    def test_app_store_mode_requires_bundle_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('apps.flick.distribution.sys._MEIPASS', folder, create=True):
                self.assertFalse(is_app_store_build())
                (Path(folder) / 'flick-app-store.txt').touch()
                self.assertTrue(is_app_store_build())
