"""Codex §109 M1：主页「旧排法换成新默认」那一次保存晚到时，不能盖掉别处已经存好的文件夹排法。"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from routes import settings as settings_routes

HOME = Path(__file__).resolve().parent / "static" / "home.html"
OLD = {"theater": 0, "album": 1}
NEW = {"folder-later": 0, "folder-play": 1}


class HomeLayoutMigrationTests(unittest.TestCase):
    def put(self, store, positions, migration):
        body = settings_routes.HomeLayoutUpdate(version=2, positions=positions, migration=migration)
        with patch.object(settings_routes, "SETTINGS", store), patch.object(settings_routes, "save_settings"):
            return asyncio.run(settings_routes.update_home_layout(body))

    def test_late_migration_does_not_overwrite_a_folder_layout(self):
        store = {"home_layout": {"version": 2, "positions": dict(NEW)}}
        result = self.put(store, {"folder-play": 0, "folder-later": 1}, migration=True)
        self.assertEqual("already_migrated", result["skipped"])
        self.assertEqual(NEW, store["home_layout"]["positions"])

    def test_migration_replaces_an_old_layout(self):
        store = {"home_layout": {"version": 2, "positions": dict(OLD)}}
        self.put(store, NEW, migration=True)
        self.assertEqual(NEW, store["home_layout"]["positions"])

    def test_normal_drag_save_still_overwrites(self):
        store = {"home_layout": {"version": 2, "positions": dict(NEW)}}
        self.put(store, {"folder-play": 0, "folder-later": 1}, migration=False)
        self.assertEqual({"folder-play": 0, "folder-later": 1}, store["home_layout"]["positions"])

    def test_page_cancels_a_pending_migration_when_the_server_already_has_folders(self):
        page = HOME.read_text(encoding="utf-8")
        self.assertIn("scheduleLayoutServerSave({ migration: true })", page)
        self.assertIn("migration: Boolean(options.migration)", page)
        sync = page[page.index("async function syncLayoutFromServer"):page.index("function requestLayoutSync")]
        self.assertLess(sync.index("cancelPendingMigrationSave();"), sync.index("const nextLayout"))


if __name__ == "__main__":
    unittest.main()
