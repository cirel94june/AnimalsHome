import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_chat_profile import prepare_chat_model_catalog


class ChatModelCatalogTests(unittest.TestCase):
    def test_newer_desktop_catalog_supplies_new_models_to_companion_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chat_home = root / 'chat'
            chat_home.mkdir()
            old = chat_home / 'models_cache.json'
            old.write_text(json.dumps({'models': [{'slug': 'gpt-6-sol'}]}), encoding='utf-8')
            desktop = root / 'desktop-models.json'
            desktop.write_text(json.dumps({'models': [{
                'slug': 'gpt-6.1-sol', 'tool_mode': 'code_mode_only',
                'input_modalities': ['text', 'image'],
            }]}), encoding='utf-8')
            os.utime(old, ns=(1_000_000_000, 1_000_000_000))
            os.utime(desktop, ns=(2_000_000_000, 2_000_000_000))
            target = prepare_chat_model_catalog(chat_home, 'node', 'unused', source_cache=desktop)
            model = json.loads(target.read_text(encoding='utf-8'))['models'][0]
            self.assertEqual(model['slug'], 'gpt-6.1-sol')
            self.assertEqual(model['input_modalities'], ['text', 'image'])
            self.assertEqual(model['tool_mode'], 'disabled')
            self.assertEqual(model['experimental_supported_tools'], [])

    def test_catalog_refreshes_when_its_source_changes_during_server_lifetime(self):
        with tempfile.TemporaryDirectory() as directory:
            chat_home = Path(directory)
            cache = chat_home / 'models_cache.json'
            cache.write_text(json.dumps({'models': [{'slug': 'gpt-6-sol'}]}), encoding='utf-8')
            os.utime(cache, ns=(1_000_000_000, 1_000_000_000))
            prepare_chat_model_catalog(chat_home, 'node', 'unused')
            cache.write_text(json.dumps({'models': [{'slug': 'gpt-6.1-sol'}]}), encoding='utf-8')
            os.utime(cache, ns=(2_000_000_000, 2_000_000_000))
            target = prepare_chat_model_catalog(chat_home, 'node', 'unused')
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['models'][0]['slug'], 'gpt-6.1-sol')


if __name__ == '__main__':
    unittest.main()
