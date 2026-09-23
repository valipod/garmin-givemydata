#!/usr/bin/env python3
"""Fetch gear per activity into activity_gear. Resumable; --reset starts over.

Gear is Connect metadata, never in the FIT file. Multisport activities carry it on their
children, so those are followed and the result is stored against the parent — local split
legs keep the parent's activity id in their filename.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import garmin_givemydata as g
from garmin_client import GarminClient
from garmin_mcp.db import get_connection, init_db, save_to_db

GEAR = "/gc-api/gear-service/gear/filterGear?activityId=%d"
DETAIL = "/gc-api/activity-service/activity/%d"


def gear_for(client, aid):
    """Gear on the activity, or merged from its legs when it is a multisport parent."""
    rows = client.api_fetch(GEAR % aid) or []
    if rows:
        return rows
    detail = client.api_fetch(DETAIL % aid) or {}
    kids = (detail.get("metadataDTO") or {}).get("childIds") or []
    merged = []
    for kid in kids:
        for item in client.api_fetch(GEAR % kid) or []:
            if item not in merged:
                merged.append(item)
        time.sleep(0.15)
    return merged


def main():
    g.load_env()
    conn = get_connection()
    conn.execute("PRAGMA busy_timeout = 60000")
    init_db(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS activity_gear_checked (activity_id INTEGER PRIMARY KEY)")
    if "--reset" in sys.argv:
        conn.execute("DELETE FROM activity_gear_checked")
        conn.execute("DELETE FROM activity_gear")
        conn.commit()

    seen = {r[0] for r in conn.execute("SELECT activity_id FROM activity_gear_checked")}
    ids = [r[0] for r in conn.execute(
        "SELECT activity_id FROM activity WHERE activity_id IS NOT NULL "
        "ORDER BY start_time_local DESC")]
    todo = [i for i in ids if i not in seen]
    print("activities: %d total, %d to check" % (len(ids), len(todo)), flush=True)
    if not todo:
        return

    client = GarminClient(email=os.environ["GARMIN_EMAIL"], password=os.environ["GARMIN_PASSWORD"],
                          profile_dir=g.PROFILE_DIR, headless=True, session_file=g.SESSION_FILE)
    if not client.login():
        sys.exit("login failed")

    hits = 0
    try:
        for n, aid in enumerate(todo, 1):
            try:
                data = gear_for(client, aid)
            except Exception as exc:
                print("  %d error: %s" % (aid, type(exc).__name__), flush=True)
                continue
            if data:
                save_to_db(conn, "activity_gear", data, cal_date=str(aid))
                hits += 1
            conn.execute("INSERT OR IGNORE INTO activity_gear_checked VALUES (?)", (aid,))
            if n % 25 == 0:
                conn.commit()
                print("  %d/%d checked, %d with gear" % (n, len(todo), hits), flush=True)
            time.sleep(0.2)
    finally:
        conn.commit()
        client.close()
    print("done: %d checked, %d with gear" % (len(todo), hits), flush=True)


if __name__ == "__main__":
    main()
