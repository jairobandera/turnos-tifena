"""Reglas de negocio: horarios, descansos, horas de 4, validación y reporte mensual.

Convención de turnos del día D:
  T3 Noche  : 22:00 de D-1 -> 06:00 de D   (por eso va primero en la planilla)
  T1 Mañana : 06:00 -> 14:00 de D
  T2 Tarde  : 14:00 -> 22:00 de D
Rotación "para atrás": Mañana -> Noche -> Tarde -> Mañana.
"""
from collections import defaultdict
from datetime import timedelta

from db import iso, parse_date, date_range, month_bounds, license_days

SHIFT_START = {"T3": -2, "T1": 6, "T2": 14}
SHIFT_NAME = {"T1": "Mañana", "T2": "Tarde", "T3": "Noche"}
NEXT_SHIFT = {"T1": "T3", "T3": "T2", "T2": "T1"}
POS_NAME = {"cajero": "Cajero", "shop": "Cajero Shop", "pistero": "Pistero", "auxiliar": "Auxiliar", "otro": "Otras tareas"}
DAY_NAMES = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]


def libres_target(objetivo, dias_disponibles, dias_mes):
    """Libres que le corresponden: tope prorrateado por días sin licencia / mes incompleto."""
    if not dias_mes:
        return objetivo
    return int(objetivo * dias_disponibles / dias_mes + 0.5)


def shift_interval(d, shift):
    """(inicio, fin) en horas absolutas desde una época arbitraria."""
    base = parse_date(d).toordinal() * 24 + SHIFT_START[shift]
    return base, base + 8


def fmt_day(d):
    d = parse_date(d)
    return f"{d.day:02d}/{d.month:02d}"


def weeks_of_month(year, month):
    """Semanas lunes-domingo que tocan el mes: [(lunes, domingo)]."""
    first, last = month_bounds(year, month)
    start = first - timedelta(days=first.weekday())
    out = []
    while start <= last:
        out.append((start, start + timedelta(days=6)))
        start += timedelta(days=7)
    return out


def employee_timeline(assignments):
    """{emp_id: [(inicio, fin, date, shift)] ordenado}"""
    tl = defaultdict(list)
    seen = set()
    for a in assignments:
        key = (a["employee_id"], a["date"], a["shift"])
        if key in seen:
            continue
        seen.add(key)
        s, e = shift_interval(a["date"], a["shift"])
        tl[a["employee_id"]].append((s, e, a["date"], a["shift"]))
    for v in tl.values():
        v.sort()
    return tl


def rest_events(timeline):
    """Recorre turnos consecutivos de un empleado.
    Devuelve [(tipo, date, shift_prev, shift_next, date_prev)] con tipo 'hora4' (8 h de descanso) o 'pegado' (<= 0 h)."""
    out = []
    for prev, nxt in zip(timeline, timeline[1:]):
        rest = nxt[0] - prev[1]
        if rest <= 0:
            out.append(("pegado", nxt[2], prev[3], nxt[3], prev[2]))
        elif rest <= 8:
            out.append(("hora4", nxt[2], prev[3], nxt[3], prev[2]))
    return out


def shift_blocks(seq):
    """seq = [(date, shift)] sólo días trabajados, ordenado. Agrupa en bloques del mismo turno (los libres no cortan)."""
    blocks = []
    for d, s in seq:
        if blocks and blocks[-1]["shift"] == s:
            blocks[-1]["days"].append(d)
        else:
            blocks.append({"shift": s, "days": [d]})
    return blocks


