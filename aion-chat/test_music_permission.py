"""Execute the actual ordinary MUSIC dispatch loops with denied/late permissions."""
import ast
import asyncio
from pathlib import Path
from unittest.mock import Mock
import pytest


def dispatch_loops(relative):
    tree=ast.parse((Path(__file__).parent/relative).read_text(encoding='utf-8'))
    return [node for node in ast.walk(tree) if isinstance(node,ast.For)
            and isinstance(node.target,ast.Name) and node.target.id=='keyword'
            and 'music_matches' in ast.unparse(node.iter)]


@pytest.mark.parametrize('relative,count',[('routes/chat.py',3),('routes/chatroom.py',2),('schedule.py',2)])
@pytest.mark.parametrize('mode',['off','on','revoked'])
def test_every_chat_dispatch_enforces_music_permission(relative,count,mode):
    async def probe():
        loops=dispatch_loops(relative);assert len(loops)==count
        for node in loops:
            enabled={'value':mode!='off'}
            def search(*args,**kwargs):
                if mode=='revoked':enabled['value']=False
                return [{'id':1,'name':'sample','artist':'test'}]
            search=Mock(side_effect=search);audio=Mock(return_value='synthetic://audio')
            async def run_sync(fn,*args,**kwargs):return fn(*args,**kwargs)
            namespace={'asyncio':asyncio,'search_songs':search,'get_audio_url':audio,'music_cards':[],
                       'music_matches':['one','two','three','four'],
                       'is_capability_enabled':lambda key:enabled['value'],'run_music_sync':run_sync}
            function=ast.AsyncFunctionDef(name='dispatch',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[node],decorator_list=[])
            exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),relative,'exec'),namespace)
            await namespace['dispatch']()
            if mode=='on':
                assert len(namespace['music_cards'])==3
            else:
                assert namespace['music_cards']==[]
                assert search.call_count==(0 if mode=='off' else 1)
    asyncio.run(probe())


def test_wish_music_rechecks_permission_before_search():
    tree=ast.parse((Path(__file__).parent/'autonomy.py').read_text(encoding='utf-8'))
    statements=[node for node in ast.walk(tree) if isinstance(node,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='songs' for t in node.targets)
                and 'search_songs(music_keyword' in ast.unparse(node)]
    assert len(statements)==1
    search=Mock()
    namespace={'search_songs':search,'music_keyword':'sample','is_capability_enabled':lambda key:False}
    exec(compile(ast.fix_missing_locations(ast.Module(body=statements,type_ignores=[])),'autonomy.py','exec'),namespace)
    search.assert_not_called();assert namespace['songs']==[]
