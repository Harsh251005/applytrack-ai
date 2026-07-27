import time
from datetime import datetime

from agents import function_tool
from src.services.sheets_service import get_sheets_service
from src.tools.get_email import get_email_impl
from src.tools.search_emails import search_emails_impl

# Statuses that mean "this application is closed" — no point checking Gmail for them.
FINAL_STATUSES = {"rejected", "offer accepted", "withdrawn", "declined", "ghosted"}

# Applications with no known date fall back to this window instead of
# searching the entire inbox history.
DEFAULT_LOOKBACK_DAYS = 60

# Retry a Gmail call this many times before giving up on that one company.
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 2


def _with_retries(fn, *args, **kwargs):
    """Runs fn, retrying on transient network errors (timeouts, connection
    resets) a couple of times before giving up. Anything else (bad query,
    auth error, etc.) is raised immediately since retrying won't help."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (TimeoutError, ConnectionError, OSError) as e:
            last_error = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_error


@function_tool
def check_for_application_updates(
    spreadsheet_id: str,
    sheet_name: str,
) -> dict:
    """
    Checks Gmail for updates on active job applications tracked in the spreadsheet.

    Instead of pulling every email in the inbox, this reads the applications
    from the sheet first, skips ones already in a final state (Rejected,
    Withdrawn, Offer Accepted, etc.), and for every remaining company runs one
    targeted Gmail search scoped to that company's name and the date since
    its last known activity (Interview Date if present, else Application
    Date, else a 60-day fallback window).

    Returns, per company that has matching emails, the row number in the
    sheet and the full content of the matching emails, so the calling agent
    can decide what (if anything) changed and call update_application_from_email.

    If a Gmail call for a given company times out, it's retried a couple of
    times before being skipped — one flaky network call won't drop the
    results already gathered for every other company. Skipped companies are
    listed in `failed_companies` so you can tell the user which ones to
    re-check.
    """

    service = get_sheets_service()

    print(f"Checking application updates for {sheet_name}")

    header_row = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!1:1")
        .execute()
        .get("values", [[]])[0]
    )

    all_rows = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A2:Z")
        .execute()
        .get("values", [])
    )

    def col_index(name: str):
        return header_row.index(name) if name in header_row else None

    company_idx = col_index("Company Name")
    status_idx = col_index("Status")
    app_date_idx = col_index("Application Date")
    interview_date_idx = col_index("Interview Date")

    def cell(row, idx):
        return row[idx].strip() if idx is not None and idx < len(row) and row[idx] else None

    candidates = []
    failed_companies = []
    companies_checked = 0

    for i, row in enumerate(all_rows):
        if not row:
            continue

        company = cell(row, company_idx)
        if not company:
            continue

        status = cell(row, status_idx) or ""
        if status.lower() in FINAL_STATUSES:
            continue

        companies_checked += 1
        row_number = i + 2  # +1 for header, +1 for 0-index

        # Prefer the most recent known date to scope the search window.
        last_date_str = cell(row, interview_date_idx) or cell(row, app_date_idx)
        parsed_date = _parse_date(last_date_str) if last_date_str else None

        query = f'"{company}" -category:promotions'
        if parsed_date:
            query += f" after:{parsed_date.strftime('%Y/%m/%d')}"
        else:
            query += f" newer_than:{DEFAULT_LOOKBACK_DAYS}d"

        # A network hiccup on one company shouldn't cost us the results
        # already gathered for every other company, so isolate failures here.
        try:
            search_result = _with_retries(search_emails_impl, query=query, max_results=5)

            if search_result["count"] == 0:
                continue

            emails = [_with_retries(get_email_impl, e["id"]) for e in search_result["emails"]]
        except Exception as e:
            failed_companies.append({"company": company, "row_number": row_number, "error": str(e)})
            continue

        candidates.append(
            {
                "row_number": row_number,
                "company": company,
                "current_status": status,
                "query_used": query,
                "emails": emails,
            }
        )

    return {
        "companies_checked": companies_checked,
        "companies_with_new_emails": len(candidates),
        "candidates": candidates,
        "failed_companies": failed_companies,
    }


def _parse_date(date_str: str):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None
