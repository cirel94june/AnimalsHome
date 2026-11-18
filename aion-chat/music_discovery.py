"""A search continuation can select only IDs actually returned by NetEase."""
import json
import re

PICKS=re.compile(r'\[MUSIC_(PICK|SAVE)\s*[:：]\s*(\d+)\s*\]',re.I)
INSTRUCTION=('\n本轮是网易云曲库找歌，请自己挑选合适的真实结果，自然说明原因。'
 '想点播并收进自己的歌单，用[MUSIC_PICK:结果中的歌曲ID]；只收藏不点播用[MUSIC_SAVE:结果中的歌曲ID]。'
 '一轮最多3首，只能选这次搜索结果里存在的ID。没有合适结果就坦白说明，不编造。'
 '不要使用其他动作指令，不要再搜索，不要声称已经成功，系统会实际执行并给出结果。')


def extract_queries(text):
    pattern=re.compile(r'\[MUSIC_SEARCH\s*[:：]\s*([^\]]+)\]',re.I)
    queries=['MUSIC_SEARCH:'+m[1].strip()[:160] for m in pattern.finditer(text)][:3]
    return pattern.sub('',text),queries


def launch_background_search(queries,target,conv_id,sender,model_key):
    from generation_control import spawn_generation_task
    if target.get('type')=='chatroom':
        from routes.chatroom import _chatroom_web_search
        task=_chatroom_web_search(target['room_id'],sender,model_key,{'searches':queries,'extracts':[]})
    else:
        if sender!='aion':
            # Legacy private-conversation rows belong only to Aion. Other actors
            # need their own chatroom target; never borrow Aion's persona/history.
            return False
        from routes.chat import perform_web_search_check
        task=perform_web_search_check(conv_id,model_key,queries,[])
    spawn_generation_task(task)


def music_only(searches,extracts):
    return bool(searches) and not extracts and all(re.match(r'^MUSIC_SEARCH\s*[：:]',q,re.I) for q in searches)


async def finish(text,contexts,*,actor,source_type,source_id,message_id,success=True,expected_generation=None):
    from capabilities import is_capability_enabled
    from web_search import strip_untrusted_reply
    from music_station import record_music_request
    from music_personal import add
    from netease_account import load
    from music_actions import run_sync, _EXAMPLES
    from music import get_audio_url
    import actors
    cfg=load();generation=cfg['generation']
    catalog={}
    for context in contexts:
        if '\nMUSIC_CATALOG\n' not in context:continue
        try:
            values=json.loads(context.split('\nMUSIC_CATALOG\n',1)[1])
            for s in values[:8]:catalog[int(s['id'])]=s
        except (ValueError,TypeError,KeyError):continue
    picks=[];seen=set()
    examples=[match.span() for match in _EXAMPLES.finditer(text)]
    for match in PICKS.finditer(text):
        if any(start<=match.start()<end for start,end in examples):continue
        song_id=int(match[2])
        if song_id in catalog and song_id not in seen:
            picks.append((match[1].upper(),catalog[song_id]));seen.add(song_id)
        if len(picks)>=3:break
    cleaned=strip_untrusted_reply(PICKS.sub('',text))
    if not success or not is_capability_enabled('music') or (expected_generation is not None and generation!=expected_generation):return cleaned,[]
    attachments=[];receipts=[]
    for index,(op,song) in enumerate(picks):
        if not is_capability_enabled('music') or load()['generation']!=generation:break
        if op=='SAVE':
            result=await add([song],actor,f'discovery:{message_id}:{index}',generation=generation)
            receipts.append(result['message']);continue
        try:audio_url=await run_sync(get_audio_url,song['id'])
        except Exception:audio_url=None
        if not is_capability_enabled('music') or load()['generation']!=generation:break
        attachments.append({'type':'music',**song,'audio_url':audio_url})
        await record_music_request(song,actor,actors.display_name(actor),source_type,source_id,message_id,account_generation=generation)
    if not is_capability_enabled('music') or load()['generation']!=generation:
        attachments=[];receipts=[]
    return (cleaned+'\n\n'+'\n'.join(receipts)).strip(),attachments
