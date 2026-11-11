import io
import json
import os
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

import offsite_backup as ob

PASS = "correct horse battery"


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "CONFIG_PATH", tmp_path / "cfg.json")
    monkeypatch.setattr(ob, "STATUS_PATH", tmp_path / "status.json")


CFG = {"account_id": "a" * 32, "access_key_id": "K" * 32, "secret_access_key": "SECRETVALUE", "bucket": "animalshome-backup", "passphrase": PASS}


def seal(data):
    out = io.BytesIO()
    ob.encrypt_stream(io.BytesIO(data), out, PASS)
    return out.getvalue()


def test_encrypt_roundtrip_including_block_boundaries():
    for size in (0, 1, ob.CHUNK_SIZE, ob.CHUNK_SIZE + 1, 2 * ob.CHUNK_SIZE):
        data = os.urandom(size)
        out = io.BytesIO()
        ob.decrypt_stream(io.BytesIO(seal(data)), out, PASS)
        assert out.getvalue() == data


def test_wrong_password_tamper_and_truncation_fail():
    sealed = seal(os.urandom(ob.CHUNK_SIZE + 10))
    with pytest.raises(ob.BackupError):
        ob.decrypt_stream(io.BytesIO(sealed), io.BytesIO(), "wrong password here!")
    flipped = bytearray(sealed)
    flipped[-3] ^= 1
    with pytest.raises(ob.BackupError):
        ob.decrypt_stream(io.BytesIO(bytes(flipped)), io.BytesIO(), PASS)
    first_block = len(ob.MAGIC) + 23 + 4 + ob.CHUNK_SIZE + 16
    with pytest.raises(ob.BackupError):
        ob.decrypt_stream(io.BytesIO(sealed[:first_block]), io.BytesIO(), PASS)


def test_plaintext_is_not_in_the_output():
    assert b"PRIVATE_CANARY" not in seal(b"PRIVATE_CANARY" * 100)


