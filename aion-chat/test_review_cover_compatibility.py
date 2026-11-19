"""URL-parser counterexample, no browser or network required."""
from pathlib import Path
import subprocess


def test_cover_relative_allowlist_cannot_normalize_to_another_origin():
    script=r'''
      const assert=require('node:assert/strict');
      const m=require('./static/music-share.js');
      for(const control of ['\n','\r','\t']) {
        const raw='/'+control+'/evil.invalid/art.jpg';
        assert.equal(m.knownCover({id:123,cover:raw}),'',
          'knownCover accepted a relative-looking URL normalized by browsers to '+new URL(raw,'https://app.invalid').origin);
        assert.equal(m.coverUrl({source_type:'local',cover_url:raw}),'');
      }
    '''
    result=subprocess.run(['node','-e',script],cwd=Path(__file__).parent,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
