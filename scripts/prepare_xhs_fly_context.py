"""Export only reviewed build sources; never upload the application's data/.git."""
from pathlib import Path
import json
import tempfile

ROOT=Path(__file__).resolve().parent.parent
FILES=('deploy/fly-xhs/Dockerfile','deploy/fly-xhs/requirements.txt','deploy/fly-xhs/entrypoint.sh',
       'aion-chat/xhs_browser_service.py','aion-chat/xhs_fly_service.py',
       'aion-chat/xhs_lite_worker.js','aion-chat/xhs_lite_worker_runner.mjs','deploy/fly-xhs/fly.toml')


def prepare():
    target=Path(tempfile.mkdtemp(prefix='animalshome-xhs-build-'))
    for name in FILES:
        destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text((ROOT/name).read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
    (target/'.dockerignore').write_text((ROOT/'deploy/fly-xhs/Dockerfile.dockerignore').read_text(encoding='utf-8'),encoding='utf-8',newline='\n')
    return target


if __name__=='__main__':print(json.dumps({'context':str(prepare()),'source_files':len(FILES)}))
