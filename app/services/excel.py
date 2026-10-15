import datetime
import openpyxl
from typing import Any, List, Dict, Tuple


def _cell_to_str(cell: Any) -> str:
    """Renders a cell as it would look in Excel: 5.0 -> '5', dates -> ISO date."""
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    if isinstance(cell, datetime.datetime):
        if cell.time() == datetime.time(0):
            return cell.date().isoformat()
        return cell.strftime("%Y-%m-%d %H:%M")
    if isinstance(cell, datetime.date):
        return cell.isoformat()
    return str(cell).strip()


def parse_excel(file_path: str) -> Tuple[List[Dict], List[str]]:
    """
    Parses the first sheet of an Excel file. Row 1 is the header.
    Returns (rows_as_dicts, headers). All values are strings.
    """
    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
    try:
        sheet = wb.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        header_row = next(rows_iter, None)
        if not header_row or all(h is None for h in header_row):
            return [], []

        data_rows = [
            list(row) for row in rows_iter
            if not all(c is None or (isinstance(c, str) and not c.strip()) for c in row)
        ]

        # Sheets often report extra blank columns: drop trailing columns with no header and no data.
        header_cells = list(header_row)
        while header_cells and header_cells[-1] is None and not any(
            len(r) >= len(header_cells) and r[len(header_cells) - 1] not in (None, "") for r in data_rows
        ):
            header_cells.pop()

        headers: List[str] = []
        seen = set()
        for i, h in enumerate(header_cells):
            name = _cell_to_str(h) or f"column_{i + 1}"
            if name.lower() in seen:
                raise ValueError(f"Duplicate header found: {name}")
            headers.append(name)
            seen.add(name.lower())

        rows = [
            {h: _cell_to_str(row[i]) if i < len(row) else "" for i, h in enumerate(headers)}
            for row in data_rows
        ]
        return rows, headers
    finally:
        wb.close()
