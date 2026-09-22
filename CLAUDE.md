# garmin-givemydata (Linux/WSL install)

Fork of `nrvim/garmin-givemydata`, migrated off Windows on 2026-09-22. Upstream README
covers usage; this file covers what is specific to this install.

## Running

The virtualenv is `.venv`, not `venv`. Run from the repo root — `_get_data_dir()` resolves
`DATA_DIR` from the cwd, so running from elsewhere puts the database somewhere else.

```bash
cd /var/local/sources/others/garmin && .venv/bin/python garmin_givemydata.py --days 7
```

Pin `mcp` below 3 (`requirements.txt`). The code was ported to the v2 API in `1179a50`;
it will not run against v1.

## Do not break the Windows Chrome bridge

This machine has no GUI. Chrome is installed in WSL only so SeleniumBase can drive it, and
the client spawns Xvfb when `DISPLAY` is empty (`garmin_client/client.py:298`). Nothing is
ever displayed.

Interactive browsing still goes to Windows Chrome via `wslview`, which `az login` and
`~/bin/enisa` depend on. Chrome registers as an `x-www-browser` alternative at priority
200, so two pins hold it off:

- `update-alternatives --set x-www-browser /usr/bin/wslview` — manual mode, survives
  package upgrades.
- `~/.config/mimeapps.list` names `wslview.desktop` for http, https, text/html and about.

Verify with `update-alternatives --display x-www-browser` and
`xdg-mime query default x-scheme-handler/http`. Both must say wslview.

SeleniumBase launches chromedriver by absolute path and never reads `$BROWSER` or
`xdg-open`, so the pins cost the tool nothing.

Chrome comes from Google's apt repo but `unattended-upgrades` does not cover that origin,
so Chrome only moves on an explicit `apt upgrade`. SeleniumBase then re-downloads a
matching `uc_driver` (~22 MB) on the next run. The driver is cached inside `.venv`, so
recreating the venv costs that download again.

## FIT files stay on the Windows drive

`.env` sets `GARMIN_FIT_DIR=/mnt/d/My Documents/Sport/GarminValentin`, the analysis repo.
`GARMIN_DATA_DIR` is unset, so the database and browser profile stay here on ext4.

Download dedup reads that directory, not the database
(`garmin_givemydata.py:709`):

```python
existing_fits = {f.stem.split("_")[1] for f in fit_dir.glob("*.fit")}
```

Filenames must keep the `<timestamp>_<activity_id>_<name>.fit` shape. Renaming files, or
moving them into subdirectories, makes the tool re-download everything.

Split multisport legs share their parent's activity ID (six files for one triathlon), so
they collapse into one entry in that set. That is correct and must stay that way.

## Database

`garmin.db` migrates in place on init — `ALTER TABLE ADD COLUMN`, plus a `RENAME COLUMN` on
`sleep`. Back it up before pulling upstream changes.

The 2026-09-22 upgrade deleted 56 rows: 55 `activity` rows that were API response envelopes
(no `activityId`, but an `activityList` wrapper or GraphQL `errors` key) and one `hrv` row
with a blank `calendar_date`. Both were junk from older parsers. Real activity count is
1458, not the 1513 an older `--status` reported.

The pre-migration copy is the untouched Windows database at
`D:\Program Files\Tools\garmin-givemydata\garmin.db` (48 tables vs 56 here). That Windows
install still works and has its own separate database — do not assume the two are in sync.

## Git

The branch is `master`. `valipod` is the fork remote, `origin` is upstream `nrvim`. There is
no `main` branch on the fork; a stale `valipod/main` remote-tracking ref may still resolve
locally. Sync with `git fetch --prune valipod` before comparing.
