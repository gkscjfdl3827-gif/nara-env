"""Naver Sports Doosan team news -> a dedicated ntfy topic."""
import argparse
import html
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import json
from pathlib import Path

TOPIC = "doosan-bears-news-dsu-7c92a6e4"
API = "https://api-gw.sports.naver.com/news/articles/kbaseball"


def collect(session, now):
    articles = {}
    snapshot = os.environ.get("DOOSAN_NEWS_INPUT")
    if snapshot:
        rows = json.loads(Path(snapshot).read_text(encoding="utf-8-sig"))
        if not isinstance(rows, list):
            raise ValueError("Unexpected snapshot")
        for row in rows:
            key = str(row["oid"]) + ":" + str(row["aid"])
            if not row.get("title") or not row.get("dateTime"):
                raise ValueError("Incomplete article")
            articles[key] = row
        print(f"Doosan: validated {len(articles)} articles from Naver cloud snapshot")
        return sorted(articles.items(), key=lambda pair: pair[1]["dateTime"])
    for offset in range(3):
        date = (now - timedelta(days=offset)).strftime("%Y%m%d")
        page = 1
        while True:
            response = session.get(API, params={"team": "OB", "sort": "latest",
                "date": date, "page": page, "pageSize": 40}, timeout=25)
            response.raise_for_status()
            data = response.json()
            if data.get("success") is not True:
                raise RuntimeError("Naver Sports request unsuccessful")
            result = data["result"]
            rows = result["newsList"]
            if not isinstance(rows, list):
                raise ValueError("Unexpected newsList")
            for row in rows:
                key = str(row["oid"]) + ":" + str(row["aid"])
                if not row.get("title") or not row.get("dateTime"):
                    raise ValueError("Incomplete article")
                articles[key] = row
            if page * 40 >= int(result["totalCount"]):
                break
            if not rows or page >= 100:
                raise RuntimeError("Incomplete pagination")
            page += 1
    return sorted(articles.items(), key=lambda pair: pair[1]["dateTime"])


def run(db_path="doosan_news.db", dry_run=False, session=None, now=None):
    session = session or requests.Session()
    session.headers.update({"Referer": "https://m.sports.naver.com/"})
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    articles = collect(session, now)
    if dry_run:
        print(f"Doosan: fetched {len(articles)} articles; no messages or state written")
        return 0
    topic = os.environ.get("DOOSAN_NTFY_TOPIC") or TOPIC
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        if not db.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
            db.executemany("INSERT OR IGNORE INTO sent VALUES (?)", [(key,) for key, _ in articles])
            db.execute("INSERT INTO metadata VALUES ('initialized', ?)", (now.isoformat(),))
            print(f"Doosan: initialized with {len(articles)} existing articles (no historical alerts)")
            return 0
        # Recover successful sends if the previous runner could not push its DB.
        recent = session.get(f"https://ntfy.sh/{topic}/json", params={"poll": "1", "since": "24h"}, timeout=25)
        recent.raise_for_status()
        import json
        recent_links = {item.get("click") for line in recent.text.splitlines()
                        if (item := json.loads(line)).get("event") == "message"}
        count = 0
        for key, article in articles:
            if db.execute("SELECT 1 FROM sent WHERE id=?", (key,)).fetchone():
                continue
            url = f'https://m.sports.naver.com/kbaseball/article/{article["oid"]}/{article["aid"]}'
            if url not in recent_links:
                response = session.post("https://ntfy.sh", json={"topic": topic,
                    "title": "[두산베어스] " + html.unescape(article["title"]),
                    "message": f'{article.get("sourceName", "")} · {article["dateTime"]}\n{url}',
                    "click": url, "tags": ["baseball"], "priority": 4}, timeout=25)
                response.raise_for_status()
                count += 1
            db.execute("INSERT OR IGNORE INTO sent VALUES (?)", (key,))
            db.commit()
        print(f"Doosan: sent {count} new article alerts")
        return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)

