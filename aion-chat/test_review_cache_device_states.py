"""Independent comparison with the deployed capability builder; no device IO."""
import ast
import asyncio
import subprocess

import pytest

import capabilities
from test_cache_device_states import test_device_changes_stay_after_cacheable_history as setup_synthetic


@pytest.mark.parametrize('kind',['phone','patrol','reference'])
@pytest.mark.parametrize('granted',[False,True])
def test_default_output_and_permission_gates_match_deployed_builder(monkeypatch,kind,granted):
    import app_supervision_ai as phone
    import svakom_ai
    # Reuse only the fully synthetic environment, including fake phone snapshots.
    setup_synthetic(monkeypatch,kind)
    if not granted:
        monkeypatch.setattr(capabilities,'is_capability_enabled',lambda key:False)
        monkeypatch.setattr(svakom_ai,'capture_permission',lambda **kw:False)
    if kind=='phone':monkeypatch.setattr(phone,'_phone_online',lambda:False)

    source=subprocess.run(['git','show','37d12f5:aion-chat/capabilities.py'],
                          capture_output=True,text=True,encoding='utf-8',check=True).stdout
    original=next(n for n in ast.parse(source).body
                  if isinstance(n,ast.AsyncFunctionDef) and n.name=='build_capability_prompt_items')
    namespace=vars(capabilities).copy()
    exec(compile(ast.Module(body=[original],type_ignores=[]),'synthetic-production-baseline','exec'),namespace)
    before=asyncio.run(namespace['build_capability_prompt_items']('synthetic'))
    after=asyncio.run(capabilities.build_capability_prompt_items('synthetic'))
    assert before==after, 'caller omitting live_state_out changed output'
    live=[]
    static=asyncio.run(capabilities.build_capability_prompt_items('synthetic',live_state_out=live))
    if not granted:
        assert live==[]
        assert static==after
    elif kind in ('phone','patrol'):
        assert sorted(static+live)==sorted(after), 'moving state lost or duplicated complete control instructions'
        assert len(live)==1
    else:
        assert any(svakom_ai.PROMPT==item for item in static)
        assert live==['reference-B']
        assert '\n'.join(static+live).count('reference-B')==1
