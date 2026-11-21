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


def suggest(year, month, assignments, employees, settings, today, only_encargado=None):
    """Sugiere días (de hoy en adelante) en que conviene pasar a alguien a su puesto "ocasional"
    (ej. Soledad del spot a Auxiliar/limpieza) y quién podría cubrir su lugar.

    Prioridad 3: falta gente en el puesto ocasional en su mismo turno.
    Prioridad 2: hay un refuerzo en su mismo turno que sabe hacer su puesto (cambio sin dejar huecos).
    """
    first, last = month_bounds(year, month)
    start = max(first, parse_date(today))
    staffing = settings["staffing"]
    emp_by_id = {e["id"]: e for e in employees}
    occasional = [(e, p) for e in employees if e["active"] for p, lvl in e["positions"].items() if lvl == "ocasional"]
    encargados = [e for e in employees if e["active"] and "otro" in e["positions"]
                  and (only_encargado is None or e["id"] == only_encargado)]
    if not occasional and not encargados:
        return []
    by_day = defaultdict(list)
    for a in assignments:
        by_day[a["date"]].append(a)
    out = []
    for d in date_range(start, last):
        ds = iso(d)
        day = by_day.get(ds, [])
        if not day:
            continue
        count = defaultdict(int)
        for a in day:
            count[(a["shift"], a["position"])] += 1
        for e, p_to in occasional:
            best = None
            for a in day:
                if a["employee_id"] != e["id"] or a["position"] in (p_to, "otro") or a.get("locked"):
                    continue
                s, p_from = a["shift"], a["position"]
                need_to = staffing.get(s, {}).get(p_to, 0)
                if not need_to:
                    continue
                score, reasons = 0, []
                if count[(s, p_to)] < need_to:
                    score += 3
                    reasons.append(f"falta {POS_NAME[p_to]} a la {SHIFT_NAME[s].lower()}")
                covers = []
                for b in day:
                    other = emp_by_id.get(b["employee_id"])
                    if b["shift"] != s or b["employee_id"] == e["id"] or not other or b["position"] == "otro":
                        continue
                    is_extra = count[(s, b["position"])] > staffing.get(s, {}).get(b["position"], 0)
                    if is_extra and other["positions"].get(p_from) in ("principal", "suplente"):
                        covers.append(other["name"])
                if covers:
                    score += 2
                    who = sorted(set(covers))
                    if len(who) == 1:
                        reasons.append(f"{who[0]} está de refuerzo y puede cubrir {POS_NAME[p_from]}")
                    else:
                        reasons.append(f"{', '.join(who[:-1])} o {who[-1]} están de refuerzo y pueden cubrir {POS_NAME[p_from]}")
                if score and (best is None or score > best["score"]):
                    best = {"date": ds, "shift": s, "employee_id": e["id"], "name": e["name"],
                            "from_position": p_from, "to_position": p_to, "score": score,
                            "covers": sorted(set(covers)), "reasons": reasons}
            if best:
                out.append(best)
        # encargado de refuerzo (o con un refuerzo que lo cubre) -> a "otras tareas" (ej. corte de pasto)
        for e in encargados:
            if any(a["employee_id"] == e["id"] and a["position"] == "otro" for a in day):
                continue
            for a in day:
                if a["employee_id"] != e["id"] or a.get("locked"):
                    continue
                s, p = a["shift"], a["position"]
                need = staffing.get(s, {}).get(p, 0)
                covers = [emp_by_id[b["employee_id"]]["name"] for b in day
                          if b["shift"] == s and b["employee_id"] != e["id"] and b["employee_id"] in emp_by_id
                          and b["position"] != p and b["position"] != "otro"
                          and count[(s, b["position"])] > staffing.get(s, {}).get(b["position"], 0)
                          and emp_by_id[b["employee_id"]]["positions"].get(p) in ("principal", "suplente")]
                if count[(s, p)] > need:
                    reason = f"es refuerzo de {POS_NAME[p]}: sale de la lista sin dejar hueco"
                elif covers:
                    who = sorted(set(covers))
                    reason = f"{' o '.join(who)} {'está' if len(who) == 1 else 'están'} de refuerzo y {'puede' if len(who) == 1 else 'pueden'} cubrir {POS_NAME[p]}"
                else:
                    continue
                out.append({"date": ds, "shift": s, "employee_id": e["id"], "name": e["name"], "from_position": p,
                            "to_position": "otro", "note": "Corte de pasto", "score": 2, "covers": sorted(set(covers)),
                            "reasons": [reason]})
                break
    out.sort(key=lambda x: (x["date"], x["to_position"]))
    return out


def shift_interval(d, shift, cl=()):
    """(inicio, fin) en horas absolutas. Con cierres usa el horario real; si el turno está cerrado, el normal."""
    w = shift_window(d, shift, cl) if cl else None
    if w:
        return w
    base = parse_date(d).toordinal() * 24 + SHIFT_START[shift]
    return base, base + 8


# ---------------- cierres de la estación ----------------
# Un cierre es {start: 'YYYY-MM-DDTHH:MM', end: ..., note, yearly}. Si un turno queda con menos de
# MIN_OPEN horas abiertas se considera cerrado y ese rato lo cubre el turno de al lado
# (ej. reabre 25/12 20:00 -> la noche entra a las 20:00 y hace 10 horas).
MIN_OPEN = 4


def _abs_hour(dt):
    d, t = dt.split("T")
    h, mi = t.split(":")[:2]
    return parse_date(d).toordinal() * 24 + int(h) + int(mi) / 60


