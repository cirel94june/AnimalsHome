import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite


class PostSentinelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import post_sentinel as service
        self.service = service
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'chat.db'
        self.db_patch = patch.object(service, 'get_db', self.connect)
        self.enabled_patch = patch.object(service, 'enabled', return_value=True)
        self.db_patch.start()
        self.enabled_patch.start()
        async with self.connect() as db:
            await db.execute('CREATE TABLE messages (id TEXT,conv_id TEXT,role TEXT,content TEXT,created_at REAL)')
            await db.execute('CREATE TABLE chatroom_messages (id TEXT,room_id TEXT,sender TEXT,content TEXT,created_at REAL)')
            await db.commit()
        await service.ensure_schema()

    async def asyncTearDown(self):
        await self.service.stop()
        self.db_patch.stop()
        self.enabled_patch.stop()
        self.tmp.cleanup()

    def connect(self):
        return aiosqlite.connect(self.db_path)

    async def message(self, msg_id='one', *, text='我喜欢温柔的提醒', scope='private', ts=None):
        ts = ts or time.time()
        private = scope == 'private'
        async with self.connect() as db:
            await db.execute(f'INSERT INTO {"messages" if private else "chatroom_messages"} VALUES (?,?,?,?,?)',
                             (msg_id, 'room', 'user', text, ts))
            await db.commit()
        return {'type': 'msg_created' if private else 'chatroom_msg_created', 'data': {
            'id': msg_id, 'conv_id' if private else 'room_id': 'room',
            'role' if private else 'sender': 'user', 'content': text, 'created_at': ts,
        }}

    def analysis(self, content='喜欢温柔的提醒', source='U1'):
        return {'updates': [{'key': 'communication.reminders', 'category': 'companionship',
                             'content': content, 'sources': [source]}], 'state': None}

    async def test_profile_subject_uses_configured_name_for_facts_and_activity(self):
        for name in ('小星', 'Mira'):
            with self.subTest(name=name), patch.object(self.service, 'load_worldbook', return_value={'user_name': name}):
                prompt = self.service._analysis_prompt({'entries': {}, 'current_state': None}, [], {}, '')
                self.assertIn(f'本人姓名为“{name}”', prompt)
                document = {'entries': {}, 'current_state': None}
                response = {'updates': [{'category': 'profile', 'content': '用户喜欢自然聊天',
                                         'topic': '用户的交流偏好', 'sources': ['U1']}],
                            'state': {'text': '用户正在洗菜', 'phase': 'ongoing', 'sources': ['U1']}}
                self.service._apply_analysis(document, response, {'U1': 'private:one'}, {'source_ts': time.time()})
                entry = next(iter(document['entries'].values()))
                self.assertEqual(entry['content'], name + '喜欢自然聊天')
                self.assertEqual(entry['topic'], name + '的交流偏好')
                self.assertEqual(document['current_state']['text'], name + '正在洗菜')

    async def test_subject_rename_preserves_technical_terms_and_other_people(self):
        from user_profile_policy import personalize_text, personalize_document
        self.assertEqual(personalize_text('用户在做用户画像功能，给其他用户改善用户体验', 'Mira'),
                         'Mira在做用户画像功能，给其他用户改善用户体验')
        self.assertEqual(personalize_text('用户喜欢写字', '名字用户'), '名字用户喜欢写字')
        entry = {'key': 'manual', 'content': '用户喜欢自然聊天', 'topic': '', 'locked': True,
                 'sources': ['manual'], 'source_ts': 123.456, 'updated_at': 789.012}
        state = {'text': '用户正在洗菜', 'event_at': 234.567, 'expires_at': 800.123, 'locked': True}
        document = {'entries': {'manual': dict(entry)}, 'current_state': dict(state)}
        self.assertEqual(personalize_document(document, 'Mira'), (1, True))
        self.assertEqual(personalize_document(document, 'Mira'), (0, False))
        for key in entry.keys() - {'content'}:
            self.assertEqual(document['entries']['manual'][key], entry[key])
        for key in state.keys() - {'text'}:
            self.assertEqual(document['current_state'][key], state[key])

    async def test_deduplicates_user_events_and_ignores_ai_and_system_messages(self):
        event = await self.message()
        await self.service.enqueue_event(event)
        await self.service.enqueue_event(event)
        for role in ('assistant', 'system'):
            await self.service.enqueue_event({'type': 'msg_created', 'data': {**event['data'], 'role': role}})
        model = AsyncMock(return_value=self.analysis())
        with patch.object(self.service, '_call_model', model):
            self.assertTrue(await self.service.run_pending_once())
            self.assertFalse(await self.service.run_pending_once())
        model.assert_awaited_once()
        data = await self.service.snapshot()
        self.assertEqual(data['entries'][0]['sources'], ['private:one'])
        self.assertIn('喜欢温柔', await self.service.build_context())

    async def test_shared_chatroom_uses_same_profile_and_short_user_sources(self):
        await self.service.enqueue_event(await self.message(scope='chatroom'))
        with patch.object(self.service, '_call_model', AsyncMock(return_value=self.analysis())) as model:
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(data['entries'][0]['sources'], ['chatroom:one'])
        self.assertIn('U1', model.await_args.args[0])

    async def test_manual_edit_allows_future_updates_but_stale_analysis_cannot_overwrite_it(self):
        await self.service.enqueue_event(await self.message())
        async def during_call(prompt):
            data = await self.service.snapshot()
            await self.service.save_manual({'entries': [{
                'key': 'communication.reminders', 'category': 'companionship',
                'content': '我自己写的提醒偏好', 'locked': True,
            }], 'current_state': None}, data['revision'])
            return self.analysis()
        with patch.object(self.service, '_call_model', side_effect=during_call):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(data['entries'][0]['content'], '我自己写的提醒偏好')
        self.assertFalse(data['entries'][0]['locked'])
        await self.service.enqueue_event(await self.message('two'))
        with patch.object(self.service, '_call_model', AsyncMock(return_value=self.analysis('自动改写', 'U2'))):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['entries'][0]['content'], '自动改写')
        self.assertEqual((await self.service.snapshot())['last_error'], '')

    async def test_off_disables_analysis_and_context_and_invalidates_inflight_result(self):
        event = await self.message()
        with patch.object(self.service, 'enabled', return_value=False), patch.object(self.service, '_call_model', AsyncMock()) as model:
            await self.service.enqueue_event(event)
            await self.service.run_pending_once()
            self.assertEqual(await self.service.build_context(), '')
            model.assert_not_awaited()
        await self.service.enqueue_event(event)
        async def during_call(prompt):
            await self.service.invalidate_pending()
            return self.analysis()
        with patch.object(self.service, '_call_model', side_effect=during_call):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['entries'], [])

    async def test_invalid_source_and_model_failure_leave_document_unchanged(self):
        event = await self.message()
        await self.service.enqueue_event(event)
        invalid = self.analysis()
        invalid['updates'][0]['sources'] = ['made-up']
        with patch.object(self.service, '_call_model', AsyncMock(return_value=invalid)):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['entries'], [])
        await self.service.enqueue_event(await self.message('two'))
        with patch.object(self.service, '_call_model', AsyncMock(side_effect=RuntimeError('model offline'))):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(data['entries'], [])
        self.assertTrue(data['last_error'])

    async def test_state_expiry_noop_and_older_events(self):
        now = time.time()
        await self.service.enqueue_event(await self.message(text='我已经泡上澡了', ts=now))
        response = {'updates': [], 'state': {'text': '正在泡澡', 'phase': 'ongoing',
                                           'ttl_minutes': 30, 'sources': ['U1']}}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        first = (await self.service.snapshot())['current_state']
        self.assertIn('正在泡澡', await self.service.build_context(now=now + 60))
        self.assertNotIn('正在泡澡', await self.service.build_context(now=now + 3600))
        await self.service.enqueue_event(await self.message('two', text='哈哈', ts=now + 120))
        with patch.object(self.service, '_call_model', AsyncMock(return_value={'updates': [], 'state': None})):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['current_state']['event_at'], first['event_at'])
        await self.service.enqueue_event(await self.message('old', ts=now - 100))
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['current_state']['event_at'], first['event_at'])

    async def test_manual_revision_and_deleted_key_protection(self):
        data = await self.service.snapshot()
        content = {'entries': [{'key': 'food.preference', 'category': 'preferences',
                               'content': '喜欢清淡', 'locked': True}], 'current_state': None}
        await self.service.save_manual(content, data['revision'])
        with self.assertRaises(ValueError):
            await self.service.save_manual(content, data['revision'])
        data = await self.service.snapshot()
        await self.service.save_manual({'entries': [], 'current_state': None}, data['revision'])
        await self.service.enqueue_event(await self.message())
        response = {'updates': [{'key': 'food.preference', 'category': 'preferences',
                                 'content': '喜欢重口味', 'sources': ['U1']}], 'state': None}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['entries'], [])

    async def test_api_manual_edit_and_disabled_context_in_shared_prompt(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from routes.user_profile import router
        app = FastAPI()
        app.include_router(router)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            data = (await client.get('/api/user-profile')).json()
            body = {'revision': data['revision'], 'entries': [{
                'key': 'preferences.food', 'category': 'preferences', 'content': '手写偏好', 'locked': True,
            }], 'current_state': None}
            self.assertEqual((await client.put('/api/user-profile', json=body)).status_code, 200)
            self.assertEqual((await client.put('/api/user-profile', json=body)).status_code, 409)
        import context_builder
        with patch.object(context_builder, 'build_capability_prompt_items', AsyncMock(return_value=[])), patch.object(
            context_builder, 'is_capability_enabled', return_value=False
        ), patch.object(context_builder, 'get_device_context_for_prompt', return_value=''), patch.object(
            context_builder, 'build_cli_file_storage_text', return_value=''
        ):
            self.assertIn('手写偏好', await context_builder.build_ability_block('用户'))
            with patch.object(self.service, 'enabled', return_value=False):
                self.assertNotIn('手写偏好', await context_builder.build_ability_block('用户'))

    async def test_real_sentinel_adapter_uses_config_and_parses_output(self):
        import memory
        with patch.object(self.service, 'get_sentinel_config', return_value={
            'ready': True, 'model': 'configured-luna'
        }), patch.object(memory, '_call_sentinel_text', AsyncMock(return_value='{"updates":[],"state":null}')) as call:
            self.assertEqual(await self.service._call_model('test'), {'updates': [], 'state': None})
        self.assertEqual(call.await_args.args[0]['model'], 'configured-luna')

    async def test_background_luna_does_not_wait_for_busy_chat_slots(self):
        import ai_providers
        from types import SimpleNamespace
        async def stream(*args, **kwargs):
            yield SimpleNamespace(kind='text_delta', text='{"updates":[],"state":null}')
        with patch.object(self.service, 'get_sentinel_config', return_value={
            'ready': True, 'provider': 'codex', 'model': 'configured-luna'
        }), patch.object(ai_providers, '_CODEX_SCRIPT', 'fixture-script'), patch.object(
            ai_providers, '_codex_semaphore', return_value=asyncio.Semaphore(0)
        ), patch.object(ai_providers, '_build_codex_chat_environment', return_value={}), patch.object(
            ai_providers, 'stream_codex_app_server', side_effect=stream
        ):
            self.assertEqual(await asyncio.wait_for(self.service._call_model('test'), timeout=2),
                             {'updates': [], 'state': None})

    async def test_cleared_state_does_not_return_from_old_queued_message(self):
        now = time.time()
        first = await self.message(text='我在泡澡', ts=now - 20)
        second = await self.message('two', text='还在泡澡', ts=now - 10)
        response = {'updates': [], 'state': {'text': '正在泡澡', 'phase': 'ongoing',
                                           'ttl_minutes': 30, 'sources': ['U1']}}
        await self.service.enqueue_event(first)
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        await self.service.enqueue_event(second)
        data = await self.service.snapshot()
        await self.service.save_manual({'entries': [], 'current_state': None}, data['revision'])
        response['state']['sources'] = ['U2']
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        self.assertIsNone((await self.service.snapshot())['current_state'])

    async def test_source_edit_during_model_call_discards_extraction(self):
        await self.message('old', text='我喜欢苹果', ts=time.time()-20)
        await self.service.enqueue_event(await self.message(text='对，就是那种水果'))
        async def during_call(prompt):
            async with self.connect() as db:
                await db.execute('UPDATE messages SET content=? WHERE id=?', ('我喜欢香蕉', 'old'))
                await db.commit()
            response = self.analysis('喜欢苹果')
            response['updates'][0]['sources'] = ['U1', 'U2']
            return response
        with patch.object(self.service, '_call_model', side_effect=during_call):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['entries'], [])

    async def test_off_before_dispatch_prevents_model_call(self):
        await self.service.enqueue_event(await self.message())
        context = self.service._context_for_job
        active = True
        async def disable_during_context(job):
            nonlocal active
            result = await context(job)
            active = False
            await self.service.invalidate_pending()
            return result
        with patch.object(self.service, 'enabled', side_effect=lambda: active), patch.object(
            self.service, '_context_for_job', side_effect=disable_during_context
        ), patch.object(self.service, '_call_model', AsyncMock()) as model:
            await self.service.run_pending_once()
            model.assert_not_awaited()

    async def test_clear_empty_state_fences_older_queued_activity(self):
        await self.service.enqueue_event(await self.message(text='我在泡澡', ts=time.time()-10))
        data = await self.service.snapshot()
        await self.service.save_manual({'entries': [], 'current_state': None}, data['revision'])
        response = {'updates': [], 'state': {'text': '正在泡澡', 'phase': 'ongoing',
                                           'ttl_minutes': 30, 'sources': ['U1']}}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        self.assertIsNone((await self.service.snapshot())['current_state'])

    async def test_valid_pickup_survives_invalid_state_and_partial_retry(self):
        await self.service.enqueue_event(await self.message(text='明天十点半之后取药'))
        response = {'updates': [{'category': 'recent', 'topic': '取药', 'kind': 'task',
                                'content': '明天10:30之后取药，尚未完成', 'sources': ['U1']}],
                    'state': {'text': '计划明天取药', 'phase': 'planned',
                              'ttl_minutes': 'bad', 'sources': ['U1']}}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(len(data['entries']), 1)
        self.assertIn('有效期', data['last_error'])
        self.assertEqual(data['pending'], 1)
        self.assertIn('尚未完成', await self.service.build_context(now=time.time() + 45*86400))

    async def test_same_topic_updates_and_completed_task_leaves_prompt(self):
        available = time.time() + 86400
        await self.service.enqueue_event(await self.message(text='准备去医院取药'))
        response = {'updates': [{'category': 'recent', 'topic': '取药', 'kind': 'task',
                                'content': '准备去医院取药', 'available_at': available,
                                'sources': ['U1']}], 'state': None}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        await self.service.enqueue_event(await self.message('two', text='先吃早饭，晚点再出门'))
        response['updates'][0].update(ref='P1', content='早饭后出门取药', available_at=None, sources=['U2'])
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        delayed = (await self.service.snapshot())['entries'][0]
        self.assertEqual(delayed['available_at'], available)
        self.assertEqual(delayed['status'], 'active')
        await self.service.enqueue_event(await self.message('three', text='已经取到了'))
        response['updates'][0].update(content='已经取到药', status='done', sources=['U3'])
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(len(data['entries']), 1)
        self.assertEqual(data['entries'][0]['status'], 'done')
        self.assertNotIn('取药', await self.service.build_context())

    async def test_prompt_budget_and_project_decay_without_losing_tasks(self):
        now = time.time()
        values = {
            'old': {'key': 'old', 'category': 'recent', 'kind': 'project', 'topic': '临时项目',
                    'content': '曾考虑做一个小游戏', 'source_ts': now-31*86400},
            'task': {'key': 'task', 'category': 'recent', 'kind': 'task', 'topic': '医院',
                     'content': '需要去医院取药，结果尚未确认', 'due_at': now-86400, 'source_ts': now-31*86400},
        }
        for n in range(15):
            values[f'p{n}'] = {'key': f'p{n}', 'category': 'profile', 'content': '偏好内容' * 45,
                              'source_ts': now, 'locked': n == 0}
        async with self.connect() as db:
            await db.execute('UPDATE post_sentinel_document SET payload=?',
                             (json.dumps({'version': 2, 'entries': values, 'current_state': None}),))
            await db.commit()
        context = await self.service.build_context(now=now)
        self.assertLessEqual(len(context), 1200)
        self.assertNotIn('小游戏', context)
        self.assertIn('取药', context)
        self.assertIn('结果未确认', context)
        self.assertEqual(len((await self.service.snapshot())['entries']), 17)

    async def test_earlier_user_evidence_can_be_backfilled_without_refreshing_age(self):
        old_time = time.time()-3600
        await self.message('earlier', text='我喜欢安静', ts=old_time)
        await self.service.enqueue_event(await self.message(text='今天在看电影'))
        response = {'updates': [{'category': 'profile', 'topic': '交流', 'content': '喜欢安静',
                                'sources': ['U1']}], 'state': None}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(data['entries'][0]['source_ts'], old_time)
        self.assertEqual(data['last_error'], '')

    async def test_failed_job_retries_with_exact_error_and_recovers(self):
        await self.service.enqueue_event(await self.message())
        with patch.object(self.service, '_call_model', AsyncMock(side_effect=RuntimeError('model offline'))):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertIn('model offline', data['last_error'])
        self.assertEqual(data['pending'], 1)
        async with self.connect() as db:
            await db.execute('UPDATE post_sentinel_jobs SET next_attempt_at=0')
            await db.commit()
        with patch.object(self.service, '_call_model', AsyncMock(return_value=self.analysis())):
            await self.service.run_pending_once()
        self.assertEqual((await self.service.snapshot())['pending'], 0)
        self.assertEqual((await self.service.snapshot())['last_error'], '')

    async def test_migration_backs_up_once_and_preserves_manual_and_age(self):
        old = {'entries': {'pref': {'key': 'pref', 'category': 'preferences', 'content': '手动内容',
                                   'locked': True, 'source_ts': 123, 'sources': ['manual']},
                           'removed': {'key': 'removed', 'category': 'preferences', 'content': '已删内容',
                                       'locked': True, 'deleted': True, 'source_ts': 122}},
               'current_state': {'text': '手动状态', 'phase': 'ongoing', 'locked': True,
                                 'event_at': 234.567, 'expires_at': 1234.567}}
        async with self.connect() as db:
            await db.execute('UPDATE post_sentinel_document SET payload=?', (json.dumps(old),))
            await db.commit()
        await self.service.ensure_schema()
        await self.service.ensure_schema()
        data = await self.service.snapshot()
        self.assertEqual(data['entries'][0]['category'], 'profile')
        self.assertEqual(data['entries'][0]['source_ts'], 123)
        self.assertFalse(data['entries'][0]['locked'])
        self.assertFalse(data['current_state']['locked'])
        self.assertEqual(data['current_state']['event_at'], 234.567)
        async with self.connect() as db:
            count = (await (await db.execute('SELECT count(*) FROM post_sentinel_backups')).fetchone())[0]
            payload = json.loads((await (await db.execute('SELECT payload FROM post_sentinel_document')).fetchone())[0])
        self.assertTrue(payload['entries']['removed']['deleted'])
        self.assertTrue(payload['entries']['removed']['locked'])
        self.assertEqual(count, 1)

    async def test_consolidation_cannot_retire_or_merge_away_unfinished_task(self):
        import copy
        now = time.time()
        original = {'entries': {'task': {'key': 'task', 'category': 'recent', 'kind': 'task',
                    'status': 'stale', 'content': '明天取药', 'source_ts': now, 'sources': ['private:one']}}}
        for update in ({'ref': 'P1', 'status': 'retired'}, {'ref': 'P1', 'status': 'done'},
                       {'ref': 'P1', 'kind': 'context'}, {'merge_from': ['P1'], 'kind': 'context'}):
            with self.subTest(update=update):
                document = copy.deepcopy(original)
                self.service._apply_analysis(document, {'updates': [{**update, 'category': 'recent',
                    'content': '压缩后的资料'}], 'state': None}, {}, {'consolidation': True, 'source_ts': now})
                self.assertEqual(document['entries']['task']['status'], 'stale')
                self.assertTrue(document['_analysis_errors'])

    async def test_two_independent_tasks_can_share_topic_without_overwrite(self):
        await self.service.enqueue_event(await self.message(text='明天取自己的药'))
        response = {'updates': [{'category': 'recent', 'topic': '取药', 'kind': 'task',
                                'content': '明天取自己的药', 'sources': ['U1']}], 'state': None}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        await self.service.save_manual({'entries': [{**data['entries'][0], 'locked': True}], 'current_state': None}, data['revision'])
        await self.service.enqueue_event(await self.message('two', text='下周还要取狗狗的药'))
        response['updates'][0].update(content='下周取狗狗的药', sources=['U2'])
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        self.assertEqual(len((await self.service.snapshot())['entries']), 2)

    async def test_stale_project_keeps_historical_label_until_retirement(self):
        from user_profile_policy import render_context
        now = time.time()
        entry = {'key': 'project', 'category': 'recent', 'kind': 'project',
                 'content': '准备做一个小游戏', 'source_ts': now-14*86400}
        document = {'entries': {'project': entry}}
        context = render_context(document, now=now)[0]
        self.assertIn('小游戏', context)
        self.assertIn('久未确认', context)
        self.assertNotIn('小游戏', render_context(document, now=now+16*86400)[0])

    async def test_new_model_label_and_explicit_duration_are_normalized(self):
        now = time.time()
        await self.service.enqueue_event(await self.message(text='我喜欢完整段落，接下来一周在做一个创作练习', ts=now))
        response = {'updates': [
            {'ref': 'P4', 'category': 'profile', 'topic': '交流', 'content': '喜欢完整段落', 'sources': ['U1']},
            {'category': 'recent', 'kind': 'context', 'topic': '练习', 'duration_days': 7,
             'content': '接下来一周做创作练习', 'sources': ['U1']},
        ], 'state': None}
        with patch.object(self.service, '_call_model', AsyncMock(return_value=response)):
            await self.service.run_pending_once()
        data = await self.service.snapshot()
        self.assertEqual(data['last_error'], '')
        self.assertEqual(len(data['entries']), 2)
        self.assertIn('练习', await self.service.build_context(now=now+4*86400))
        self.assertNotIn('练习', await self.service.build_context(now=now+8*86400))
        self.assertIn('完整段落', await self.service.build_context(now=now+8*86400))

    async def test_api_unrelated_edit_preserves_task_times_and_confirmation(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from routes.user_profile import router
        now = time.time()
        data = await self.service.save_manual({'entries': [
            {'key': 'manual.preference', 'category': 'profile', 'content': '完整段落', 'locked': True},
            {'key': 'manual.task', 'category': 'recent', 'kind': 'task', 'status': 'active',
             'content': '明天取药', 'available_at': now+86400, 'locked': False},
        ], 'current_state': None}, 0)
        original = data['entries'][1]
        app = FastAPI()
        app.include_router(router)
        # The compact editor sends only visible fields; lifecycle metadata must survive.
        entries = [{k:v for k,v in e.items() if k in ('key','category','content','locked')} for e in data['entries']]
        entries[0]['content'] = '喜欢自然完整的段落'
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.put('/api/user-profile', json={'revision':data['revision'], 'entries':entries,'current_state':None})
        self.assertEqual(response.status_code, 200)
        task = response.json()['entries'][1]
        self.assertEqual(task['source_ts'], original['source_ts'])
        self.assertEqual(task['available_at'], original['available_at'])
        self.assertEqual(task['kind'], 'task')
        self.assertEqual(task['status'], 'active')
        self.assertFalse(task['locked'])


if __name__ == '__main__':
    unittest.main()
