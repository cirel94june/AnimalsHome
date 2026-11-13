import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import config
import image_gen
from image_profiles import profiles_from,validate_profiles
from routes import settings


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(settings,'save_settings',lambda *args:None)
    monkeypatch.setattr(settings.manager,'broadcast',AsyncMock())
    for key in ['image_gen_provider','image_gen_base_url','image_gen_api_key','image_gen_model']:
        monkeypatch.setitem(config.SETTINGS,key,'')
    monkeypatch.setitem(config.SETTINGS,'image_gen_profiles',{})


def test_legacy_configuration_migrates_to_correct_protocol():
    value=profiles_from({'image_gen_provider':'gemini','image_gen_api_key':'BANANA_KEY','image_gen_model':'banana-model'})
    assert value['gemini']['api_key']=='BANANA_KEY' and value['openai']['api_key']==''


def test_code_can_be_saved_without_picture_keys_and_profiles_survive(environment):
    profiles={'openai':{'base_url':'https://openai.invalid/v1','api_key':'OPENAI_KEY','model':'gpt-image-test'},
              'gemini':{'base_url':'','api_key':'BANANA_KEY','model':''}}
    asyncio.run(settings.update_settings(settings.SettingsUpdate(image_gen_provider='code',image_gen_profiles=profiles,
        image_gen_base_url='',image_gen_api_key='',image_gen_model='')))
    assert config.SETTINGS['image_gen_provider']=='code'
    assert config.SETTINGS['image_gen_profiles']==profiles
    asyncio.run(settings.update_settings(settings.SettingsUpdate(image_gen_provider='gemini')))
    assert config.SETTINGS['image_gen_profiles']==profiles


def test_old_client_updates_only_its_active_profile(environment):
    monkey=profiles_from({'image_gen_provider':'gemini','image_gen_api_key':'KEEP_BANANA'})
    config.SETTINGS['image_gen_profiles']=monkey
    asyncio.run(settings.update_settings(settings.SettingsUpdate(image_gen_provider='openai',image_gen_base_url='https://picture.invalid/v1',image_gen_api_key='NEW_OPENAI',image_gen_model='gpt-image-test')))
    assert config.SETTINGS['image_gen_profiles']['gemini']['api_key']=='KEEP_BANANA'
    assert config.SETTINGS['image_gen_profiles']['openai']['api_key']=='NEW_OPENAI'


@pytest.mark.parametrize('profile',[{'openai':{'api_key':'only-key'}},{'gemini':{'base_url':'https://bad.invalid/?key=SECRET'}}])
def test_invalid_profile_does_not_mutate_settings(environment,profile):
    before=dict(config.SETTINGS)
    with pytest.raises(HTTPException):
        asyncio.run(settings.update_settings(settings.SettingsUpdate(image_gen_provider='code',image_gen_profiles=profile)))
    assert config.SETTINGS==before


def test_explicit_mode_uses_its_own_configuration_without_changing_default(environment,monkeypatch):
    monkeypatch.setitem(config.SETTINGS,'image_gen_provider','code')
    monkeypatch.setitem(config.SETTINGS,'image_gen_profiles',validate_profiles({'openai':{'base_url':'https://picture.invalid','api_key':'ONLY_OPENAI','model':'gpt-image-test'},'gemini':{'api_key':'ONLY_BANANA'}}))
    called=[]
    async def openai(prompt,selfie,actor,*profile,**kwargs): called.append((prompt,actor,profile)); return 'album/test.png'
    monkeypatch.setattr(image_gen,'_generate_openai_image',openai)
    assert asyncio.run(image_gen.generate_image('openai|a flower',source_identity='ai3'))=='album/test.png'
    assert called==[('a flower','ai3',('https://picture.invalid','ONLY_OPENAI','gpt-image-test'))]
    assert config.SETTINGS['image_gen_provider']=='code'


def test_code_generation_is_text_only_and_bounded(environment,monkeypatch):
    import ai_providers
    from text_only import TEXT_ONLY
    monkeypatch.setitem(config.SETTINGS,'image_gen_provider','code')
    monkeypatch.setattr(image_gen,'_code_model',lambda who:'own-model')
    closed=[]
    async def stream(*args):
        try:
            assert TEXT_ONLY.get() is True
            yield 'x'*180001
            raise AssertionError('oversized stream should stop')
        finally: closed.append(True)
    monkeypatch.setattr(ai_providers,'stream_ai',stream)
    assert asyncio.run(image_gen.generate_image('code|a flower',source_identity='ai3')) is None
    assert closed==[True] and TEXT_ONLY.get() is False
