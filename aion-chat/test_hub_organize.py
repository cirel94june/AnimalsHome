import hub_organize


def test_only_digests_newest_first_and_clipped():
    payload = {"items": [
        {"source_platform": "frontend_dream", "content": "梦", "created_at": "2026-11-09"},
        {"source_platform": "daemon:digest", "content": "旧", "created_at": "2026-11-01"},
        {"source_platform": "daemon:digest", "content": "x" * 9000, "created_at": "2026-11-08"},
    ]}
    rows = hub_organize.pick_digests(payload)
    assert [r["at"] for r in rows] == ["2026-11-08", "2026-11-01"]
    assert len(rows[0]["content"]) == hub_organize.MAX_CHARS


def test_other_shapes_do_not_crash():
    assert hub_organize.pick_digests(None) == []
    assert hub_organize.pick_digests({"items": "x"}) == []
    assert len(hub_organize.pick_digests({"memories": [{"source_platform": "daemon:digest", "content": "a"}]})) == 1
