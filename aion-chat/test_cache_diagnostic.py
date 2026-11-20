import ai_providers as provider


def test_cache_diagnostic_distinguishes_missing_zero_and_hit_without_secrets(monkeypatch):
    monkeypatch.setattr(provider,'_CACHE_OBSERVED',{})
    cfg={'provider':'custom_openai','base_url':'https://relay.invalid/v1','model':'test',
         'api_key':'do-not-return-this','cache_marker':True}
    assert provider.relay_cache_diagnostic(cfg)['calls']==0
    provider._observe_relay_cache(cfg,{'prompt_tokens':100,'raw':{}},True)
    provider._observe_relay_cache(cfg,{'prompt_tokens':100,'raw':{'cached_tokens':0}},True)
    provider._observe_relay_cache(cfg,{'prompt_tokens':100,'raw':{'cached_tokens':60}},True)
    status=provider.relay_cache_diagnostic(cfg)
    assert (status['calls'],status['reported'],status['hits'])==(3,2,1)
    assert 'do-not-return-this' not in str(status) and 'relay.invalid' not in str(status)
    assert provider.relay_cache_diagnostic({**cfg,'api_key':'another'})['calls']==0


def test_diagnostic_reports_compatibility_fallback_only_for_same_account(monkeypatch):
    cfg={'provider':'custom_openai','base_url':'https://relay.invalid/v1','model':'test','api_key':'key'}
    key=provider._relay_feature_key('https://relay.invalid/v1/chat/completions','test','key')
    monkeypatch.setattr(provider,'_NO_CACHE_MARKER',{key})
    monkeypatch.setattr(provider,'_NO_LONG_CACHE',{key})
    monkeypatch.setattr(provider,'_NO_STREAM_USAGE',{key})
    status=provider.relay_cache_diagnostic(cfg)
    assert status['marker_rejected'] and status['long_cache_rejected'] and status['stream_usage_rejected']
    assert provider.relay_cache_diagnostic({**cfg,'api_key':'other'})['marker_rejected'] is False
    assert provider.relay_cache_diagnostic({'provider':'codex_cli'}) is None