def analyze(year, month, assignments, employees, licenses, settings):
    """Valida el mes y arma el reporte privado del admin.

    `assignments` puede incluir días anteriores al mes (historial) para evaluar continuidad.
    """
    first, last = month_bounds(year, month)
    m_start, m_end = iso(first), iso(last)
    month_days = [iso(d) for d in date_range(first, last)]
    n_days = len(month_days)
    emp_by_id = {e["id"]: e for e in employees}
    lic = license_days(licenses)
    staffing = settings["staffing"]
    bloque = int(settings.get("bloque", 4))
    max_h4 = int(settings.get("max_horas4", 2))
    target_libres = int(settings.get("libres_objetivo", 6))
    max_seguidos = int(settings.get("max_dias_seguidos", 6))

    issues = []

    def issue(level, msg, date=None, shift=None, employee_id=None, position=None):
        issues.append({"level": level, "msg": msg, "date": date, "shift": shift, "employee_id": employee_id, "position": position})

    in_month = [a for a in assignments if m_start <= a["date"] <= m_end]
    by_day = defaultdict(list)
    for a in in_month:
        by_day[a["date"]].append(a)

    # --- dotación, licencias, puestos, duplicados ---
    for d in month_days:
        day = by_day.get(d, [])
        if not day:
            issue("info", "Día sin armar", date=d)
            continue
        for shift, need in staffing.items():
            for pos, n in need.items():
                have = sum(1 for a in day if a["shift"] == shift and a["position"] == pos)
                if have < n:
                    issue("warn", f"Falta {POS_NAME[pos]} ({have}/{n})", date=d, shift=shift, position=pos)
        per_emp = defaultdict(list)
        for a in day:
            per_emp[a["employee_id"]].append(a)
            emp = emp_by_id.get(a["employee_id"])
            name = emp["name"] if emp else f"#{a['employee_id']}"
            if d in lic.get(a["employee_id"], ()):
                issue("error", f"{name} está de licencia", date=d, shift=a["shift"], employee_id=a["employee_id"])
            if emp and a["position"] == "otro" and "otro" not in emp["positions"]:
                issue("error", f"{name}: sólo el encargado puede ir a otras tareas", date=d, shift=a["shift"], employee_id=a["employee_id"])
            elif emp and a["position"] != "otro" and a["position"] not in emp["positions"]:
                issue("warn", f"{name} cubre {POS_NAME[a['position']]} (no es su puesto)", date=d, shift=a["shift"], employee_id=a["employee_id"], position=a["position"])
            if emp and not emp["active"]:
                issue("warn", f"{name} está dado de baja", date=d, shift=a["shift"], employee_id=a["employee_id"])
        for eid, lst in per_emp.items():
            shifts = [a["shift"] for a in lst]
            if len(shifts) != len(set(shifts)):
                issue("error", f"{emp_by_id.get(eid, {}).get('name', eid)} está dos veces en el mismo turno", date=d, employee_id=eid)

    # --- por empleado: descansos, horas de 4, bloques, libres ---
    timeline = employee_timeline(assignments)
    weeks = weeks_of_month(year, month)
    report = []
    involved = {e["id"] for e in employees if e["active"]} | {a["employee_id"] for a in in_month}

    for eid in sorted(involved, key=lambda i: emp_by_id.get(i, {}).get("name", "")):
        emp = emp_by_id.get(eid, {"id": eid, "name": f"#{eid}", "positions": {}, "active": False})
        tl = timeline.get(eid, [])
        events = [ev for ev in rest_events(tl) if m_start <= ev[1] <= m_end]
        h4_dates = []
        for kind, d, sp, sn, dp in events:
            if kind == "pegado":
                issue("error", f"{emp['name']}: {SHIFT_NAME[sp]} del {fmt_day(dp)} pegado a {SHIFT_NAME[sn]} del {fmt_day(d)} sin descanso", date=d, shift=sn, employee_id=eid)
            else:
                h4_dates.append(d)
                if sp == "T3" and sn == "T2":
                    issue("warn", f"{emp['name']}: Noche → Tarde el mismo día (hora de 4 manual)", date=d, shift=sn, employee_id=eid)
        worked_days = {t[2] for t in tl}
        lic_days = lic.get(eid, set())
        counts = {"T1": 0, "T2": 0, "T3": 0}
        for t in tl:
            if m_start <= t[2] <= m_end:
                counts[t[3]] += 1
        # sólo cuentan los días ya armados; los días de licencia no son libres
        filled_days = [d for d in month_days if by_day.get(d)]
        libres = [d for d in filled_days if d not in worked_days and d not in lic_days]
        lic_in_month = [d for d in month_days if d in lic_days]
        avail = sum(1 for d in filled_days if d not in lic_days)
        target = libres_target(target_libres, avail, n_days)
        libres_armados = libres

        # días seguidos trabajados
        run, run_start = 0, None
        for d in date_range(parse_date(m_start) - timedelta(days=max_seguidos + 1), last):
            ds = iso(d)
            if ds in worked_days:
                run += 1
                run_start = run_start or ds
                if run == max_seguidos + 1 and ds >= m_start:
                    issue("warn", f"{emp['name']}: más de {max_seguidos} días seguidos sin libre", date=ds, employee_id=eid)
            else:
                run, run_start = 0, None

        # bloques de cajeros principales
        if emp["positions"].get("cajero") == "principal" and settings.get("cajeros_estrictos", True):
            seq = []
            for t in tl:
                if not seq or seq[-1][0] != t[2]:
                    seq.append((t[2], t[3]))
                else:
                    seq[-1] = (t[2], t[3])
            blocks = shift_blocks(seq)
            for i, b in enumerate(blocks):
                if b["days"][-1] < m_start:
                    continue
                first_block = i == 0
                last_block = i == len(blocks) - 1
                n = len(b["days"])
                if n > bloque:
                    issue("warn", f"{emp['name']}: {n} {SHIFT_NAME[b['shift']].lower()}s seguidas (bloque de {bloque})", date=b["days"][bloque], employee_id=eid)
                elif n < bloque and not first_block and not last_block:
                    issue("warn", f"{emp['name']}: sólo {n} {SHIFT_NAME[b['shift']].lower()}(s) antes de cambiar de turno (deben ser {bloque})", date=b["days"][-1], employee_id=eid)
                if not first_block:
                    prev = blocks[i - 1]["shift"]
                    if NEXT_SHIFT[prev] != b["shift"] and b["days"][0] >= m_start:
                        issue("warn", f"{emp['name']}: pasa de {SHIFT_NAME[prev]} a {SHIFT_NAME[b['shift']]} (le toca {SHIFT_NAME[NEXT_SHIFT[prev]]})", date=b["days"][0], employee_id=eid)

        if len(h4_dates) > max_h4:
            issue("warn", f"{emp['name']}: {len(h4_dates)} horas de 4 en el mes (máximo {max_h4})", employee_id=eid)
        if emp["active"] and len(libres) > target:
            issue("error", f"{emp['name']}: {len(libres)} libres, más de los {target} que le corresponden", employee_id=eid)
        elif len(filled_days) == n_days and emp["active"] and len(libres) < target:
            issue("warn", f"{emp['name']}: {len(libres)} libres en el mes (le corresponden {target})", employee_id=eid)

        week_rows = []
        for ws, we in weeks:
            wd = [iso(x) for x in date_range(max(ws, first), min(we, last))]
            week_rows.append({
                "label": f"{fmt_day(ws)} al {fmt_day(we)}",
                "libres": sum(1 for x in wd if x in libres),
                "horas4": sum(1 for x in h4_dates if x in wd),
                "trabajados": sum(1 for x in wd if x in worked_days),
                "licencia": sum(1 for x in wd if x in lic_days),
            })
        report.append({
            "employee_id": eid,
            "name": emp["name"],
            "libres": len(libres),
            "libres_armados": len(libres_armados),
            "libres_objetivo": target,
            "licencia": len(lic_in_month),
            "horas4": len(h4_dates),
            "horas4_fechas": h4_dates,
            "max_horas4": max_h4,
            "turnos": counts,
            "semanas": week_rows,
        })

    return {"issues": issues, "report": report, "weeks": [{"start": iso(a), "end": iso(b)} for a, b in weeks]}


