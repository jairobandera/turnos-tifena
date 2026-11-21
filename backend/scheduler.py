"""Generador automático de turnos con OR-Tools CP-SAT.

Arma los días "libres de datos" de un mes (o rehace desde una fecha) tomando como historial
fijo los días ya armados, para que la rotación, los bloques de 4 y las horas de 4 sigan sin cortes.
"""
from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from db import iso, parse_date, date_range, month_bounds, license_days
from rules import NEXT_SHIFT, POS_NAME, SHIFT_NAME, fmt_day, libres_target, special_hours, closed_days

SH = ["T3", "T1", "T2"]          # orden cronológico dentro del día
HISTORY_DAYS = 14

# Pesos del objetivo (más alto = más importante evitar)
W_UNFILLED = 10000
W_H4_EXCESS = 3000
W_CAJERO_RULE = 400       # sólo si no se pudo cumplir estricto
W_LIBRES_FALTAN = 300
W_SEGUIDOS = 150
W_SEMANA_SIN_LIBRE = 80
W_H4 = 60
W_WRONG_ORDER = 40
W_SUP_NOCHE = 30
W_EARLY = 25
W_LIBRES_SOBRAN = 30
W_EXT = 35
W_SUP = 8
W_SWITCH_FIJO = 3
W_KEEP = 25               # premio por dejar a alguien donde ya estaba (al rehacer cambia lo mínimo)
W_LIBRES_EXCESO = 5000    # nadie puede tener más libres de los que le corresponden
W_EXTRA = 6               # un refuerzo (persona de más) en mañana/tarde
W_EXTRA_NOCHE = 45

# Cuántas personas de más puede tener cada puesto como refuerzo ("*" = cualquier turno)
EXTRA_CAP = {("T3", "pistero"): 1, ("*", "pistero"): 2, ("*", "auxiliar"): 1, ("*", "shop"): 1}



def generate(year, month, employees, assignments, licenses, settings, desde=None, mode="completar", time_limit=None,
             closures=()):
    """mode='completar': sólo llena días vacíos >= desde (los armados quedan fijos).
    mode='rehacer'  : rehace todos los días >= desde, manteniendo las asignaciones con candado."""
    first, last = month_bounds(year, month)
    desde = max(parse_date(desde), first) if desde else first
    by_day = defaultdict(list)
    for a in assignments:
        by_day[a["date"]].append(a)
    month_days = [iso(d) for d in date_range(first, last)]
    # un día está "armado" si tiene alguien en un puesto; las "otras tareas" solas no cuentan
    staffed = {d for d, lst in by_day.items() if any(a["position"] != "otro" for a in lst)}
    if mode == "completar":
        free = [d for d in month_days if d >= iso(desde) and d not in staffed]
    else:
        free = [d for d in month_days if d >= iso(desde)]
    if not free:
        return {"ok": False, "msg": "No hay días vacíos para generar desde esa fecha."}
    free_set = set(free)
    locked = [a for a in assignments if a["date"] in free_set and (a["position"] == "otro" or (mode == "rehacer" and a.get("locked")))]
    prefer = {(a["employee_id"], a["date"], a["shift"], a["position"]) for a in assignments if a["date"] in free_set}

    # el modelo ve siempre el mes completo (para contar bien los libres) + historial previo
    start = min(parse_date(free[0]) - timedelta(days=HISTORY_DAYS), first)
    days = [iso(d) for d in date_range(start, last)]
    emps = [e for e in employees if e["active"]]
    lic = license_days(licenses)
    staffing = settings["staffing"]
    B = int(settings.get("bloque", 4))
    max_h4 = int(settings.get("max_horas4", 2))
    target_libres = int(settings.get("libres_objetivo", 6))
    max_seg = int(settings.get("max_dias_seguidos", 6))
    time_limit = float(time_limit or settings.get("tiempo_generador", 25))

    # cierres de la estación: turnos cerrados no llevan gente; días cerrados no cuentan como libres
    special = special_hours(days[0], last, closures)
    closed_shift = {(d, s) for d, v in special.items() for s, h in v.items() if h is None}
    closed_d = closed_days(special)
    common = (days, free_set, by_day, lic, B, max_h4, target_libres, max_seg, month_days, closed_shift, closed_d)
    phases = [True, False] if settings.get("cajeros_estrictos", True) else [False]
    result = None
    for strict in phases:
        # Etapa 1: sólo los cajeros principales (modelo chico, se resuelve casi óptimo).
        # Sus turnos quedan fijos para la etapa 2, que arma al resto.
        forced = list(locked)
        cajeros = [e for e in emps if e["positions"].get("cajero") == "principal"]
        if cajeros:
            only_caj = {s: {"cajero": need["cajero"]} for s, need in staffing.items() if need.get("cajero")}
            locked_caj = [a for a in locked if a["employee_id"] in {e["id"] for e in cajeros}]
            r1 = _solve(strict, cajeros, only_caj, locked_caj, *common, time_limit * 0.3, night_bonus=True, prefer=prefer)
            if r1["ok"]:
                forced += [a for a in r1["assignments"] if not a.get("locked")]
        result = _solve(strict, emps, staffing, forced, *common, time_limit * 0.7, prefer=prefer)
        if result["ok"]:
            result["estricto"] = strict
            break
    if not result["ok"]:
        return result

    # días del mes que quedan como estaban + los generados
    kept = [a for a in assignments if a["date"] in month_days and a["date"] not in free_set]
    result["assignments"] = kept + result["assignments"]
    result["generated_days"] = free
    return result