def test_snapshot_skips_secrets_and_copies_live_database(tmp_path):
    src = tmp_path / "data"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.txt").write_text("x")
    (src / ob.CONFIG_PATH.name).write_text("secret")
    conn = sqlite3.connect(src / "live.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("create table t(a)")
    conn.execute("insert into t values (1)")
    conn.commit()
    ob.snapshot(src, tmp_path / "copy")
    conn.close()
    assert not (tmp_path / "copy" / ob.CONFIG_PATH.name).exists()
    assert not list((tmp_path / "copy").glob("*-wal"))
    copied = sqlite3.connect(tmp_path / "copy" / "live.db")
    assert copied.execute("select count(*) from t").fetchone()[0] == 1
    copied.close()


class FakeStore:
    def __init__(self):
        self.files = {}

    def put(self, key, path):
        self.files[key] = path.read_bytes()

    def list_backups(self):
        return sorted(self.files)

    def get(self, key, path):
        path.write_bytes(self.files[key])

    def delete(self, key):
        del self.files[key]


def make_data(tmp_path):
    src = tmp_path / "data"
    src.mkdir()
    conn = sqlite3.connect(src / "chat.db")
    conn.execute("create table t(a)")
    conn.commit()
    conn.close()
    (src / "note.txt").write_text("hello")
    return src


def test_backup_then_drill(tmp_path):
    ob._write_private(ob.CONFIG_PATH, CFG)
    store = FakeStore()
    lounge = tmp_path / "lounge"
    lounge.mkdir()
    conn = sqlite3.connect(lounge / "visitor-lounge.sqlite3")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("create table v(a)")
    conn.execute("insert into v values (1)")
    conn.commit()
    result = ob.run_backup(store, [(make_data(tmp_path), "data"), (lounge, "lounge")])
    conn.close()
    assert ob._NAME.match(result["name"]) and PASS.encode() not in store.files[result["name"]]
    drill = ob.run_drill(store)
    assert drill["databases"] == 2 and drill["bad"] == 0


def test_archive_keeps_both_folders(tmp_path):
    import tarfile
    tree = tmp_path / "tree"
    (tree / "data").mkdir(parents=True)
    (tree / "lounge").mkdir()
    (tree / "data" / "a.txt").write_text("a")
    (tree / "lounge" / "b.sqlite3").write_bytes(b"")
    ob.make_archive(tree, tmp_path / "x.tar.gz")
    with tarfile.open(tmp_path / "x.tar.gz") as tar:
        names = tar.getnames()
    assert "data/a.txt" in names and "lounge/b.sqlite3" in names


def test_sources_include_lounge_when_present(tmp_path, monkeypatch):
    base = tmp_path / "app" / "aion-chat"
    lounge = tmp_path / "app" / "AionsHome-Visitor-Lounge" / "data"
    lounge.mkdir(parents=True)
    base.mkdir(parents=True)
    monkeypatch.setattr(ob, "BASE_DIR", base)
    monkeypatch.setattr(ob, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    assert [name for _, name in ob.sources()] == ["data", "lounge"]


def test_prune_keeps_newest():
    store = FakeStore()
    for i in range(16):
        store.files[f"aionshome/202611{i + 1:02d}T000000Z.tar.gz.enc"] = b""
    assert ob.prune(store) == 2
    assert min(store.files) == "aionshome/20261103T000000Z.tar.gz.enc"


def test_list_ignores_foreign_objects():
    xml = (b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Contents><Key>aionshome/20261101T000000Z.tar.gz.enc</Key></Contents>'
           b'<Contents><Key>aionshome/keepme.txt</Key></Contents></ListBucketResult>')

    class Client:
        def request(self, *a, **k):
            import httpx
            return httpx.Response(200, content=xml)
    assert ob.R2(CFG, Client()).list_backups() == ["aionshome/20261101T000000Z.tar.gz.enc"]


def test_config_validation_and_merge():
    merged = ob.merge_config({**CFG}, {"bucket": "", "passphrase": ""})
    assert merged["passphrase"] == PASS
    for bad in ({"account_id": "x"}, {"bucket": "../evil"}, {"passphrase": "short"}, {"access_key_id": "a/b"}):
        with pytest.raises(HTTPException):
            ob.merge_config({}, bad)


def test_public_state_never_contains_secrets():
    ob._write_private(ob.CONFIG_PATH, CFG)
    text = json.dumps(ob.public_state())
    assert "SECRETVALUE" not in text and PASS not in text and "K" * 32 not in text and "a" * 32 not in text


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 Unix 权限位")
def test_config_file_is_private():
    ob._write_private(ob.CONFIG_PATH, CFG)
    assert (ob.CONFIG_PATH.stat().st_mode & 0o777) == 0o600


def test_errors_do_not_leak_secrets(monkeypatch):
    ob._write_private(ob.CONFIG_PATH, CFG)

    def boom(*a, **k):
        raise RuntimeError("SECRETVALUE")
    monkeypatch.setattr(ob, "snapshot", boom)
    monkeypatch.setattr(ob, "R2", lambda cfg: FakeStore())
    result = ob.backup_now()
    assert result["ok"] is False and "SECRETVALUE" not in json.dumps(result) and "SECRETVALUE" not in ob.STATUS_PATH.read_text()


def test_schedule():
    day = datetime(2026, 11, 9, 5, 0, tzinfo=ob.BEIJING)
    assert ob.due(day, None, True)
    assert not ob.due(day, None, False)
    assert not ob.due(datetime(2026, 11, 9, 3, 0, tzinfo=ob.BEIJING), None, True)
    assert not ob.due(day, day.timestamp() - 60, True)
    assert ob.due(day, day.timestamp() - 86400, True)


def test_signature_is_stable_and_uses_utc_day():
    now = datetime(2026, 11, 9, 1, 2, 3, tzinfo=timezone.utc)
    one = ob.auth_headers("GET", "h", "/b", {"list-type": "2"}, "AK", "SK", now)
    two = ob.auth_headers("GET", "h", "/b", {"list-type": "2"}, "AK", "SK", now)
    assert one == two and "AK/20261109/auto/s3/aws4_request" in one["Authorization"] and "SK" not in one["Authorization"].split("Signature=")[1][:2]