def expand_closures(closures, start, end):
    """Cierres (incluidos los que se repiten todos los años) que tocan el rango -> [(inicio, fin, nota)]."""
    start, end = parse_date(start), parse_date(end)
    out = []
    for c in closures:
        s, e = c["start"], c["end"]
        try:
            if c.get("yearly"):
                gap = int(e[:4]) - int(s[:4])
                for y in range(start.year - 1, end.year + 2):
                    out.append((_abs_hour(f"{y}{s[4:]}"), _abs_hour(f"{y + gap}{e[4:]}"), c.get("note", "")))
            else:
                out.append((_abs_hour(s), _abs_hour(e), c.get("note", "")))
        except ValueError:
            continue  # 29/02 en año no bisiesto, etc.
    lo, hi = start.toordinal() * 24 - 48, (end.toordinal() + 2) * 24
    return [x for x in out if x[1] > lo and x[0] < hi and x[1] > x[0]]


def _open_part(s, e, cl):
    parts = [(s, e)]
    for cs, ce, _ in cl:
        nxt = []
        for a, b in parts:
            if ce <= a or cs >= b:
                nxt.append((a, b))
                continue
            if a < cs:
                nxt.append((a, cs))
            if ce < b:
                nxt.append((ce, b))
        parts = nxt
    return max(parts, key=lambda p: p[1] - p[0]) if parts else None


def shift_window(d, shift, cl):
    """Horario real del turno con cierres: (inicio, fin) en horas absolutas, o None si está cerrado."""
    base = parse_date(d).toordinal() * 24 + SHIFT_START[shift]
    s, e = base, base + 8
    if not cl:
        return s, e
    part = _open_part(s, e, cl)
    if not part or part[1] - part[0] < MIN_OPEN:
        return None
    o_s, o_e = part
    prev = _open_part(s - 8, s, cl)
    if prev and prev[1] == s and prev[1] - prev[0] < MIN_OPEN:
        o_s = prev[0]
    nxt = _open_part(e, e + 8, cl)
    if nxt and nxt[0] == e and nxt[1] - nxt[0] < MIN_OPEN:
        o_e = nxt[1]
    return o_s, o_e


def fmt_hour(x):
    h = x % 24
    hh, mm = int(h), int(round((h - int(h)) * 60))
    return f"{hh:02d}:{mm:02d}"


def special_hours(start, end, cl):
    """{fecha: {turno: None (cerrado) | "20:00 a 06:00"}} sólo para los turnos que no son normales."""
    out = {}
    if not cl:
        return out
    for d in date_range(start, end):
        ds = iso(d)
        for sh in ("T3", "T1", "T2"):
            w = shift_window(ds, sh, cl)
            base = d.toordinal() * 24 + SHIFT_START[sh]
            if w is None:
                out.setdefault(ds, {})[sh] = None
            elif w != (base, base + 8):
                out.setdefault(ds, {})[sh] = f"{fmt_hour(w[0])} a {fmt_hour(w[1])}"
    return out


def closed_days(special):
    return {d for d, v in special.items() if len(v) == 3 and all(x is None for x in v.values())}


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


def employee_timeline(assignments, cl=()):
    """{emp_id: [(inicio, fin, date, shift)] ordenado}"""
    tl = defaultdict(list)
    seen = set()
    for a in assignments:
        key = (a["employee_id"], a["date"], a["shift"])
        if key in seen:
            continue
        seen.add(key)
        s, e = shift_interval(a["date"], a["shift"], cl)
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


def analyze(year, month, assignments, employees, licenses, settings, closures=()):
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

    special = special_hours(first, last, closures)
    closed = closed_days(special)
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
        if d in closed:
            if day:
                issue("warn", "La estación está cerrada ese día", date=d)
            continue
        if not day:
            issue("info", "Día sin armar", date=d)
            continue
        for shift, need in staffing.items():
            if special.get(d, {}).get(shift, "") is None:
                if any(a["shift"] == shift for a in day):
                    issue("warn", f"Turno {SHIFT_NAME[shift].lower()} cerrado y tiene gente asignada", date=d, shift=shift)
                continue
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
    timeline = employee_timeline(assignments, closures)
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
        # los días con la estación cerrada no son libres ni cuentan para el tope
        filled_days = [d for d in month_days if by_day.get(d) and d not in closed]
        libres = [d for d in filled_days if d not in worked_days and d not in lic_days]
        lic_in_month = [d for d in month_days if d in lic_days]
        avail = sum(1 for d in filled_days if d not in lic_days)
        target = libres_target(target_libres, avail, n_days - len(closed))
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
        elif len(filled_days) == n_days - len(closed) and emp["active"] and len(libres) < target:
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

    return {"issues": issues, "report": report, "weeks": [{"start": iso(a), "end": iso(b)} for a, b in weeks],
            "special": special}


def day_layout(d, assignments, employees, licenses_days_map, staffing, cl=()):
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

    sp = special_hours(d, d, cl).get(d, {})

    def block(shift):
        if shift in sp and sp[shift] is None:
            return [("CERRADO", "")]
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
    all_closed = len(sp) == 3 and all(v is None for v in sp.values())
    heads = {}
    for sh, title in (("T3", "TURNO 3 (Noche)"), ("T1", "TURNO 1 (Mañana)"), ("T2", "TURNO 2 (Tarde)")):
        heads[sh] = f"{title} {sp[sh]}" if sp.get(sh) else title
    return {
        "date": d,
        "title": f"{DAY_NAMES[dd.weekday()]} {fmt_day(dd)}" + (" · ESTACIÓN CERRADA" if all_closed else ""),
        "heads": heads,
        "T3": block("T3"),
        "T1": block("T1"),
        "T2": block("T2"),
        "libres": ([e["name"].upper() for e in on_lic] + [e["name"].upper() for e in others]) if day and not all_closed else [],
    }
