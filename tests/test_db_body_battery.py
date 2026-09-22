"""Regression tests for body_battery aggregate backfill from daily_summary.

Issue #13: the body_battery_events endpoint only returns time-series data,
so the body_battery table's aggregate columns (highest/lowest/charged/etc.)
were always NULL even though the same values lived in daily_summary.
"""

import json

from garmin_mcp.db import (
    backfill_body_battery_from_daily_summaries,
    upsert_body_battery,
    upsert_body_battery_from_daily_summary,
    upsert_daily_summary,
)


def _ds(date: str, **bb) -> dict:
    """Build a daily_summary record with body_battery aggregate fields."""
    rec = {"calendarDate": date}
    rec.update(
        {
            "bodyBatteryChargedValue": bb.get("charged"),
            "bodyBatteryDrainedValue": bb.get("drained"),
            "bodyBatteryHighestValue": bb.get("highest"),
            "bodyBatteryLowestValue": bb.get("lowest"),
            "bodyBatteryMostRecentValue": bb.get("most_recent"),
            "bodyBatteryAtWakeTime": bb.get("at_wake"),
            "bodyBatteryDuringSleep": bb.get("during_sleep"),
        }
    )
    return rec


def _bb_row(conn, date: str) -> dict:
    return dict(conn.execute("SELECT * FROM body_battery WHERE calendar_date = ?", (date,)).fetchone() or {})


class TestUpsertBodyBatteryFromDailySummary:
    def test_inserts_new_row_with_aggregates(self, temp_db):
        upsert_body_battery_from_daily_summary(
            temp_db, _ds("2026-05-01", highest=99, lowest=47, charged=53, drained=54, at_wake=99)
        )
        row = _bb_row(temp_db, "2026-05-01")
        assert row["highest"] == 99
        assert row["lowest"] == 47
        assert row["charged"] == 53
        assert row["drained"] == 54
        assert row["at_wake"] == 99

    def test_updates_existing_row_without_clobbering_raw_json(self, temp_db):
        # Simulate the events endpoint having populated raw_json + a row
        # but NOT the aggregate columns
        events_record = {"bodyBattery": {"data": [["2026-05-01T22:00", 50, 1, 3]]}}
        upsert_body_battery(temp_db, events_record, cal_date="2026-05-01")
        before = _bb_row(temp_db, "2026-05-01")
        assert before["highest"] is None
        assert before["raw_json"] is not None

        # Now run the daily_summary path
        upsert_body_battery_from_daily_summary(temp_db, _ds("2026-05-01", highest=99, lowest=47, charged=53))

        after = _bb_row(temp_db, "2026-05-01")
        assert after["highest"] == 99
        assert after["lowest"] == 47
        assert after["charged"] == 53
        # raw_json from events endpoint must survive
        assert after["raw_json"] == before["raw_json"]
        assert "bodyBattery" in json.loads(after["raw_json"])

    def test_skips_when_all_values_are_none(self, temp_db):
        # No body_battery fields in the daily_summary — should not insert
        upsert_body_battery_from_daily_summary(temp_db, {"calendarDate": "2026-05-01"})
        assert _bb_row(temp_db, "2026-05-01") == {}

    def test_skips_when_no_calendar_date(self, temp_db):
        upsert_body_battery_from_daily_summary(temp_db, {"bodyBatteryHighestValue": 99})
        assert temp_db.execute("SELECT COUNT(*) FROM body_battery").fetchone()[0] == 0

    def test_partial_daily_summary_does_not_clobber_existing_aggregates(self, temp_db):
        # Realistic: a sync sets {highest, lowest, charged}. A later sync of
        # the same date returns a partial daily_summary that only has highest.
        # The other fields must not be wiped to NULL.
        upsert_body_battery_from_daily_summary(
            temp_db, _ds("2026-05-01", highest=99, lowest=47, charged=53, at_wake=99)
        )
        upsert_body_battery_from_daily_summary(temp_db, _ds("2026-05-01", highest=80))
        row = _bb_row(temp_db, "2026-05-01")
        assert row["highest"] == 80  # latest value wins
        assert row["lowest"] == 47  # preserved
        assert row["charged"] == 53  # preserved
        assert row["at_wake"] == 99  # preserved


