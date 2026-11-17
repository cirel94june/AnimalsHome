"""Idle startup must not load native media, real image paths must still work."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest

import lazy_media


def test_idle_camera_voice_and_routes_do_not_import_native_media(tmp_path):
    source=textwrap.dedent('''
        import importlib.util, importlib.abc, json, pathlib, sys
        source_root=pathlib.Path(sys.argv[1])
        fixture_root=pathlib.Path(sys.argv[2])
        sys.path.insert(0,str(source_root))
        spec=importlib.util.spec_from_file_location('config',source_root/'config.py')
        config=importlib.util.module_from_spec(spec)
        config.__file__=str(fixture_root/'config.py')
        sys.modules['config']=config
        spec.loader.exec_module(config)
        class Guard(importlib.abc.MetaPathFinder):
            def find_spec(self,fullname,path=None,target=None):
                if fullname.split('.')[0] in {'cv2','numpy'}:
                    raise AssertionError('idle startup imported native media: '+fullname)
        sys.meta_path.insert(0,Guard())
        import camera, voice, phone_camera, routes.phone_camera
        assert 'cv2' not in sys.modules and 'numpy' not in sys.modules
        assert pathlib.Path(config.DATA_DIR).is_relative_to(fixture_root)
        print(json.dumps({'idle_native_media_loaded':False}))
    ''')
    result=subprocess.run([sys.executable,'-c',source,str(Path(__file__).parent),str(tmp_path)],
                          capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stderr[-2000:]
    assert json.loads(result.stdout.strip().splitlines()[-1])=={'idle_native_media_loaded':False}


def test_concurrent_first_use_imports_one_complete_module(monkeypatch):
    calls=[]
    def load(name):
        calls.append(name);time.sleep(.01)
        return SimpleNamespace(value=42)
    monkeypatch.setattr(lazy_media,'import_module',load)
    module=lazy_media._LazyModule('numpy')
    with ThreadPoolExecutor(max_workers=8) as pool:
        values=list(pool.map(lambda _:module.value,range(16)))
    assert values==[42]*16 and calls==['numpy']


def test_failed_import_can_retry_without_publishing_partial_module(monkeypatch):
    calls=[]
    def load(name):
        calls.append(name)
        if len(calls)==1:raise ImportError('fixture unavailable')
        return SimpleNamespace(value=7)
    monkeypatch.setattr(lazy_media,'import_module',load)
    module=lazy_media._LazyModule('cv2')
    with pytest.raises(ImportError):module.value
    assert module.value==7 and calls==['cv2','cv2']


def test_only_two_fixed_modules_supported():
    assert lazy_media.media_module('numpy') is lazy_media.media_module('numpy')
    with pytest.raises(ValueError):lazy_media.media_module('arbitrary_module')
