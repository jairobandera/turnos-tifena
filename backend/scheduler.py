"""Generador automático de turnos con OR-Tools CP-SAT.

Arma los días "libres de datos" de un mes (o rehace desde una fecha) tomando como historial
fijo los días ya armados, para que la rotación, los bloques de 4 y las horas de 4 sigan sin cortes.
"""
from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from db import iso, parse_date, date_range, month_bounds, license_days
from rules import NEXT_SHIFT, POS_NAME, SHIFT_NAME, fmt_day, libres_target, special_hours, closed_days, staffing_for

SH = ["T3", "T1", "T2"]          # orden cronológico dentro del día
HISTORY_DAYS = 31          # se mira el mes anterior completo (rueda de noches, ciclos, horas de 4)

# Pesos del objetivo (más alto = más importante evitar)
W_UNFILLED = 10000
W_H4_EXCESS = 20000       # el máximo de horas de 4 de la configuración se respeta sí o sí
W_CAJERO_RULE = 400       # sólo si no se pudo cumplir estricto
W_LIBRES_FALTAN = 12000   # cada libre de menos (los 6 libres van antes que la rueda de noches)
W_SEGUIDOS = 6000         # máximo de días seguidos (si alguien tiene libres de más, antes cubre el hueco)
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
W_LIBRES_EXCESO = 8000    # nadie puede tener más libres de los que le corresponden
OTROS_SERVICIOS = "Otros servicios"
W_JORNAL = 40             # jornalero sin días fijos: cada día trabajado cuesta (sólo si hace falta)
W_JORNAL_DESVIO = 250     # jornalero con días por mes (ej. Soledad 15): cada día de más o de menos
W_NOCHE_RUEDA = 1500
W_NOCHE_BLOQUE = 2500     # pisteros: noches en bloque de 4 seguidas
      # pisteros: no volver a la noche hasta que hayan pasado los demás
W_NOCHE_LIBRE = 3000      # nadie tiene libre en medio de sus noches (sólo a mano, si alguien lo pide)
W_FASE_HIST = 500         # el ciclo de cada cajero sale de lo que ya trabajó (historial)
CICLO = 12                # cajeros: 4 noches + 4 días de tarde + 4 días de mañana (días de calendario)
W_OTROS = 30              # encargado en "Otros servicios" cuando sobra gente (así nadie pasa de 6 libres)
# Sin refuerzos automáticos: cada turno lleva exactamente su dotación. Los pisteros extra
# sólo existen en los días especiales que carga el encargado.



def next_allowed(shift, allowed):
    """Turno que sigue en la rotación salteando los que la persona no hace (ej. Maycol: Mañana -> Tarde)."""
    nxt = NEXT_SHIFT[shift]
    for _ in range(3):
        if nxt in allowed:
            return nxt
        nxt = NEXT_SHIFT[nxt]
    return shift


