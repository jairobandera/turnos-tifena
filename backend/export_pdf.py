"""PDF con el formato de la planilla impresa: una hoja A4 por semana."""
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import PageBreak, SimpleDocTemplate, Table, TableStyle

from export_common import rows_needed

MARGIN = 18
COLS = [0.105, 0.145, 0.15, 0.14, 0.15, 0.14, 0.17]  # proporciones = columnas del Excel


def build_pdf(weeks, title="Turnos"):
    bio = BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=MARGIN, title=title)
    width = A4[0] - 2 * MARGIN
    col_w = [width * c for c in COLS]
    story = []
    for wi, w in enumerate(weeks):
        total_rows = sum(rows_needed(d) + 2 for d in w["days"])
        row_h = min(15.5, (A4[1] - 2 * MARGIN - 10) / total_rows)
        font = max(6.5, min(10, row_h * 0.68))
        for day in w["days"]:
            n = rows_needed(day)
            data = [[day["title"], "", "", "", "", "", ""],
                    ["TURNO 3 (Noche)", "", "TURNO 1 (Mañana)", "", "TURNO 2 (Tarde)", "", "LIBRES"]]
            for i in range(n):
                row = []
                for key in ("T3", "T1", "T2"):
                    lab, name = day[key][i] if i < len(day[key]) else ("", "")
                    row += [lab, name]
                row.append(day["libres"][i] if i < len(day["libres"]) else "")
                data.append(row)
            last = n + 1
            style = [
                ("FONT", (0, 0), (-1, -1), "Helvetica", font),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font + 1),
                ("SPAN", (0, 0), (-1, 0)),
                ("SPAN", (0, 1), (1, 1)), ("SPAN", (2, 1), (3, 1)), ("SPAN", (4, 1), (5, 1)),
                ("ALIGN", (0, 0), (-1, 1), "CENTER"),
                ("ALIGN", (6, 2), (6, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("BOX", (0, 0), (-1, -1), 1.2, colors.black),
                ("BOX", (0, 0), (-1, 0), 0.6, colors.black),
                ("GRID", (0, 1), (-1, 1), 0.6, colors.black),
            ]
            for c1, c2 in ((0, 1), (2, 3), (4, 5), (6, 6)):
                style.append(("BOX", (c1, 2), (c2, last), 0.6, colors.black))
            t = Table(data, colWidths=col_w, rowHeights=[row_h] * len(data))
            t.setStyle(TableStyle(style))
            story.append(t)
        if wi < len(weeks) - 1:
            story.append(PageBreak())
    doc.build(story)
    return bio.getvalue()
