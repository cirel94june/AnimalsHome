"""Independent local review of image stream cancellation and HTML collection concurrency."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

import ai_providers
import html_works
import image_gen
from text_only import TEXT_ONLY


@pytest.mark.parametrize('ending', ['timeout', 'cancel', 'provider_error'])
def test_review_code_stream_closes_and_restores_text_only(monkeypatch, ending):
    monkeypatch.setattr(image_gen, '_code_model', lambda actor: 'TEST_OWN_MODEL')
    monkeypatch.setattr(image_gen, 'IMAGE_GEN_TIMEOUT', .05 if ending == 'timeout' else 1)
    states, closed = [], []

    async def stream(messages, key, meta, **kwargs):
        assert key == 'TEST_OWN_MODEL'
        try:
            states.append(TEXT_ONLY.get())
            if ending == 'provider_error':
                meta['provider_error'] = 'TEST_FAILURE'
                yield '{}'
            else:
                await asyncio.sleep(10)
                yield '{}'
        finally:
            closed.append(True)

    monkeypatch.setattr(ai_providers, 'stream_ai', stream)

    async def run():
        assert TEXT_ONLY.get() is False
        if ending == 'cancel':
            task = asyncio.create_task(image_gen.generate_image('code|a test flower', source_identity='ai3'))
            for _ in range(20):
                if states: break
                await asyncio.sleep(.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        else:
            assert await image_gen.generate_image('code|a test flower', source_identity='ai3') is None
        assert TEXT_ONLY.get() is False
    asyncio.run(run())
    assert states == [True] and closed == [True]


def test_review_code_never_uses_a_default_model_when_identity_has_none(monkeypatch):
    import memory_hub_jobs
    identities = []
    def own(actor):
        identities.append(actor)
        return ''
    monkeypatch.setattr(memory_hub_jobs, 'own_model', own)
    async def no_calls(*args, **kwargs):
        raise AssertionError('must not invoke another model')
        yield ''
    monkeypatch.setattr(ai_providers, 'stream_ai', no_calls)
    assert asyncio.run(image_gen.generate_image('code|a test flower', source_identity='ai3')) is None
    assert identities == ['ai3']


def test_review_concurrent_collection_is_one_work_and_never_uses_weekly_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(html_works, 'DB_PATH', tmp_path / 'works.db')
    document = '<html><body><script>window.untrusted=true</script><div>test</div></body></html>'
    def collect(_):
        return html_works.save_chat_work('ONE_SOURCE', 'ai3', 'TEST_ACTOR', 'TEST_TITLE', document)
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(collect, range(8)))
    assert len(set(ids)) == 1
    assert len(html_works.list_works()) == 1
    assert html_works.recent_count('ai3') == 0
    assert html_works.get_work(ids[0], False)['html'] == document
    assert html_works.delete_work(ids[0])
    second = collect(None)
    assert second != ids[0]
    assert html_works.recent_count('ai3') == 0