def day_layout(d, assignments, employees, licenses_days_map, staffing):
    """Estructura de un día como en la planilla en papel (usada por Excel y PDF)."""
    emp_by_id = {e["id"]: e for e in employees}
    day = [a for a in assignments if a["date"] == d]

    def names(shift, pos):
        lst = sorted((a for a in day if a["shift"] == shift and a["position"] == pos), key=lambda a: a.get("ord", 0))
        out = []
        for a in lst:
            if a["employee_id"] in emp_by_id:
                n = emp_by_id[a["employee_id"]]["name"].upper()
                if pos == "otro" and a.get("note"):
                    n += f" ({a['note'].upper()})"
                out.append(n)
        return out

    def block(shift):
        rows = []
        labels = {"cajero": "CAJERO:" if shift == "T3" else "CAJERO PISTA:", "shop": "CAJERO SHOP:", "pistero": "PISTERO:",
                  "auxiliar": "AUXILIAR:", "otro": "OTRAS TAREAS:"}
        for pos in ["cajero", "shop", "pistero", "auxiliar", "otro"]:
            need = staffing.get(shift, {}).get(pos, 0)
            lst = names(shift, pos)
            if need == 0 and not lst:
                continue
            lst = lst + [""] * max(0, need - len(lst))
            for i, n in enumerate(lst or [""]):
                rows.append((labels[pos] if i == 0 else "", n))
        return rows

    worked = {a["employee_id"] for a in day}
    on_lic = [e for e in employees if d in licenses_days_map.get(e["id"], ()) and e["id"] not in worked]
    others = [e for e in employees if e["active"] and e["id"] not in worked and e not in on_lic]
    dd = parse_date(d)
    return {
        "date": d,
        "title": f"{DAY_NAMES[dd.weekday()]} {fmt_day(dd)}",
        "T3": block("T3"),
        "T1": block("T1"),
        "T2": block("T2"),
        "libres": [e["name"].upper() for e in on_lic] + [e["name"].upper() for e in others] if day else [],
    }
