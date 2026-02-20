"""
Rocketlane EU Projects - Duration & Status Updater
Cron: Daily at 9:00 AM IST (3:30 AM UTC)
Crontab entry: 30 3 * * * /usr/bin/python3 /path/to/script.py
"""

import sys
import requests
import logging
from datetime import date, timedelta, datetime

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY  = "rl-b7d0b5c6-263e-43db-abcf-4040eb924df2"
BASE_URL = "https://api.rocketlane.com/api/1.0"
HEADERS  = {
    "api-key": API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Known field IDs ───────────────────────────────────────────────────────────
# Region field: 1799, EU option: 4  (from decoded view criteria)
REGION_FIELD_ID   = 1799
EU_OPTION_ID      = "4"

# Duration (Workdays) and Project Status custom field IDs
DURATION_FIELD_ID = 2279041
STATUS_FIELD_ID   = 2279048

# ⚠️  Fill these in after running: python3 script.py --discover
KICKOFF_FIELD_ID       = 607260  # fieldLabel = "Kick-off Date"
GOLIVE_PLAN_FIELD_ID   = 611603  # fieldLabel = "Go-Live Date Planned"
GOLIVE_ACTUAL_FIELD_ID = 611602  # fieldLabel = "Go-Live Date"



# ── India Holiday Calendar 2026 (Rocketlane HR doc) ───────────────────────────
HOLIDAYS = {
    date(2026, 1,  1),   # New Year's Day
    date(2026, 1, 15),   # Pongal
    date(2026, 1, 26),   # Republic Day
    date(2026, 4, 14),   # Tamil New Year's Day
    date(2026, 5,  1),   # May Day
    date(2026, 5, 28),   # Bakrid
    date(2026, 9, 14),   # Vinayakar Chathurthi
    date(2026, 10, 2),   # Gandhi Jayanthi
    date(2026, 10, 9),   # Ayudha Puja
    date(2026, 12, 25),  # Christmas
}

# ── Date / workday helpers ────────────────────────────────────────────────────

def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def calc_workdays(start: date, end: date) -> int:
    if end < start:
        return 0
    count, cur = 0, start
    while cur <= end:
        if is_working_day(cur):
            count += 1
        cur += timedelta(days=1)
    return count


def parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value / 1000).date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                pass
    return None


def get_status(golive_plan: date | None, golive_actual: date | None, today: date) -> str:
    if golive_actual is not None and golive_plan is not None:
        if golive_actual < golive_plan:
            return "Delivered Early"
        elif golive_actual == golive_plan:
            return "Delivered On-Time"
        else:
            return "Delayed"
    if golive_plan is None:
        return "At Risk"
    if golive_plan < today:
        return "Delayed"
    return "On Track"


# ── Field extraction helpers ──────────────────────────────────────────────────

def get_field_value_by_id(project: dict, field_id: int):
    """Extract a custom field value from the project's 'fields' array by fieldId."""
    for f in project.get("fields", []):
        if f.get("fieldId") == field_id:
            return f.get("fieldValue")
    return None


def get_native_date(project: dict, *keys):
    """Fallback: read a native top-level date field by trying multiple key names."""
    for k in keys:
        v = project.get(k)
        if v is not None:
            return v
    return None


# ── Rocketlane API calls ───────────────────────────────────────────────────────

def get_eu_projects() -> list:
    """
    GET /projects filtered server-side using exact criteria from the UI view:
        - project.field.1799.oneOf=4   → Region = EU       (customFields)
        - projectName.nc=test          → name notContains "test" (nativeFields)

    Paginates via nextPageToken from the pagination block.
    API returns pageSize=100 per call; totalRecordCount=167 requires 2 pages.
    Note: do NOT pass limit/pageSize — causes 500 with custom field filters.
    """
    projects = []
    params = {
        f"project.field.{REGION_FIELD_ID}.oneOf": EU_OPTION_ID,
        "projectName.nc": "test",
    }

    while True:
        r = requests.get(f"{BASE_URL}/projects", headers=HEADERS, params=params)
        r.raise_for_status()
        data = r.json()

        batch = data.get("data", [])
        projects.extend(batch)

        pagination = data.get("pagination", {})
        log.info("Page fetched — records so far: %d / %d",
                 len(projects), pagination.get("totalRecordCount", "?"))

        if not pagination.get("hasMore", False):
            break

        next_token = pagination.get("nextPageToken")
        if not next_token:
            break

        # Use nextPageToken for subsequent pages
        params["pageToken"] = next_token

    return projects