def generate(year, month, employees, assignments, licenses, settings, desde=None, mode="completar", time_limit=None,
             closures=(), extras=None, jornales=None):
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
    # se mantienen las "otras tareas" que puso el encargado a mano (las "Otros servicios" automáticas se rehacen)
    manual_task = lambda a: a["position"] == "otro" and (a.get("locked") or (a.get("note") or "") != OTROS_SERVICIOS)
    locked = [a for a in assignments if a["date"] in free_set and (manual_task(a) or (mode == "rehacer" and a.get("locked")))]
    prefer = {(a["employee_id"], a["date"], a["shift"], a["position"]) for a in assignments if a["date"] in free_set}

    # el modelo ve siempre el mes completo (para contar bien los libres) + historial previo
    start = min(parse_date(free[0]) - timedelta(days=HISTORY_DAYS), first)
    days = [iso(d) for d in date_range(start, last)]
    emps = [e for e in employees if e["active"]]
    if jornales:
        # jornales sólo para este mes (ej. lo que sugiere el aviso para Soledad); 0 = sólo lo necesario
        jornales = {int(k): int(v) for k, v in jornales.items()}
        emps = [{**e, "jornal_dias": jornales[e["id"]] if jornales[e["id"]] > 0 else 0} if e["id"] in jornales else e
                for e in emps]
    if settings.get("encargado_fuera"):
        # el encargado queda fuera de la planilla: sus días de trabajo van a "Otros servicios"
        emps = [{**e, "fuera": True} if "otro" in e["positions"] else e for e in emps]
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
    extras = extras or {}
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
            r1 = _solve(strict, cajeros, only_caj, locked_caj, *common, time_limit * 0.3, night_bonus=True, prefer=prefer,
                        extras={})
            if r1["ok"]:
                forced += [a for a in r1["assignments"] if not a.get("locked")]
        result = _solve(strict, emps, staffing, forced, *common, time_limit * 0.7, prefer=prefer, extras=extras)
        if result["ok"] and cajeros and (result["faltantes"] or result.get("libres_cortos")):
            # quedó un hueco (o alguien sin sus libres) por haber fijado antes a los cajeros:
            # probar todo junto, arrancando desde la solución que ya hay, y quedarse con lo mejor
            hints = {(a["employee_id"], a["date"], a["shift"], a["position"]) for a in result["assignments"]}
            alt = _solve(strict, emps, staffing, list(locked), *common, time_limit, prefer=prefer, extras=extras,
                         hints=hints)
            # antes que dejar a alguien sin sus libres, preferimos dejar el hueco a la vista (el encargado decide)
            key = lambda r: (bool(r.get("libres_cortos")), len(r["faltantes"]), r["costo"])
            if alt["ok"] and key(alt) < key(result):
                result = alt
        if result["ok"]:
            result["estricto"] = strict
            break
    if not result["ok"]:
        return result

    # días del mes que quedan como estaban + los generados
    kept = [a for a in assignments if a["date"] in month_days and a["date"] not in free_set]
    result["assignments"] = kept + result["assignments"]
    result["generated_days"] = free
    if result.get("holes"):
        hist = [a for a in assignments if a["date"] < iso(first)]
        _repair_holes(result, year, month, hist, emps, licenses, settings, closures, extras or {}, free_set)
    return result


def _repair_holes(result, year, month, hist, emps, licenses, settings, closures, extras, free_set):
    """Repaso final: si quedó un puesto sin cubrir y hay alguien con libres de más que puede hacerlo,
    se lo pone (directo, o con un enroque: un suplente pasa al puesto vacío y el de libres de más ocupa su lugar).
    Nunca pasa el máximo de horas de 4 ni deja turnos pegados."""
    from rules import analyze, rest_events, employee_timeline, staffing_for as need_for
    max_h4 = int(settings.get("max_horas4", 2))
    lic = license_days(licenses)
    special = special_hours(*month_bounds(year, month), closures)
    by_id = {e["id"]: e for e in emps}
    A = result["assignments"]

    def can(e, d, s, p):
        return (e["positions"].get(p) in ("principal", "suplente") and s in (e.get("shifts") or SH)
                and d not in lic.get(e["id"], ()) and special.get(d, {}).get(s, "") is not None
                and not e.get("jornalero") and not e.get("fuera"))

    def rest_ok(eid, extra):
        tl = employee_timeline(hist + A + [extra], closures).get(eid, [])
        ev = rest_events(tl)
        if any(k == "pegado" for k, *_ in ev):
            return False
        first, last = month_bounds(year, month)
        return sum(1 for k, d, *_ in ev if k == "hora4" and iso(first) <= d <= iso(last)) <= max_h4

    still = []
    for d, s, p, n in result["holes"]:
        for _ in range(n):
            if d not in free_set:
                still.append((d, s, p))
                continue
            rep = analyze(year, month, hist + A, emps, licenses, settings, closures, extras)["report"]
            spare = [r["employee_id"] for r in sorted(rep, key=lambda r: r["libres_objetivo"] - r["libres"])
                     if not r["jornalero"] and r["libres"] > r["libres_objetivo"]]
            busy = {a["employee_id"] for a in A if a["date"] == d}
            done = False
            # 1) directo: alguien con libres de más que sabe hacer ese puesto
            for eid in spare:
                e = by_id.get(eid)
                new = {"date": d, "shift": s, "position": p, "employee_id": eid, "ord": 9, "locked": False, "note": ""}
                if e and eid not in busy and can(e, d, s, p) and rest_ok(eid, new):
                    A.append(new)
                    done = True
                    break
            # 2) enroque: X (que está en ese turno y sabe hacer p) pasa a p; Y (libres de más) ocupa el lugar de X
            if not done:
                for a in [a for a in A if a["date"] == d and a["shift"] == s and a["position"] not in (p, "otro")]:
                    x = by_id.get(a["employee_id"])
                    if not x or not can(x, d, s, p) or a.get("locked"):
                        continue
                    q = a["position"]
                    for eid in spare:
                        y = by_id.get(eid)
                        new = {"date": d, "shift": s, "position": q, "employee_id": eid, "ord": 9, "locked": False, "note": ""}
                        if y and eid not in busy and can(y, d, s, q) and rest_ok(eid, new):
                            a["position"] = p
                            A.append(new)
                            done = True
                            break
                    if done:
                        break
            if not done:
                still.append((d, s, p))
    need_ok = lambda d, s, p: need_for(settings["staffing"], extras, d, s).get(p, 0)
    result["faltantes"] = [f"{fmt_day(d)} {SHIFT_NAME[s]}: falta {POS_NAME[p]} x1" for d, s, p in still
                           if sum(1 for a in A if a["date"] == d and a["shift"] == s and a["position"] == p) < need_ok(d, s, p)]
    result["holes"] = still


