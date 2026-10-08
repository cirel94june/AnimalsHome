import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


class AssetTests(unittest.TestCase):
    def test_optional_pages_use_the_same_android_cache_routes(self):
        source = Path(__file__).resolve().parents[2] / 'asset_manifest.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ('static/playground.html', 'entertainment/lobby.html',
                             'entertainment/lobby.js', 'billiards/static/index.html',
                             'billiards/static/room.js', 'billiards/static/tests/rules.test.js'):
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(relative)
            config = ModuleType('config')
            config.BASE_DIR = root
            config.PUBLIC_DIR = root / 'public'
            spec = importlib.util.spec_from_file_location('pool_asset_manifest_test', source)
            module = importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules, {'config': config}):
                spec.loader.exec_module(module)
            disabled = {url for url, _, _ in module._iter_client_assets()}
            self.assertNotIn('/billiards', disabled)
            self.assertFalse(any(url.startswith('/billiards-assets/') for url in disabled))
            self.assertIn('/entertainment-assets/lobby.js', disabled)
            module.enable_billiards_assets()
            entries = {url: file for url, file, _ in module._iter_client_assets()}
            self.assertEqual(entries['/playground'], root / 'entertainment/lobby.html')
            self.assertEqual(entries['/playground/explore'], root / 'static/playground.html')
            self.assertIn('/billiards', entries)
            self.assertIn('/billiards-assets/room.js', entries)
            self.assertNotIn('/billiards-assets/tests/rules.test.js', entries)
            (root / 'entertainment/lobby.html').unlink()
            (root / 'billiards/static/index.html').unlink()
            entries = {url: file for url, file, _ in module._iter_client_assets()}
            self.assertEqual(entries['/playground'], root / 'static/playground.html')
            self.assertNotIn('/billiards', entries)
