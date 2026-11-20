"""Best-effort cache anchors across restarts. Only SHA256 digests, no chat text."""
import hashlib
import json
import os
import re
import tempfile

from config import DATA_DIR

PATH=DATA_DIR/'cache_window_hints.json'
_HINTS=None
LIMIT=200


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()


def load():
    try:
        if PATH.stat().st_size>64*1024:return {}
        values=json.loads(PATH.read_text(encoding='utf-8'))
        if not isinstance(values,dict):return {}
        return {k:v for k,v in list(values.items())[-LIMIT:]
                if isinstance(k,str) and isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',k)
                and re.fullmatch('[a-f0-9]{64}',v)}
    except (OSError,ValueError,RecursionError):return {}


def hints():
    global _HINTS
    if _HINTS is None:_HINTS=load()
    return _HINTS


def find(scope,keys):
    saved=hints().get(digest(scope))
    return next((key for key in keys if digest(key)==saved),None) if saved else None


def remember(scope,start):
    values=hints();key=digest(scope);value=digest(start)
    if values.get(key)==value:return
    values[key]=value
    while len(values)>LIMIT:values.pop(next(iter(values)))
    name=None
    try:
        fd,name=tempfile.mkstemp(dir=PATH.parent,prefix='.cache-window-')
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(values,output,separators=(',',':'))
        os.replace(name,PATH)
    except OSError:
        pass  # A missing hint may cost a cache miss; it must never stop a reply.
    finally:
        if name:
            try:os.unlink(name)
            except OSError:pass
