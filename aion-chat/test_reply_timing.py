import unittest
from unittest.mock import patch

from generation_control import Generation, GenerationQueue
from reply_timing import timed, model_started


class ReplyTimingTest(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_model_and_visible_output_have_separate_times(self):
        clock = [10.0]
        with patch('reply_timing.time.perf_counter', side_effect=lambda: clock[0]):
            scope = Generation('private', 'fixture', 'timing-test')

            @timed('history')
            async def history():
                clock[0] += .1

            async def turn():
                queue = GenerationQueue()
                await history()
                await queue.put({'type': 'start', 'id': 'reply'})
                first_text = model_started()
                clock[0] += 2
                first_text('hello')
                clock[0] += .2
                await queue.put({'type': 'chunk', 'content': 'hello'})
                clock[0] += .3
                await queue.put({'type': 'chunk', 'content': ' again'})
                return [queue.get_nowait() for _ in range(queue.qsize())]

            events = await scope.start(turn())
            reports = [e for e in events if e['type'] == 'reply_timing']
            self.assertEqual([r['phase'] for r in reports], ['context_ready', 'first_visible_text'])
            self.assertAlmostEqual(reports[0]['elapsed_ms'], 100)
            self.assertAlmostEqual(reports[1]['elapsed_ms'], 2300)
            self.assertEqual([s['stage'] for s in reports[1]['stages']], ['history', 'model_first_text'])
            self.assertAlmostEqual(reports[1]['stages'][1]['duration_ms'], 2000)
            self.assertNotIn('hello', str(reports))

    async def test_timed_functions_work_without_a_chat_and_keep_exceptions(self):
        @timed('fixture')
        async def fail():
            raise ValueError('expected')
        with self.assertRaisesRegex(ValueError, 'expected'):
            await fail()
