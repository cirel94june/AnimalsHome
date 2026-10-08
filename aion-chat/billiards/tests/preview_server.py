"""Isolated local QA host. Never opens the real chat DB or calls a real model."""
import sys
import tempfile
from pathlib import Path
from types import ModuleType

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
chatroom = ModuleType('chatroom')
chatroom.get_chatroom_names = lambda: ('试玩用户', '试玩伴侣一', '试玩伴侣二')
sys.modules['chatroom'] = chatroom

from billiards import service
from billiards.routes import install
from billiards.companionship import append_message

tmp = tempfile.TemporaryDirectory(prefix='aions-billiards-qa-')
service._service = service.BilliardsService(Path(tmp.name), companionship=False)
app = FastAPI()
install(app)
app.mount('/entertainment-assets', StaticFiles(directory=str(ROOT / 'entertainment')))
app.mount('/public', StaticFiles(directory=str(ROOT.parent / 'public')))
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')))

@app.get('/playground')
async def lobby():
    return FileResponse(ROOT / 'entertainment' / 'lobby.html')

@app.get('/api/chatroom/config')
async def config():
    return {}

@app.post('/api/qa/autonomous')
async def autonomous():
    game = await service._service.create(['aion', 'connor'], difficulty='low', seed=17)
    await service._service.resume(game['id'], '')
    return game

@app.post('/api/qa/abandon/{sid}')
async def abandon(sid: str):
    game = service._service.get(sid)
    game['status'] = 'finished'
    game['lease'] = None
    service._service.save(game)
    return game

@app.post('/api/qa/ai-turn/{seat}')
async def ai_turn(seat: int):
    assert seat in (0, 1)
    game = await service._service.create(['aion', 'connor'], difficulty='low', seed=17)
    game['state']['turn'] = seat
    game['state']['phase'] = 'aim'
    game['state']['placement'] = None
    service._service.save(game)
    return game

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18769, log_level='warning')