class TestUpsertBodyBatteryEventsOrdering:
    """Real sync order is daily_summary FIRST, body_battery_events AFTER.
    The events upsert must not wipe the aggregates that daily_summary just
    populated (issue #13 + Copilot review on PR #40).
    """

    def test_events_payload_after_daily_summary_preserves_aggregates(self, temp_db):
        # Step 1: daily_summary populates aggregates
        upsert_body_battery_from_daily_summary(
            temp_db, _ds("2026-05-01", highest=99, lowest=47, charged=53, drained=54, at_wake=99)
        )
        # Step 2: events endpoint fires with time-series data, no aggregates
        events_payload = {"bodyBattery": {"data": [["2026-05-01T22:00", 50, 1, 3]]}}
        upsert_body_battery(temp_db, events_payload, cal_date="2026-05-01")

        row = _bb_row(temp_db, "2026-05-01")
        # Aggregates from daily_summary must survive the events upsert
        assert row["highest"] == 99
        assert row["lowest"] == 47
        assert row["charged"] == 53
        assert row["drained"] == 54
        assert row["at_wake"] == 99
        # raw_json now reflects the events payload (the time-series is what
        # the body_battery row's raw_json is meant to hold)
        assert "bodyBattery" in json.loads(row["raw_json"])


class TestBackfillBodyBatteryFromDailySummaries:
    def test_inserts_missing_rows_and_fills_existing_nulls(self, temp_db):
        # Three daily_summary rows with body_battery data
        upsert_daily_summary(temp_db, _ds("2026-05-01", highest=99, lowest=47, charged=53, drained=54))
        upsert_daily_summary(temp_db, _ds("2026-05-02", highest=80, lowest=38, charged=56, drained=32))
        upsert_daily_summary(temp_db, _ds("2026-05-03", highest=84, lowest=38, charged=37, drained=51))
        # Wipe the body_battery rows that upsert_daily_summary just created
        # to simulate the pre-fix state where they never existed
        temp_db.execute("DELETE FROM body_battery")
        # Plus a body_battery row from the events endpoint with NULL aggregates
        upsert_body_battery(
            temp_db,
            {"bodyBattery": {"data": [["2026-05-02T22:00", 50, 1, 3]]}},
            cal_date="2026-05-02",
        )
        before = _bb_row(temp_db, "2026-05-02")
        assert before["highest"] is None

        backfill_body_battery_from_daily_summaries(temp_db)

        # All three dates should now have aggregates populated
        for date, expected_highest in (("2026-05-01", 99), ("2026-05-02", 80), ("2026-05-03", 84)):
            row = _bb_row(temp_db, date)
            assert row["highest"] == expected_highest, f"date {date}: highest={row.get('highest')}"

        # Events-endpoint raw_json on 2026-05-02 must have survived
        row_2 = _bb_row(temp_db, "2026-05-02")
        assert row_2["raw_json"] is not None
        assert "bodyBattery" in json.loads(row_2["raw_json"])

    def test_does_not_overwrite_existing_aggregate(self, temp_db):
        # daily_summary has highest=99, but body_battery already has highest=42
        # (e.g. set by a different code path). The backfill must keep 42.
        upsert_daily_summary(temp_db, _ds("2026-05-01", highest=99, lowest=47, charged=53))
        temp_db.execute("UPDATE body_battery SET highest = 42 WHERE calendar_date = '2026-05-01'")
        backfill_body_battery_from_daily_summaries(temp_db)
        assert _bb_row(temp_db, "2026-05-01")["highest"] == 42

    def test_skips_dates_without_body_battery_in_daily_summary(self, temp_db):
        # daily_summary row with NO body battery data — backfill should not
        # touch body_battery for that date
        upsert_daily_summary(temp_db, {"calendarDate": "2026-05-01", "totalSteps": 10000})
        backfill_body_battery_from_daily_summaries(temp_db)
        assert temp_db.execute("SELECT COUNT(*) FROM body_battery").fetchone()[0] == 0

    def test_idempotent(self, temp_db):
        upsert_daily_summary(temp_db, _ds("2026-05-01", highest=99, charged=53))
        backfill_body_battery_from_daily_summaries(temp_db)
        first = _bb_row(temp_db, "2026-05-01")
        backfill_body_battery_from_daily_summaries(temp_db)
        second = _bb_row(temp_db, "2026-05-01")
        assert first == second
