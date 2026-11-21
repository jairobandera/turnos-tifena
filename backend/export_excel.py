"""Excel con el mismo formato que la planilla impresa: una hoja por semana."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.worksheet.page import PageMargins

from export_common import rows_needed

THIN = Side(style="thin", color="000000")
MED = Side(style="medium", color="000000")
WIDTHS = {"A": 11, "B": 15, "C": 16, "D": 15, "E": 16, "F": 15, "G": 15}
GROUPS = [("T3", 1, 2, "TURNO 3 (Noche)"), ("T1", 3, 4, "TURNO 1 (Mañana)"), ("T2", 5, 6, "TURNO 2 (Tarde)")]


def _box(ws, r1, c1, r2, c2, side=THIN):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            cell = ws.cell(row=r, column=c)
            b = cell.border
            cell.border = Border(
                left=side if c == c1 else b.left,
                right=side if c == c2 else b.right,
                top=side if r == r1 else b.top,
                bottom=side if r == r2 else b.bottom,
            )


def build_excel(weeks):
    wb = Workbook()
    wb.remove(wb.active)
    for w in weeks:
        ws = wb.create_sheet(w["label"].replace("/", "-")[:31])
        for col, width in WIDTHS.items():
            ws.column_dimensions[col].width = width
        ws.page_setup.orientation = "portrait"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins = PageMargins(left=0.3, right=0.3, top=0.3, bottom=0.3)
        ws.print_options.horizontalCentered = True

        r = 1
        for day in w["days"]:
            n = rows_needed(day)
            # título del día
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
            c = ws.cell(row=r, column=1, value=day["title"])
            c.font = Font(name="Arial", bold=True, size=12)
            c.alignment = Alignment(horizontal="center", vertical="center")
            _box(ws, r, 1, r, 7)
            # encabezados
            h = r + 1
            for key, c1, c2, title in GROUPS:
                ws.merge_cells(start_row=h, start_column=c1, end_row=h, end_column=c2)
                cell = ws.cell(row=h, column=c1, value=day.get("heads", {}).get(key, title))
                cell.font = Font(name="Arial", size=11)
                cell.alignment = Alignment(horizontal="center")
                _box(ws, h, c1, h, c2)
            cell = ws.cell(row=h, column=7, value="LIBRES")
            cell.font = Font(name="Arial", size=11)
            cell.alignment = Alignment(horizontal="center")
            _box(ws, h, 7, h, 7)
            # contenido
            top = h + 1
            for key, c1, c2, _ in GROUPS:
                for i, (label, name) in enumerate(day[key]):
                    if label:
                        ws.cell(row=top + i, column=c1, value=label).font = Font(name="Arial", size=11)
                    if name:
                        ws.cell(row=top + i, column=c2, value=name).font = Font(name="Arial", size=11)
                _box(ws, top, c1, top + n - 1, c2)
            for i, name in enumerate(day["libres"]):
                cell = ws.cell(row=top + i, column=7, value=name)
                cell.font = Font(name="Arial", size=11)
                cell.alignment = Alignment(horizontal="center")
            _box(ws, top, 7, top + n - 1, 7)
            _box(ws, r, 1, top + n - 1, 7, MED)
            r = top + n
        ws.print_area = f"A1:G{r - 1}"
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()