def _solve(strict, emps, staffing, locked, days, free_set, by_day, lic, B, max_h4,
           target_libres, max_seg, month_days, closed_shift, closed_d, time_limit, night_bonus=False,
           prefer=frozenset(), extras=None, hints=None):
    m = cp_model.CpModel()
    pen = []
    shortfalls = []
    x = {}
    ws = {}
    off = {}
    month_set = set(month_days)
    locked_keys = {}
    for a in locked:
        locked_keys[(a["employee_id"], a["date"], a["shift"], a["position"])] = a

    # Rueda de noches de los pisteros: cada uno hace su bloque y no vuelve hasta que pasen todos.
    # En cualquier ventana de (bloque x cantidad de pisteros) días, nadie hace más de un bloque de noches.
    def night_pistero(e):
        """Pisteros que hacen noches: van en bloques de 4 seguidas."""
        return (e["positions"].get("pistero") in ("principal", "suplente") and "T3" in (e.get("shifts") or SH)
                and not e.get("jornalero") and not e.get("fuera") and e["positions"].get("cajero") != "principal")

    def in_rueda(e):
        """La rueda la hacen los pisteros que no son suplentes de cajero (esos quedan para cubrir cajeros)."""
        return night_pistero(e) and "cajero" not in e["positions"]
    night_pool = [e for e in emps if in_rueda(e)] if not night_bonus else []
    rueda = B * len(night_pool)

    for ei, e in enumerate(emps):
        eid = e["id"]
        fuera = bool(e.get("fuera"))
        encargado = "otro" in e["positions"]
        jornal_dias = int(e.get("jornal_dias") or 0)
        rot = ("cajero" in e["positions"] or "pistero" in e["positions"]) and not fuera
        caj_principal = e["positions"].get("cajero") == "principal"
        # Cajeros titulares: ciclo fijo de calendario de 12 días. 4 noches seguidas (sin libres),
        # 4 días de tarde y 4 de mañana (los libres caen ahí); el día 4 de la mañana a las 22:00 entra la noche.
        phase_mode = caj_principal and strict and not fuera
        rot = rot and not phase_mode
        jornalero = bool(e.get("jornalero"))
        allowed = set(e.get("shifts") or SH)   # turnos que puede hacer (ej. Belén sólo la mañana)
        fuera_shift = "T1" if "T1" in allowed else next(s for s in SH if s in allowed)
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
                        if fuera and not is_locked:
                            if p != "otro" or s != fuera_shift:
                                continue
                            xv = m.NewBoolVar("")
                            x[ei, t, s, p] = xv
                            xs.append(xv)
                            continue
                        if p == "otro" and not is_locked:
                            continue   # "otras tareas" sólo las pone el encargado a mano (o con "fuera de planilla")
                        # "ocasional" (ej. Soledad en limpieza) sólo si el encargado lo aplicó/fijó
                        if not is_locked and (lvl in (None, "ocasional") or s not in allowed
                                              or staffing_for(staffing, extras, d, s).get(p, 0) == 0):
                            continue
                        xv = m.NewBoolVar("")
                        x[ei, t, s, p] = xv
                        xs.append(xv)
                        if is_locked:
                            m.Add(xv == 1)
                        if (eid, d, s, p) in prefer:
                            pen.append((-W_KEEP, xv))
                        if hints and (eid, d, s, p) in hints:
                            m.AddHint(xv, 1)
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
            if jornalero and not jornal_dias and is_free and d in month_set:
                pen.append((W_JORNAL, work))
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

            prevfull_before = prevfull
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
                        if s2 not in (s, next_allowed(s, allowed)):
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
            elif is_free and not phase_mode:
                pen.append((W_SWITCH_FIJO, switch))
            if rot and is_free:
                # en la noche nadie tiene libre: libre después de una noche sin haber completado el bloque
                gap = m.NewBoolVar("")
                m.Add(gap >= o + prevL[SH.index("T3")] + (1 - prevfull_before) - 2)
                pen.append((W_NOCHE_LIBRE, gap))
            prevL = L

        # --- ciclo de calendario de los cajeros titulares ---
        if phase_mode:
            z = [m.NewBoolVar("") for _ in range(CICLO)]
            m.AddExactlyOne(z)
            mismatches, observed = {}, 0
            for k in range(CICLO):
                mismatch = 0
                for t, d in enumerate(days):
                    blk = ((k + t) % CICLO) // B      # 0 = noche, 1 = tarde, 2 = mañana
                    if d in free_set:
                        if blk == 0:
                            if d in lic_e or (d, "T3") in closed_shift:
                                m.Add(ws[ei, t, "T1"] + ws[ei, t, "T2"] == 0).OnlyEnforceIf(z[k])
                            else:
                                m.Add(ws[ei, t, "T3"] == 1).OnlyEnforceIf(z[k])
                        elif blk == 1:
                            m.Add(ws[ei, t, "T1"] + ws[ei, t, "T3"] == 0).OnlyEnforceIf(z[k])
                        else:
                            m.Add(ws[ei, t, "T2"] + ws[ei, t, "T3"] == 0).OnlyEnforceIf(z[k])
                    elif by_day.get(d):
                        # día ya armado: ¿coincide con esta fase?
                        sh = {a["shift"] for a in by_day[d] if a["employee_id"] == eid}
                        if blk == 0:
                            bad = "T3" not in sh and d not in lic_e and (d, "T3") not in closed_shift
                        elif blk == 1:
                            bad = bool(sh & {"T1", "T3"})
                        else:
                            bad = bool(sh & {"T2", "T3"})
                        mismatch += 1 if bad else 0
                        if k == 0 and sh:
                            observed += 1
                mismatches[k] = mismatch
                if mismatch:
                    pen.append((W_FASE_HIST * mismatch, z[k]))
            if observed >= B:
                # lo ya armado define el ciclo: se sigue sí o sí desde ahí (no se corre por un cierre o un libre)
                best = min(mismatches.values())
                for k, mm in mismatches.items():
                    if mm > best:
                        m.Add(z[k] == 0)

        # --- rueda de noches (pisteros) ---
        if night_pistero(e) and not night_bonus:
            # las noches van en bloque de 4 seguidas: si empieza una noche, siguen 3 más (los cierres cuentan)
            for t in range(len(days)):
                if days[t] not in free_set:
                    continue
                n_t = ws[ei, t, "T3"]
                prev = ws[ei, t - 1, "T3"] if t > 0 else 0
                start = m.NewBoolVar("")
                m.Add(start >= n_t - prev)
                for k in range(1, B):
                    if t + k >= len(days):
                        break
                    dk = days[t + k]
                    if (dk, "T3") in closed_shift or dk in lic_e:
                        continue
                    short = m.NewBoolVar("")
                    m.Add(short >= start - ws[ei, t + k, "T3"])
                    pen.append((W_NOCHE_BLOQUE, short))
                if t + B < len(days):
                    five = m.NewBoolVar("")
                    m.Add(five >= sum(ws[ei, t + k, "T3"] for k in range(B + 1)) - B)
                    pen.append((W_NOCHE_BLOQUE // 2, five))
        if rueda and night_pistero(e) and rueda <= len(days):
            for t_end in range(rueda - 1, len(days)):
                if days[t_end] not in free_set:
                    continue
                win = range(t_end - rueda + 1, t_end + 1)
                extra_n = m.NewIntVar(0, rueda, "")
                m.Add(extra_n >= sum(ws[ei, k, "T3"] for k in win) - B)
                # la reserva (suplentes de cajero) también se empareja, pero con menos peso que los libres
                pen.append((W_NOCHE_RUEDA if in_rueda(e) else W_NOCHE_RUEDA // 5, extra_n))

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
        if jornalero and jornal_dias:
            # jornalero con días por mes: lo más cerca posible de esos días (prorrateado si no está todo el mes)
            open_days = len([t for t in month_idx if days[t] not in closed_d])
            goal = int(jornal_dias * len(avail) / open_days + 0.5) if open_days else jornal_dias
            worked = sum(1 - off[ei, t] for t in avail)
            # lo configurado es un TOPE: nunca más jornales de los puestos (si falta gente, queda "Falta" a la vista)
            fixed_worked = sum(1 for t in avail if days[t] not in free_set
                               and any(a["employee_id"] == eid for a in by_day.get(days[t], [])))
            m.Add(worked <= max(goal, fixed_worked))
            dev = m.NewIntVar(0, 62, "")
            m.Add(dev >= worked - goal)
            m.Add(dev >= goal - worked)
            pen.append((W_JORNAL_DESVIO, dev))
        if jornalero:
            avail = []   # el jornalero no tiene mínimo ni tope de libres
        if not jornalero:
            # si alguien tiene que perder libres, que sea parejo: cada libre de menos cuesta más que el anterior
            for step in range(0, 6):
                under = m.NewIntVar(0, 62, "")
                m.Add(under >= target - step - off_sum)
                pen.append((W_LIBRES_FALTAN, under))
                if step == 1:
                    shortfalls.append(under)   # le faltan 2 libres o más
            # libres de más (sólo si sobra gente): parejos, el segundo de más cuesta más que el primero
            for step in range(0, 4):
                over = m.NewIntVar(0, 62, "")
                m.Add(over >= off_sum - target - step)
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
            for p, need in staffing_for(staffing, extras, d, s).items():
                vars_ = [x[ei, t, s, p] for ei in range(len(emps)) if (ei, t, s, p) in x]
                n_locked = sum(1 for k in locked_keys if k[1] == d and k[2] == s and k[3] == p)
                need = max(int(need), n_locked)   # dotación exacta (más sólo si el encargado lo fijó)
                if need == 0:
                    continue
                u = m.NewIntVar(0, need, "")
                m.Add(sum(vars_) + u == need)
                pen.append(((130 if s == "T3" else 100) if night_bonus else W_UNFILLED, u))
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
                        "note": locked_keys.get((eid, d, s, p), {}).get("note", "") or (OTROS_SERVICIOS if p == "otro" else "")})
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
        "holes": [(d, s, p, solver.Value(u)) for d, s, p, u in unfilled if solver.Value(u)],
        "optimo": status == cp_model.OPTIMAL,
        "libres_cortos": any(solver.Value(u) > 0 for u in shortfalls),
        "costo": solver.ObjectiveValue(),
    }
