#!/usr/bin/env python3
"""
Fetches the latest trailers from the YouTube channel feeds in channels.csv and
writes trailers.json. Runs on GitHub Actions, so YouTube sees GitHub's servers
rather than your home server. Standard library only.

Keeps trailers from earlier runs that are still inside the age window, so a
run where YouTube refuses some feeds does not empty the list.
"""
import csv
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CHANNELS = os.path.join(HERE, "channels.csv")
OUT = os.path.join(HERE, "trailers.json")
MAX_AGE = timedelta(days=180)
PER_CHANNEL = 3
COUNT = 12
UA = "trailarr-feed/1.0 (personal use, low volume)"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

# Same filter rules as offshoot.py.
INCLUDE = re.compile(r"trailer|teaser|予告|ティーザー|(?<![A-Za-z])PV(?![A-Za-z])", re.I)
EXCLUDE = re.compile(
    r"interview|behind the scenes|making of|メイキング|特別映像|本編映像|talk|recap|"
    r"press conference|q&a|reaction|logo|ad-free|#shorts|\bclip\b",
    re.I,
)


def load_channels(path):
    ids, seen = [], set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            lower = {k.strip().lower(): v for k, v in row.items() if k}
            cid = (lower.get("channel_id") or "").strip()
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
    return ids


def fetch_feed(cid, attempts=3):
    url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + cid
    last = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 + attempt * 3)
    raise last


def parse_feed(xml_bytes, now):
    root = ET.fromstring(xml_bytes)
    author = root.find("a:author/a:name", NS)
    channel = author.text if author is not None else ""
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        title = (e.findtext("a:title", namespaces=NS) or "").strip()
        pub = (e.findtext("a:published", namespaces=NS) or "").replace("Z", "+00:00")
        if not vid or not pub:
            continue
        published = datetime.fromisoformat(pub)
        if not INCLUDE.search(title) or EXCLUDE.search(title):
            continue
        if now - published > MAX_AGE:
            continue
        out.append({
            "video_id": vid,
            "title": title,
            "channel": channel,
            "published": published,
            "url": "https://www.youtube.com/watch?v=" + vid,
            "thumb": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid,
        })
    return out[:PER_CHANNEL]


def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for t in rows:
        try:
            t["published"] = datetime.fromisoformat(t["published"])
        except (KeyError, ValueError, TypeError):
            continue
        out.append(t)
    return out


def main():
    now = datetime.now(timezone.utc)
    channels = load_channels(CHANNELS)
    fresh, failed = [], 0
    for cid in channels:
        try:
            fresh += parse_feed(fetch_feed(cid), now)
        except Exception as e:
            failed += 1
            print("feed %s failed: %s" % (cid, e))
        time.sleep(0.3)
    ok = len(channels) - failed
    print("%d channels, %d ok, %d failed, %d trailers this run" % (len(channels), ok, failed, len(fresh)))
    if ok == 0:
        print("no feeds worked; leaving trailers.json unchanged")
        sys.exit(1)

    fresh_ids = {t["video_id"] for t in fresh}
    kept = [t for t in load_previous()
            if t["video_id"] not in fresh_ids and now - t["published"] <= MAX_AGE]
    merged = sorted(fresh + kept, key=lambda t: t["published"], reverse=True)[:COUNT]

    serial = [dict(t, published=t["published"].isoformat()) for t in merged]
    text = json.dumps(serial, ensure_ascii=False, indent=1) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print("wrote %d trailers to trailers.json" % len(serial))


if __name__ == "__main__":
    main()
