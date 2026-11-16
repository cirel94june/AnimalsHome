"""Keep isolated Codex homes on the freshest login for the configured account."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


def _snapshot(path):
    try:
        content=Path(path).read_bytes()
        try:value=json.loads(content)
        except ValueError:value={}
        return (value if isinstance(value,dict) else {}),content
    except OSError:return {},None


def _identity(value):
    tokens=value.get('tokens') or {}
    if (value.get('auth_mode')=='chatgpt' and isinstance(tokens,dict)
            and isinstance(tokens.get('account_id'),str) and tokens['account_id'].strip()
            and isinstance(tokens.get('access_token'),str) and tokens['access_token'].strip()):
        return ('chatgpt',tokens['account_id'])
    if isinstance(value.get('OPENAI_API_KEY'),str) and value['OPENAI_API_KEY'].strip():
        return ('api_key',value['OPENAI_API_KEY'])
    return None


def _freshness(value):
    try:return datetime.fromisoformat(str(value.get('last_refresh') or '').replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,OverflowError):return 0


def prepare_auth_copy(source,destination,peers=()):
    """Canonical login fixes account identity; another account is never borrowed.

    CLI refreshes live in isolated homes too. File mtime is not login freshness.
    No source: preserve existing behavior and do not import a peer's identity.
    """
    source,destination=Path(source),Path(destination)
    if not source.is_file():return
    canonical,selected_content=_snapshot(source);identity=_identity(canonical)
    if selected_content is None:return
    selected=source;freshness=_freshness(canonical)
    if identity is not None:
        for path in (destination,*map(Path,peers)):
            value,content=_snapshot(path)
            if _identity(value)==identity and _freshness(value)>freshness:
                selected,freshness,selected_content=path,_freshness(value),content
    if selected.resolve()!=destination.resolve():
        destination.parent.mkdir(parents=True,exist_ok=True)
        descriptor,name=tempfile.mkstemp(prefix='.auth-',dir=destination.parent)
        temporary=Path(name)
        try:
            with os.fdopen(descriptor,'wb') as output:
                os.chmod(temporary,0o600)
                output.write(selected_content)
                output.flush();os.fsync(output.fileno())
            os.replace(temporary,destination)
        finally:
            temporary.unlink(missing_ok=True)
