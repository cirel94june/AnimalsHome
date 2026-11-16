import asyncio
from unittest.mock import AsyncMock
import httpx
import pytest
import image_gen


@pytest.mark.parametrize('stage,error,expected',[
 ('request','timeout','图片服务等候超时'),
 ('download','timeout','下载等候超时'),
 ('download',403,'下载图片失败（403）'),
 ('request',429,'限流或额度不足'),
 ('request',401,'Key 无效'),
 ('save','bad','保存相册失败'),
])
def test_openai_failure_identifies_stage_without_leaking_key(monkeypatch,capsys,stage,error,expected):
 class Client:
  def __init__(self,*a,**kw):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):return False
  async def post(self,*args,**kwargs):
   if stage=='request':
    if error=='timeout':raise httpx.ReadTimeout('SECRET_IN_UPSTREAM_EXCEPTION')
    r=httpx.Response(error,request=httpx.Request('POST','https://relay.invalid/SECRET_IN_URL'))
    r.raise_for_status()
   return httpx.Response(200,json={'data':[{'url':'https://cdn.invalid/SECRET_IN_URL'}]},request=httpx.Request('POST','https://relay.invalid'))
  async def get(self,*args,**kwargs):
   assert 'headers' not in kwargs
   if stage=='download':
    if error=='timeout':raise httpx.ReadTimeout('SECRET_IN_UPSTREAM_EXCEPTION')
    r=httpx.Response(error,request=httpx.Request('GET','https://cdn.invalid/SECRET_IN_URL'));r.raise_for_status()
   return httpx.Response(200,content=b'bad-image',request=httpx.Request('GET','https://cdn.invalid'))
 monkeypatch.setattr(image_gen.httpx,'AsyncClient',Client)
 monkeypatch.setattr(image_gen,'save_generated_image',AsyncMock(side_effect=ValueError('SECRET_IN_UPSTREAM_EXCEPTION')))
 async def run():
  image_gen._failure.set('')
  result=await image_gen._generate_openai_image('TEST',False,'aion','https://relay.invalid/v1','SECRET_KEY','gpt-image-test')
  return result,image_gen.failure_reason()
 result,reason=asyncio.run(run())
 assert result is None and expected in reason
 assert 'SECRET' not in reason+capsys.readouterr().out