def get_project_details(pid) -> dict:
    r = requests.get(f"{BASE_URL}/projects/{pid}", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def update_project_fields(pid, fields_payload: list):
    """
    PUT /projects/{id}
    Correct payload per Rocketlane Custom Fields docs:
        { "fields": [ { "fieldId": <int>, "fieldValue": <value> } ] }
    """
    r = requests.put(
        f"{BASE_URL}/projects/{pid}",
        headers=HEADERS,
        json={"fields": fields_payload},
    )
    r.raise_for_status()
    return r.json()


def get_status_option_map() -> dict:
    """
    Fetch options for the Project Status field (SINGLE_CHOICE).
    includeAllFields=true is required to get fieldOptions in the response.
    Returns dict mapping optionLabel → optionValue (int):
        {'At Risk': 1, 'On Track': 2, 'Delayed': 3, 'Delivered On-Time': 4, 'Delivered Early': 5}
    """
    r = requests.get(
        f"{BASE_URL}/fields/{STATUS_FIELD_ID}",
        headers=HEADERS,
        params={"includeAllFields": "true"},
    )
    r.raise_for_status()
    field = r.json()
    options = field.get("fieldOptions", [])
    mapping = {
        opt.get("optionLabel"): opt.get("optionValue")
        for opt in options
        if opt.get("optionLabel") is not None
    }
    log.info("Project Status options loaded: %s", mapping)
    return mapping


def discover_date_field_ids():
    """
    Prints all PROJECT field IDs and labels so you can fill in
    KICKOFF_FIELD_ID, GOLIVE_PLAN_FIELD_ID, GOLIVE_ACTUAL_FIELD_ID above.
    Run with: python3 script.py --discover
    """
    r = requests.get(f"{BASE_URL}/fields", headers=HEADERS,
                     params={"objectType.eq": "PROJECT", "limit": 100})
    r.raise_for_status()
    data = r.json()
    fields = data if isinstance(data, list) else data.get("data", [])
    log.info("=== All PROJECT fields ===")
    for f in fields:
        log.info("  fieldId=%-10s  fieldLabel=%s", f.get("fieldId"), f.get("fieldLabel"))


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    if "--discover" in sys.argv:
        discover_date_field_ids()
        return

    today = date.today()
    log.info("=== Rocketlane EU Project Updater — %s ===", today)

    # 1. Fetch Project Status option map (SINGLE_CHOICE field)
    log.info("Fetching Project Status field options …")
    status_option_map = get_status_option_map()
    required_statuses = ["Delivered Early", "Delivered On-Time",
                         "At Risk", "Delayed", "On Track"]
    missing = [s for s in required_statuses if s not in status_option_map]
    if missing:
        log.error("Missing option label(s) in Project Status field: %s\n"
                  "Available options: %s", missing, list(status_option_map.keys()))
        return

    # 1. Fetch EU projects
    log.info("Fetching EU projects …")
    projects = get_eu_projects()
    # 2. Confirmation prompt — skipped automatically in cron (non-interactive)
    total = len(projects)
    log.info("Total EU projects fetched: %d", total)

    if sys.stdin.isatty():
        print(f"\n{'='*52}")
        print(f"  Total EU projects fetched : {total}")
        print(f"  Please verify this count looks correct.")
        print(f"{'='*52}")
        confirm = input("  Proceed with update? [y/N]: ").strip().lower()
        print(f"{'='*52}\n")
        if confirm != "y":
            log.info("Aborted by user.")
            return
    else:
        log.info("Non-interactive mode (cron) — proceeding automatically.")

    log.info("Proceeding with %d project(s) …", total)

    ok = failed = 0

    for p in projects:
        pid   = p.get("projectId") or p.get("id")
        pname = p.get("projectName") or p.get("name") or str(pid)

        try:
            details = get_project_details(pid)

            # ── Read dates from custom fields array by fieldId ────────────
            kickoff_raw       = get_field_value_by_id(details, KICKOFF_FIELD_ID)
            golive_plan_raw   = get_field_value_by_id(details, GOLIVE_PLAN_FIELD_ID)
            golive_actual_raw = get_field_value_by_id(details, GOLIVE_ACTUAL_FIELD_ID)

            # Fallback to native fields if custom field returns nothing
            if kickoff_raw is None:
                kickoff_raw = get_native_date(details, "startDate", "kickOffDate")
            if golive_plan_raw is None:
                golive_plan_raw = get_native_date(details, "dueDate")

            kickoff       = parse_date(kickoff_raw)
            golive_plan   = parse_date(golive_plan_raw)
            golive_actual = parse_date(golive_actual_raw)

            # ── Duration ──────────────────────────────────────────────────
            fields_to_update = []

            if kickoff and golive_plan:
                workdays = calc_workdays(kickoff, golive_plan)
                duration_val = f"{workdays}d"
                fields_to_update.append({
                    "fieldId": DURATION_FIELD_ID,
                    "fieldValue": duration_val,
                })
            else:
                duration_val = "N/A"
                log.warning("[%s] Missing Kick-off or Go-Live Planned; skipping duration.", pname)

            # ── Status ────────────────────────────────────────────────────
            status = get_status(golive_plan, golive_actual, today)
            status_option_value = status_option_map.get(status)
            if status_option_value is None:
                log.warning("[%s] Status '%s' not found in field options; skipping status update.", pname, status)
            else:
                fields_to_update.append({
                    "fieldId": STATUS_FIELD_ID,
                    "fieldValue": status_option_value,
                })

            log.info(
                "[%s] kickoff=%s | planned=%s | actual=%s → duration=%s | status=%s",
                pname, kickoff, golive_plan, golive_actual, duration_val, status,
            )

            # ── Push both fields in a single PUT call ─────────────────────
            update_project_fields(pid, fields_to_update)
            ok += 1

        except requests.HTTPError as e:
            log.error("[%s] HTTP %s — %s", pname, e.response.status_code, e.response.text)
            failed += 1
        except Exception as e:
            log.error("[%s] Unexpected error: %s", pname, e, exc_info=True)
            failed += 1

    log.info("Done. ✓ %d updated | ✗ %d failed", ok, failed)


if __name__ == "__main__":
    run()
