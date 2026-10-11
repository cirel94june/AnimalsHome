"""Read real digest queries against isolated data, stopping before AI generation."""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite


ROOT = Path(__file__).resolve().parent


class MessagesCaptured(BaseException):
    pass


class DigestWindowScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "chat.db"
        async with self.connect() as db:
            await db.executescript("""
                CREATE TABLE messages (id TEXT, conv_id TEXT, role TEXT, content TEXT,
                                       attachments TEXT, created_at REAL);
                CREATE TABLE chatroom_rooms (id TEXT, type TEXT, updated_at REAL);
                CREATE TABLE chatroom_messages (id TEXT, room_id TEXT, sender TEXT,
                                                content TEXT, created_at REAL);
                CREATE TABLE chatroom_digest_anchors (room_id TEXT, anchor_ts REAL);
                INSERT INTO chatroom_digest_anchors VALUES ('connor_unified', 20);
                INSERT INTO messages VALUES
                    ('p-old', 'old', 'user', 'old window new message', NULL, 11),
                    ('p-new', 'new', 'assistant', 'new window', NULL, 30),
                    ('p-boundary', 'old', 'user', 'already summarized', NULL, 10);
            """)
            for room, kind, updated in (
                ('g-old', 'group', 1), ('g-new', 'group', 100),
                ('c-old', 'connor_1v1', 1), ('c-new', 'connor_1v1', 100),
                ('excluded', 'other', 200),
            ):
                await db.execute("INSERT INTO chatroom_rooms VALUES (?,?,?)", (room, kind, updated))
                for ts, sender in ((10, 'user'), (15, 'user'), (20, 'user'), (25, 'connor'), (26, 'system')):
                    await db.execute("INSERT INTO chatroom_messages VALUES (?,?,?,?,?)",
                                     (f'{room}-{ts}', room, sender, 'message', ts))
            await db.commit()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def connect(self):
        return aiosqlite.connect(self.db_path)

    async def collect(self, filename, function_name):
        # Load the complete production function without unrelated hardware imports.
        tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == function_name)
        env = {'get_db': self.connect, 'aiosqlite': aiosqlite, 'load_digest_anchor': lambda: 10}
        exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), env)
        captured = {}

        async def capture(db, target, messages):
            captured.update(target=target, messages=messages)
            raise MessagesCaptured()

        with patch('homecoming.summary_coverage.filter_uncovered', side_effect=capture), patch(
            'ai_providers.simple_ai_call', new_callable=AsyncMock
        ) as model:
            with self.assertRaises(MessagesCaptured):
                await env[function_name]()
            model.assert_not_awaited()
        return captured

    async def test_main_includes_all_private_and_group_windows_after_its_anchor(self):
        result = await self.collect('memory.py', '_do_digest')
        self.assertEqual(result['target'], 'main')
        messages = result['messages']
        self.assertEqual({m['_source_id'] for m in messages}, {
            'private:p-old', 'private:p-new',
            *(f'chatroom:{room}-{ts}' for room in ('g-old', 'g-new') for ts in (15, 20, 25)),
        })
        self.assertEqual([m['created_at'] for m in messages], sorted(m['created_at'] for m in messages))

    async def test_second_uses_its_own_anchor_across_all_private_and_group_windows(self):
        result = await self.collect('chatroom.py', 'digest_chatroom')
        self.assertEqual(result['target'], 'second')
        self.assertEqual({m['_source_id'] for m in result['messages']}, {
            f'chatroom:{room}-25' for room in ('g-old', 'g-new', 'c-old', 'c-new')
        })
        self.assertEqual({m['_source'] for m in result['messages']}, {'private', 'group'})


if __name__ == '__main__':
    unittest.main()
