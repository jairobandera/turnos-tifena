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


def build_weeks(start, end, assignments, employees, licenses, staffing, closures=(), extras=None, solo_rango=False):
    """Semanas lun-dom. Con solo_rango (exportar un mes) sólo van los días del rango: del 1 al último del mes."""
    lic = license_days(licenses)
    s, e = iso(parse_date(start)), iso(parse_date(end))
    with_data = {a["date"] for a in assignments}
    weeks = []
    if solo_rango:
        # mes entero: hojas de 7 días seguidos desde el día 1 (así ninguna hoja queda con un solo día)
        todos = list(date_range(start, end))
        for i in range(0, len(todos), 7):
            dias = todos[i:i + 7]
            days = [day_layout(iso(d), assignments, employees, lic, staffing, closures, extras) for d in dias]
            weeks.append({"start": dias[0], "end": dias[-1], "label": f"{fmt_day(dias[0])} al {fmt_day(dias[-1])}", "days": days})
        return weeks
    for mon, sun in week_ranges(start, end):
        # semana completa lun-dom; los días fuera del rango sólo si ya están armados (y no es un mes)
        dias = [d for d in date_range(mon, sun) if s <= iso(d) <= e or (not solo_rango and iso(d) in with_data)]
        if not dias:
            continue
        days = [day_layout(iso(d), assignments, employees, lic, staffing, closures, extras) for d in dias]
        weeks.append({"start": dias[0], "end": dias[-1], "label": f"{fmt_day(dias[0])} al {fmt_day(dias[-1])}", "days": days})
    return weeks


def rows_needed(day):
    return max(len(day["T3"]), len(day["T1"]), len(day["T2"]), len(day["libres"]), 5)
