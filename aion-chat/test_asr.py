"""Offline checks for shared transcription settings and provider protocols."""
import asyncio
import importlib.util
import io
import json
import unittest
import wave
from unittest.mock import AsyncMock, patch

import httpx


def wav_audio():
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 1600)
    return buf.getvalue()


class AsrTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('asr'), 'Shared ASR provider is missing')
        import asr
        self.asr = asr

    async def test_legacy_and_custom_openai_use_correct_key_url_and_model(self):
        client = AsyncMock()
        client.post.return_value = httpx.Response(200, json={'text': ' 你好😀 '}, request=httpx.Request('POST', 'https://fixture.test'))
        with patch.object(self.asr.httpx, 'AsyncClient') as factory, patch.object(self.asr, 'SETTINGS', {'siliconflow_key': 'old-key'}):
            factory.return_value.__aenter__.return_value = client
            self.assertEqual(await self.asr.transcribe_audio(wav_audio()), '你好')
            self.assertEqual(client.post.call_args.args[0], 'https://api.siliconflow.cn/v1/audio/transcriptions')
            self.assertEqual(client.post.call_args.kwargs['headers']['Authorization'], 'Bearer old-key')
        config = {'asr_provider': 'openai', 'asr_base_url': 'https://fixture.test/v1/', 'asr_api_key': 'new-key', 'asr_model': 'whisper-1', 'siliconflow_key': 'old-key'}
        with patch.object(self.asr.httpx, 'AsyncClient') as factory, patch.object(self.asr, 'SETTINGS', config):
            factory.return_value.__aenter__.return_value = client
            await self.asr.transcribe_audio(wav_audio())
        self.assertEqual(client.post.call_args.args[0], 'https://fixture.test/v1/audio/transcriptions')
        self.assertEqual(client.post.call_args.kwargs['data']['model'], 'whisper-1')
        self.assertEqual(client.post.call_args.kwargs['headers']['Authorization'], 'Bearer new-key')

    async def test_paraformer_waits_for_start_and_joins_only_final_sentences(self):
        def event(name, sentence=None):
            return json.dumps({'header': {'event': name}, 'payload': {'output': {'sentence': sentence or {}}}})
        socket = AsyncMock()
        socket.recv.side_effect = [event('task-started'),
            event('result-generated', {'text': '错的中间结果', 'sentence_end': False}),
            event('result-generated', {'text': '你好。', 'sentence_end': True}),
            event('result-generated', {'text': 'Hello.', 'sentence_end': True}),
            event('task-finished')]
        config = {'asr_provider': 'dashscope', 'asr_api_key': 'fixture'}
        with patch.object(self.asr, 'SETTINGS', config), patch.object(self.asr, 'connect') as factory:
            factory.return_value.__aenter__.return_value = socket
            self.assertEqual(await self.asr.transcribe_audio(wav_audio()), '你好。Hello.')
        sent = [call.args[0] for call in socket.send.call_args_list]
        start = json.loads(sent[0])
        self.assertEqual(start['payload']['model'], 'paraformer-realtime-v2')
        self.assertEqual(start['payload']['parameters'], {'format': 'pcm', 'sample_rate': 16000, 'language_hints': ['zh', 'en']})
        self.assertEqual(sent[1], b'\0\0' * 1600)
        self.assertEqual(json.loads(sent[-1])['header']['action'], 'finish-task')
        self.assertEqual(factory.call_args.kwargs['additional_headers']['Authorization'], 'Bearer fixture')

    async def test_provider_failure_and_incomplete_config_are_not_silent_fallbacks(self):
        with patch.object(self.asr, 'SETTINGS', {'asr_api_key': 'new-key', 'siliconflow_key': 'old-key'}):
            with self.assertRaisesRegex(ValueError, '完整'):
                await self.asr.transcribe_audio(wav_audio())
        socket = AsyncMock()
        socket.recv.return_value = json.dumps({'header': {'event': 'task-failed', 'error_code': 'InvalidApiKey', 'error_message': 'bad key'}})
        with patch.object(self.asr, 'SETTINGS', {'asr_provider': 'dashscope', 'asr_api_key': 'fixture'}), patch.object(self.asr, 'connect') as factory:
            factory.return_value.__aenter__.return_value = socket
            with self.assertRaisesRegex(RuntimeError, 'InvalidApiKey'):
                await self.asr.transcribe_audio(wav_audio())
        self.assertEqual(socket.send.await_count, 1)
        async def stalled_receive():
            await asyncio.sleep(10)
        socket.recv.side_effect = stalled_receive
        with patch.object(self.asr, 'SETTINGS', {'asr_provider': 'dashscope', 'asr_api_key': 'fixture'}), patch.object(self.asr, 'connect') as factory:
            factory.return_value.__aenter__.return_value = socket
            with self.assertRaisesRegex(RuntimeError, '超时'):
                await self.asr.transcribe_audio(wav_audio(), timeout=0.02)

    async def test_upload_routes_and_desktop_wakeup_use_shared_transcriber(self):
        from fastapi import UploadFile
        from routes import voice as routes
        import numpy as np
        import voice
        with patch.object(routes, 'transcribe_audio', new_callable=AsyncMock, return_value='你好') as recognize:
            for endpoint in (routes.remote_asr, routes.transcribe_voice_message):
                result = await endpoint(UploadFile(filename='fixture.wav', file=io.BytesIO(wav_audio())))
                self.assertEqual(result, {'text': '你好'})
                self.assertEqual(recognize.call_args.args[0], wav_audio())
            recognize.side_effect = RuntimeError('fixture error')
            result = await routes.remote_asr(UploadFile(filename='fixture.wav', file=io.BytesIO(wav_audio())))
            self.assertEqual(result['error'], 'fixture error')
        with patch.object(voice, 'transcribe_audio', new_callable=AsyncMock, return_value='你好') as recognize:
            result = await asyncio.to_thread(voice.voice._asr, np.zeros(1600, dtype=np.int16))
            self.assertEqual(result, '你好')
            self.assertEqual(recognize.call_args.args[0], wav_audio())

    async def test_settings_roundtrip_and_invalid_save_preserves_current_settings(self):
        from routes import settings
        config = {'siliconflow_key': 'existing'}
        with patch.object(settings, 'SETTINGS', config), patch.object(settings, 'save_settings'):
            await settings.update_settings(settings.SettingsUpdate(asr_provider='dashscope', asr_api_key='fixture'))
            result = await settings.get_settings()
            self.assertEqual(result.get('asr_provider'), 'dashscope')
            self.assertEqual(result.get('asr_model'), 'paraformer-realtime-v2')
            self.assertEqual(config['siliconflow_key'], 'existing')
            previous = config.copy()
            with self.assertRaises(settings.HTTPException):
                await settings.update_settings(settings.SettingsUpdate(asr_base_url='https://fixture.test'))
            self.assertEqual(config, previous)
            with self.assertRaises(settings.HTTPException):
                await settings.update_settings(settings.SettingsUpdate(asr_api_key='changed-key', minimax_tts_model='unsupported'))
            self.assertEqual(config, previous)


if __name__ == '__main__':
    unittest.main()
