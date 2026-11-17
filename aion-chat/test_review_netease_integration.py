"""Run actual playlist dispatch statements with a failed transport result."""
import ast
import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from safe_live_stream import consume_safe_live_stream


def dispatch_statements(path):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    parents={child:node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    result=[]
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Name) or node.func.id!='process_music_playlists':continue
        # Keep surrounding conditionals, stopping at the containing try/function.
        statement=node
        while not isinstance(parents.get(statement),(ast.Try,ast.AsyncFunctionDef,ast.FunctionDef)):
            statement=parents[statement]
        result.append(statement)
    return result


@pytest.mark.parametrize('relative',['routes/chat.py','schedule.py'])
def test_failed_model_stream_never_dispatches_playlist_writes(relative):
    async def probe():
        async def source():
            yield '我准备整理一张晚安歌单。[MUSIC_PLAYLIST]{"name":"night"}[/MUSIC_PLAYLIST]这是我今天认真挑选的音乐，希望你喜欢。'
            raise RuntimeError('synthetic provider failure')
        result=await consume_safe_live_stream(source(),lambda text:None)
        assert result.stop_reason and '[/MUSIC_PLAYLIST]' in result.committed_text
        statements=dispatch_statements(Path(__file__).parent/relative)
        assert len(statements)==(3 if relative=='routes/chat.py' else 2)
        for statement in statements:
            process=AsyncMock(return_value='saved')
            function=ast.AsyncFunctionDef(name='dispatch',args=ast.arguments(posonlyargs=[],args=[ast.arg(arg='full_text')],kwonlyargs=[],kw_defaults=[],defaults=[]),
                                          body=[statement],decorator_list=[])
            namespace={'process_music_playlists':process,'has_error':True,'sender':'aion','re':re,
                       'target':{'type':'private'},'conv_id':'synthetic-conv','ai_msg_id':'synthetic-message'}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),relative,'exec'),namespace)
            await namespace['dispatch'](result.committed_text)
            process.assert_not_awaited()
    asyncio.run(probe())
