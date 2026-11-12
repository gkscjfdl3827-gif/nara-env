"""Publish only owned DB files, using optimistic Git updates (never merge SQLite)."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

OWNED = {'nara': ['nara_notices.db'], 'alio': ['alio_notices.db'],
         'eiaa': ['eiaa_notices.db'],
         'all': ['nara_notices.db', 'alio_notices.db', 'eiaa_notices.db']}


def git(*args, env=None, input=None, check=True):
    return subprocess.run(['git', *args], input=input, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, check=check, env=env).stdout.strip()


def blob(ref, path):
    result = git('ls-tree', ref, '--', path)
    return result.split()[2] if result else None


def persist(source):
    paths = [p for p in OWNED[source] if Path(p).exists()]
    for path in paths:
        with sqlite3.connect(path) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError(f'Invalid SQLite state: {path}')
    base = git('rev-parse', 'HEAD')
    original = {p: blob(base, p) for p in paths}
    git('config', 'user.name', 'nara-bot')
    git('config', 'user.email', 'bot@github.com')
    for attempt in range(5):
        git('fetch', 'origin', 'main')
        head = git('rev-parse', 'FETCH_HEAD')
        for path in paths:
            if blob(head, path) != original[path]:
                raise RuntimeError(f'Concurrent writer changed {path}; refusing overwrite')
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'))
            git('read-tree', head, env=env)
            for path in paths:
                git('add', '-f', '--', path, env=env)
            tree = git('write-tree', env=env)
        if tree == git('rev-parse', f'{head}^{{tree}}'):
            print('State unchanged')
            return
        commit = git('commit-tree', tree, '-p', head,
                     input=f'Save {source} notice DB [skip ci]\n')
        result = subprocess.run(['git', 'push', 'origin', f'{commit}:refs/heads/main'],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            print(f'Published {source} state: {commit}')
            return
        print(f'State push raced with another commit; retry {attempt + 1}/5')
    raise RuntimeError('State was not published; restore from workflow artifact')


if __name__ == '__main__':
    persist(sys.argv[1])
