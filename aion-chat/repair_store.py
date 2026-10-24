"""Durable repair conversations and a single-consumer work queue."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def uid():
    return uuid.uuid4().hex


class Conflict(ValueError):
    pass


class RepairStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "repair.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'discussion',
                    plan TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
                    thread_id TEXT NOT NULL DEFAULT '', context_minutes INTEGER NOT NULL DEFAULT 120,
                    created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, who TEXT NOT NULL,
                    text TEXT NOT NULL, attachments TEXT NOT NULL DEFAULT '[]', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL,
                    data TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued', cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS controls (
                    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, text TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, name TEXT NOT NULL,
                    path TEXT NOT NULL, size INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_task ON messages(task_id,id);
                CREATE INDEX IF NOT EXISTS events_task ON events(task_id,id);
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in db.execute("PRAGMA table_info(tasks)")}
            if "proposal_valid" not in columns:
                db.execute("ALTER TABLE tasks ADD COLUMN proposal_valid INTEGER NOT NULL DEFAULT 0")
                db.execute("UPDATE tasks SET proposal_valid=1 WHERE plan!=''")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return dict(row)

    def tasks(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT 100")]

    def create(self, title, context_minutes=120):
        task_id = uid()
        with self.connect() as db:
            db.execute("INSERT INTO tasks(id,title,context_minutes,created,updated) VALUES(?,?,?,?,?)",
                       (task_id, title, context_minutes, time.time(), time.time()))
        return self.task(task_id)

    def message(self, task_id, who, text, attachments=()):
        with self.connect() as db:
            cursor = db.execute("INSERT INTO messages(task_id,who,text,attachments,created) VALUES(?,?,?,?,?)",
                                (task_id, who, text, json.dumps(list(attachments), ensure_ascii=False), time.time()))
            db.execute("UPDATE tasks SET updated=? WHERE id=?", (time.time(), task_id))
            return cursor.lastrowid

    def rename(self, task_id, title):
        with self.connect() as db:
            result = db.execute("UPDATE tasks SET title=?,updated=? WHERE id=?", (title, time.time(), task_id))
            if result.rowcount != 1:
                raise KeyError(task_id)
        return self.task(task_id)

    def messages(self, task_id, after=0):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM messages WHERE task_id=? AND id>? ORDER BY id", (task_id, after))]
        for row in rows:
            row["attachments"] = json.loads(row["attachments"])
        return rows

    def event(self, task_id, kind, data):
        with self.connect() as db:
            return db.execute("INSERT INTO events(task_id,kind,data,created) VALUES(?,?,?,?)",
                              (task_id, kind, json.dumps(data, ensure_ascii=False), time.time())).lastrowid

    def events(self, task_id, after=0):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT 300", (task_id, after))]
        for row in rows:
            row["data"] = json.loads(row["data"])
        return rows

    def active(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE task_id=? AND state IN ('queued','running')", (task_id,)).fetchone()
            if not row:
                return None
            job = dict(row)
            job['can_steer'] = self.can_steer(job)
            return job

    @staticmethod
    def can_steer(job):
        payload = json.loads(job['payload']) if isinstance(job['payload'], str) else job['payload']
        # 只有发给执行者（Lucien）或大家时才能中途补充；只发给出主意的人（小克 / 座位）不行
        return job['kind'] in ('execute', 'inspect', 'plan') or (
            job['kind'] == 'discuss' and payload.get('recipient', 'connor') in ('connor', 'both'))

    def save_plan(self, task_id, plan, revision):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE task_id=? AND state IN ('queued','running')", (task_id,)).fetchone():
                raise Conflict("请先等当前回复结束或停止任务，再修改方案。")
            result = db.execute("UPDATE tasks SET plan=?,proposal_valid=1,revision=revision+1,phase='discussion',updated=? WHERE id=? AND revision=? AND phase!='completed'",
                                (plan, time.time(), task_id, revision))
            if result.rowcount != 1:
                raise Conflict("方案已更新或任务已完成，请刷新后再操作。")
        return self.task(task_id)

    def propose(self, job, plan):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT state,cancel FROM jobs WHERE id=?", (job['id'],)).fetchone()
            if not current or current['state'] != 'running' or current['cancel']:
                return
            db.execute("UPDATE tasks SET plan=?,proposal_valid=1,revision=revision+1,updated=? WHERE id=?",
                       (plan, time.time(), job['task_id']))

    def enqueue(self, task_id, kind, payload):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError(task_id)
            if task["phase"] == "completed":
                raise Conflict("这个任务已验收，请新建任务。")
            if db.execute("SELECT 1 FROM jobs WHERE task_id=? AND state IN ('queued','running')", (task_id,)).fetchone():
                raise Conflict("任务正在进行，可补充给执行者或停止后继续讨论。")
            if kind == "execute":
                if not task["plan"].strip() or not task['proposal_valid'] or payload.get("revision") != task["revision"]:
                    raise Conflict("需要确认当前版本的非空方案。")
                payload = {**payload, "plan": task["plan"]}
                db.execute("UPDATE tasks SET phase='executing' WHERE id=?", (task_id,))
            elif kind in ("discuss", "plan", "inspect"):
                # Any new discussion invalidates a previous approval/acceptance state.
                db.execute("UPDATE tasks SET phase='discussion',proposal_valid=0,revision=revision+1 WHERE id=?", (task_id,))
            else:
                raise ValueError("未知任务类型")
            job_id = uid()
            db.execute("INSERT INTO jobs(id,task_id,kind,payload,created) VALUES(?,?,?,?,?)",
                       (job_id, task_id, kind, json.dumps(payload, ensure_ascii=False), time.time()))
            text = payload.get("text", "")
            if text or payload.get("attachments"):
                db.execute("INSERT INTO messages(task_id,who,text,attachments,created) VALUES(?,?,?,?,?)",
                           (task_id, "user", text, json.dumps(payload.get("attachments", []), ensure_ascii=False), time.time()))
            db.execute("UPDATE tasks SET updated=? WHERE id=?", (time.time(), task_id))
        return job_id

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running' WHERE id=?", (row["id"],))
                job = dict(row)
                job["payload"] = json.loads(job["payload"])
                return job

    def finish(self, job, state="done"):
        with self.connect() as db:
            row = db.execute("SELECT cancel FROM jobs WHERE id=?", (job["id"],)).fetchone()
            if state == "done" and row and row[0]:
                state = "interrupted"
            db.execute("UPDATE jobs SET state=? WHERE id=?", (state, job["id"]))
            phase = "paused" if state != "done" else ("review" if job["kind"] in ("execute", "inspect") else "discussion")
            task = db.execute('SELECT proposal_valid FROM tasks WHERE id=?', (job['task_id'],)).fetchone()
            if state == 'done' and job['kind'] == 'inspect' and task['proposal_valid']:
                phase = 'discussion'
            if state == 'done' and job['kind'] == 'discuss':
                delivered = any(json.loads(r[0]).get('job') == job['id'] for r in db.execute(
                    "SELECT data FROM events WHERE task_id=? AND kind='delivery'", (job['task_id'],)))
                if delivered and not task['proposal_valid']:
                    phase = 'review'
            db.execute("UPDATE tasks SET phase=?,updated=? WHERE id=?", (phase, time.time(), job["task_id"]))

    def stop(self, task_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE jobs SET cancel=1 WHERE task_id=? AND state IN ('queued','running')", (task_id,))
            db.execute("UPDATE jobs SET state='interrupted' WHERE task_id=? AND state='queued'", (task_id,))
            db.execute("UPDATE tasks SET phase='paused' WHERE id=? AND phase!='completed'", (task_id,))

    def cancelled(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT cancel FROM jobs WHERE id=?", (job_id,)).fetchone()
            return not row or bool(row[0])

    def steer(self, task_id, text):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM jobs WHERE task_id=? AND state='running' AND cancel=0", (task_id,)).fetchone()
            if not job or not self.can_steer(job):
                raise Conflict("当前没有可补充的执行任务，请通过讨论发送。")
            if job['kind'] != 'execute':
                db.execute('UPDATE tasks SET proposal_valid=0,revision=revision+1 WHERE id=?', (task_id,))
            db.execute("INSERT INTO controls(job_id,text) VALUES(?,?)", (job[0], text))
            db.execute("INSERT INTO messages(task_id,who,text,created) VALUES(?,?,?,?)", (task_id, "user", text, time.time()))

    def recover(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT task_id FROM jobs WHERE state='running'")]
            db.execute("UPDATE jobs SET state='interrupted' WHERE state='running'")
            for task_id in ids:
                db.execute("UPDATE tasks SET phase='paused' WHERE id=?", (task_id,))
        for task_id in ids:
            self.event(task_id, "status", {"text": "维修进程曾中断。已有修改未撤销，请检查记录后继续。"})

    def runtime(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO runtime(key,value) VALUES(?,?)", (key, str(value)))
            row = db.execute("SELECT value FROM runtime WHERE key=?", (key,)).fetchone()
            return row[0] if row else None


def default_store():
    from config import DATA_DIR
    return RepairStore(DATA_DIR / "repair-room")
