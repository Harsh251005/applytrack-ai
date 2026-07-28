from typing import List

from agents import function_tool
from src.services.sheets_service import get_sheets_service


@function_tool
def remove_range(
    spreadsheet_id: str,
    sheet_name: str,
    row_numbers: List[int]
) -> dict:
    """
    Deletes one or more entire rows from a sheet — the rows are actually
    removed (not just cleared), and everything below shifts up to close
    the gap automatically.

    Use this instead of clear_range + append_rows whenever you need to
    remove existing rows. That combo just blanks the values and leaves the
    gap, or forces you to re-write the rest of the data to close it — this
    tool does the whole thing in one call.

    Args:
        spreadsheet_id: The ID of the spreadsheet.
        sheet_name: Exact sheet name (e.g. 'Sheet1'). Get this from schema.
        row_numbers: 1-indexed row numbers as they appear in the sheet
            (row 1 = header, row 2 = first data row, etc). e.g. [5, 7] removes
            rows 5 and 7; every row below either one shifts up to fill the space.
    """
    service = get_sheets_service()

    print(f"Removing rows {row_numbers} from {sheet_name}")

    sheet_meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

    sheet_id = None
    for sheet in sheet_meta.get("sheets", []):
        if sheet["properties"]["title"] == sheet_name:
            sheet_id = sheet["properties"]["sheetId"]
            break

    if sheet_id is None:
        return {
            "removed": False,
            "message": f"No sheet named '{sheet_name}' found."
        }

    # Delete highest row number first, in a single batch. deleteDimension
    # shifts everything below the deleted row up immediately, so removing
    # row 7 before row 5 keeps row 5's index correct for its own deletion —
    # doing it the other way round would delete the wrong row.
    unique_rows = sorted(set(row_numbers), reverse=True)

    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheet_id,
                    "dimension": "ROWS",
                    "startIndex": row - 1,  # Sheets API rows are 0-indexed
                    "endIndex": row,
                }
            }
        }
        for row in unique_rows
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()

    return {
        "removed": True,
        "removed_rows": unique_rows,
    }