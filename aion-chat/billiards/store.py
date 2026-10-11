"""Small atomic JSON saves, separate from the home's chat database."""
import json
import os
import re
from pathlib import Path


class SessionStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, sid: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{32}', sid):
            raise ValueError('球局编号无效')
        return self.root / (sid + '.json')

    def load(self, sid: str) -> dict:
        path = self.path(sid)
        if not path.is_file():
            raise ValueError('没有找到这局球')
        return json.loads(path.read_text(encoding='utf-8'))

    def save(self, game: dict):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(game['id'])
        temp = path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(game, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def all(self) -> list[dict]:
        games = []
        if self.root.exists():
            for path in self.root.glob('*.json'):
                try:
                    games.append(self.load(path.stem))
                except (ValueError, OSError):
                    continue
        return sorted(games, key=lambda g: g['updated_at'], reverse=True)
