"""A fixed default and the currently edited protocol are independent selectors."""
import asyncio
from unittest.mock import AsyncMock

from routes import settings


def test_review_openai_default_can_save_while_editing_official_banana(monkeypatch):
    for key in ['image_gen_provider','image_gen_base_url','image_gen_api_key','image_gen_model']:
        monkeypatch.setitem(settings.SETTINGS,key,'')
    monkeypatch.setitem(settings.SETTINGS,'image_gen_profiles',{})
    monkeypatch.setattr(settings,'save_settings',lambda *args:None)
    monkeypatch.setattr(settings.manager,'broadcast',AsyncMock())
    profiles={
        'openai': {'base_url':'https://picture.invalid/v1','api_key':'OPENAI_KEY','model':'gpt-image-test'},
        'gemini': {'base_url':'','api_key':'BANANA_KEY','model':''},
    }
    # This is the actual saveSettings payload with default=openai and editor=gemini:
    # canonical profiles include both, legacy fields are taken from visible Banana inputs.
    body=settings.SettingsUpdate(image_gen_provider='openai',image_gen_profiles=profiles,
        image_gen_base_url='',image_gen_api_key='BANANA_KEY',image_gen_model='')
    asyncio.run(settings.update_settings(body))
    assert settings.SETTINGS['image_gen_provider']=='openai'
    assert settings.SETTINGS['image_gen_profiles']==profiles