def _solve(strict, emps, staffing, locked, days, free_set, by_day, lic, B, max_h4,
           target_libres, max_seg, month_days, closed_shift, closed_d, time_limit, night_bonus=False,
           prefer=frozenset()):
    m = cp_model.CpModel()
    pen = []
    x = {}
    ws = {}
    off = {}
    month_set = set(month_days)
    locked_keys = {}
    for a in locked:
        locked_keys[(a["employee_id"], a["date"], a["shift"], a["position"])] = a

    for ei, e in enumerate(emps):
        eid = e["id"]
        rot = "cajero" in e["positions"] or "pistero" in e["positions"]
        caj_principal = e["positions"].get("cajero") == "principal"
        lic_e = lic.get(eid, set())

        L0 = [m.NewBoolVar("") for _ in SH]
        m.AddExactlyOne(L0)
        c0 = m.NewIntVar(0, B, "")
        full0 = m.NewBoolVar("")
        m.Add(c0 == B).OnlyEnforceIf(full0)
        m.Add(c0 <= B - 1).OnlyEnforceIf(full0.Not())
        prevL, prevc, prevfull = L0, c0, full0

        for t, d in enumerate(days):
            is_free = d in free_set
            wv = {}
            for s in SH:
                v = m.NewBoolVar("")
                wv[s] = v
                ws[ei, t, s] = v
            if is_free:
                onlic = d in lic_e
                for s in SH:
                    xs = []
                    for p in ["cajero", "shop", "pistero", "auxiliar", "otro"]:
                        lvl = e["positions"].get(p)
                        is_locked = (eid, d, s, p) in locked_keys
                        if (onlic or (d, s) in closed_shift) and not is_locked:
                            continue
                        # "ocasional" (ej. Soledad en limpieza) sólo si el encargado lo aplicó/fijó
                        if not is_locked and (lvl in (None, "ocasional") or staffing.get(s, {}).get(p, 0) == 0):
                            continue
                        xv = m.NewBoolVar("")
                        x[ei, t, s, p] = xv
                        xs.append(xv)
                        if is_locked:
                            m.Add(xv == 1)
                        if (eid, d, s, p) in prefer:
                            pen.append((-W_KEEP, xv))
                        if lvl == "suplente":
                            pen.append((W_SUP_NOCHE if (s == "T3" and p == "cajero") else W_SUP, xv))
                    m.Add(wv[s] == sum(xs)) if xs else m.Add(wv[s] == 0)
            else:
                shifts = {a["shift"] for a in by_day.get(d, []) if a["employee_id"] == eid}
                latest = max(shifts, key=SH.index) if shifts else None
                for s in SH:
                    m.Add(wv[s] == (1 if s == latest else 0))
            work = sum(wv.values())
            m.Add(work <= 1)
            o = m.NewBoolVar("")
            m.Add(o + work == 1)
            off[ei, t] = o

            # --- estado de rotación ---
            L = [m.NewBoolVar("") for _ in SH]
            m.AddExactlyOne(L)
            sw = []
            for i, s in enumerate(SH):
                m.Add(L[i] >= wv[s])
                m.Add(L[i] == prevL[i]).OnlyEnforceIf(o)
                b = m.NewBoolVar("")
                m.AddBoolAnd([wv[s], prevL[i]]).OnlyEnforceIf(b)
                m.AddBoolOr([wv[s].Not(), prevL[i].Not()]).OnlyEnforceIf(b.Not())
                sw.append(b)
            same = m.NewBoolVar("")
            m.Add(same == sum(sw))
            switch = m.NewBoolVar("")
            m.Add(switch + same == work)

            if rot:
                c = m.NewIntVar(0, B, "")
                m.Add(c == prevc).OnlyEnforceIf(o)
                m.Add(c == 1).OnlyEnforceIf(switch)
                ext = m.NewBoolVar("")
                m.AddImplication(ext, same)
                m.AddImplication(ext, prevfull)
                m.Add(c == prevc + 1 - ext).OnlyEnforceIf(same)
                full = m.NewBoolVar("")
                m.Add(c == B).OnlyEnforceIf(full)
                m.Add(c <= B - 1).OnlyEnforceIf(full.Not())
                early = m.NewBoolVar("")
                m.Add(early >= switch - prevfull)
                wrong = m.NewBoolVar("")
                for i, s in enumerate(SH):
                    for s2 in SH:
                        if s2 not in (s, NEXT_SHIFT[s]):
                            m.Add(wrong >= wv[s2] + prevL[i] - 1)
                if is_free:
                    if caj_principal and strict:
                        m.Add(early == 0)
                        m.Add(wrong == 0)
                        m.Add(ext == 0)
                    elif caj_principal:
                        pen += [(W_CAJERO_RULE, early), (W_CAJERO_RULE, wrong), (W_CAJERO_RULE, ext)]
                    else:
                        pen += [(W_EARLY, early), (W_WRONG_ORDER, wrong), (W_EXT, ext)]
                prevc, prevfull = c, full
            elif is_free:
                pen.append((W_SWITCH_FIJO, switch))
            prevL = L

        # --- descansos y horas de 4 ---
        h4_terms = []
        h4_const = 0
        for t in range(1, len(days)):
            d = days[t]
            touches_free = d in free_set or days[t - 1] in free_set
            if touches_free:
                m.Add(ws[ei, t - 1, "T2"] + ws[ei, t, "T3"] <= 1)
            if d in month_set:
                for sp, sn in (("T2", "T1"), ("T1", "T3")):
                    hv = m.NewBoolVar("")
                    m.Add(hv >= ws[ei, t - 1, sp] + ws[ei, t, sn] - 1)
                    m.Add(hv <= ws[ei, t - 1, sp])
                    m.Add(hv <= ws[ei, t, sn])
                    h4_terms.append(hv)
                    if touches_free:
                        pen.append((W_H4, hv))
                if d not in free_set:
                    shifts = {a["shift"] for a in by_day.get(d, []) if a["employee_id"] == eid}
                    if {"T3", "T2"} <= shifts:
                        h4_const += 1
        excess = m.NewIntVar(0, 62, "")
        m.Add(excess >= sum(h4_terms) + h4_const - max_h4)
        pen.append((W_H4_EXCESS, excess))

        # --- libres del mes ---
        # Tope de libres = libres_objetivo prorrateado por los días que no está de licencia.
        # Los días del mes que no están armados (antes de "desde") no se cuentan.
        month_idx = [t for t, d in enumerate(days) if d in month_set]
        counted = [t for t in month_idx if days[t] in free_set or by_day.get(days[t])]
        avail = [t for t in counted if days[t] not in lic_e and days[t] not in closed_d]
        target = libres_target(target_libres, len(avail), len([t for t in month_idx if days[t] not in closed_d]))
        off_sum = sum(off[ei, t] for t in avail)
        under = m.NewIntVar(0, 62, "")
        m.Add(under >= target - off_sum)
        pen.append((W_LIBRES_FALTAN, under))
        over = m.NewIntVar(0, 62, "")
        m.Add(over >= off_sum - target)
        pen.append((W_LIBRES_EXCESO, over))

        # --- días seguidos ---
        K = max_seg + 1
        for t in range(K - 1, len(days)):
            if days[t] in free_set:
                v = m.NewIntVar(0, K, "")
                m.Add(v >= sum(1 - off[ei, k] for k in range(t - K + 1, t + 1)) - max_seg)
                pen.append((W_SEGUIDOS, v))

        # --- al menos un libre por semana (lun-dom) ---
        week = []
        for t, d in enumerate(days):
            week.append(t)
            if parse_date(d).weekday() == 6 or t == len(days) - 1:
                tt = [k for k in week if days[k] in month_set and days[k] not in lic_e and days[k] not in closed_d]
                if len(tt) >= 5 and any(days[k] in free_set for k in tt):
                    z = m.NewBoolVar("")
                    m.Add(sum(off[ei, k] for k in tt) + z >= 1)
                    pen.append((W_SEMANA_SIN_LIBRE, z))
                week = []

    # --- dotación ---
    unfilled = []
    for t, d in enumerate(days):
        if d not in free_set:
            continue
        for s in SH:
            if (d, s) in closed_shift:
                continue
            for p, need in staffing.get(s, {}).items():
                vars_ = [x[ei, t, s, p] for ei in range(len(emps)) if (ei, t, s, p) in x]
                n_locked = sum(1 for k in locked_keys if k[1] == d and k[2] == s and k[3] == p)
                n_locked_extra = max(0, n_locked - int(need))
                need = int(need)
                if need == 0 and not n_locked_extra:
                    continue
                # refuerzos: gente que sobra trabaja de más en vez de tener libres de más
                cap = 0 if night_bonus else EXTRA_CAP.get((s, p), EXTRA_CAP.get(("*", p), 0))
                cap = max(cap, n_locked_extra)
                u = m.NewIntVar(0, need, "")
                ex = m.NewIntVar(0, cap, "")
                m.Add(sum(vars_) + u - ex == need)
                pen.append(((130 if s == "T3" else 100) if night_bonus else W_UNFILLED, u))
                if cap:
                    pen.append((W_EXTRA_NOCHE if s == "T3" else W_EXTRA, ex))
                    if cap > 1:
                        ex2 = m.NewIntVar(0, cap, "")
                        m.Add(ex2 >= ex - 1)
                        pen.append((W_EXTRA * 3, ex2))
                unfilled.append((d, s, p, u))

    m.Minimize(sum(w * v for w, v in pen))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 7
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"ok": False, "msg": "No se encontró una combinación válida. Revisá los candados o los puestos."}

    out = []
    order = defaultdict(int)
    for (ei, t, s, p), v in x.items():
        if solver.Value(v):
            d = days[t]
            eid = emps[ei]["id"]
            out.append({"date": d, "shift": s, "position": p, "employee_id": eid,
                        "ord": order[(d, s, p)], "locked": bool(locked_keys.get((eid, d, s, p), {}).get("locked")),
                        "note": locked_keys.get((eid, d, s, p), {}).get("note", "")})
            order[(d, s, p)] += 1
    out.sort(key=lambda a: (a["date"], SH.index(a["shift"]), a["position"], a["ord"]))
    faltantes = [
        f"{fmt_day(d)} {SHIFT_NAME[s]}: falta {POS_NAME[p]} x{solver.Value(u)}"
        for d, s, p, u in unfilled if solver.Value(u)
    ]
    return {
        "ok": True,
        "assignments": out,
        "faltantes": faltantes,
        "optimo": status == cp_model.OPTIMAL,
        "costo": solver.ObjectiveValue(),
    }
