"""Datos comunes para exportar: semanas completas (lun-dom) con el layout de la planilla."""
from datetime import timedelta

from db import iso, parse_date, date_range, license_days
from rules import day_layout, fmt_day

MONTHS = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
          "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]


def week_ranges(start, end):
    start, end = parse_date(start), parse_date(end)
    mon = start - timedelta(days=start.weekday())
    out = []
    while mon <= end:
        out.append((mon, mon + timedelta(days=6)))
        mon += timedelta(days=7)
    return out


def build_weeks(start, end, assignments, employees, licenses, staffing, closures=()):
    lic = license_days(licenses)
    s, e = iso(parse_date(start)), iso(parse_date(end))
    with_data = {a["date"] for a in assignments}
    weeks = []
    for mon, sun in week_ranges(start, end):
        # semana completa lun-dom; los días fuera del rango sólo si ya están armados
        days = [day_layout(iso(d), assignments, employees, lic, staffing, closures) for d in date_range(mon, sun)
                if s <= iso(d) <= e or iso(d) in with_data]
        weeks.append({"start": mon, "end": sun, "label": f"{fmt_day(mon)} al {fmt_day(sun)}", "days": days})
    return weeks


def rows_needed(day):
    return max(len(day["T3"]), len(day["T1"]), len(day["T2"]), len(day["libres"]), 5)
