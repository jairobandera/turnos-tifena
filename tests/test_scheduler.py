"""Genera diciembre 2026 a partir de las planillas reales (16-29/11) y verifica las reglas."""
from datetime import timedelta

import pytest

import db
import rules
import scheduler
from conftest import emp_id


@pytest.fixture
def diciembre(conn):
    luis, maycol = emp_id(conn, "Elias"), emp_id(conn, "Maycol")  # "luis" = empleado con vacaciones de prueba
    conn.execute("INSERT INTO licenses(employee_id, start, end, kind) VALUES (?, '2026-12-10', '2026-12-19', 'Vacaciones')", (luis,))
    conn.commit()
    # el encargado se asigna "corte de pasto" el 05/12 a la mañana
    db.replace_assignments(conn, "2026-12-01", "2026-12-31", [
        {"date": "2026-12-05", "shift": "T1", "position": "otro", "employee_id": maycol, "note": "Corte de pasto"}])
    emps, settings, lic = db.get_employees(conn), db.get_settings(conn), db.get_licenses(conn)
    settings["tiempo_generador"] = 30
    f, l = db.month_bounds(2026, 11)
    r = scheduler.generate(2026, 11, emps, db.get_assignments(conn, f, l), lic, settings, desde="2026-11-30")
    db.replace_assignments(conn, f, l, r["assignments"])
    f, l = db.month_bounds(2026, 12)
    r = scheduler.generate(2026, 12, emps, db.get_assignments(conn, f - timedelta(days=20), l), lic, settings)
    assert r["ok"]
    full = db.get_assignments(conn, f - timedelta(days=20), f - timedelta(days=1)) + r["assignments"]
    return {"r": r, "analysis": rules.analyze(2026, 12, full, emps, lic, settings), "luis": luis, "maycol": maycol, "conn": conn,
            "settings": settings}


def test_todo_cubierto_y_cajeros_estrictos(diciembre):
    r = diciembre["r"]
    assert r["faltantes"] == []
    assert r["estricto"] is True
    msgs = [i["msg"] for i in diciembre["analysis"]["issues"] if i["level"] in ("error", "warn")]
    assert not [m for m in msgs if "antes de cambiar" in m or "le toca" in m or "seguidas" in m], msgs


def test_nadie_supera_sus_libres_ni_horas4(diciembre):
    for row in diciembre["analysis"]["report"]:
        if row["jornalero"]:
            continue   # los jornaleros (Soledad, Fabiana) no tienen tope de libres
        # si sobra gente puede haber libres de más, pero repartidos (nadie con más de 2 de más)
        assert row["libres"] <= row["libres_objetivo"] + 2, row["name"]
        assert row["horas4"] <= diciembre["settings"]["max_horas4"], row["name"]


def test_licencia_respetada(diciembre):
    luis = diciembre["luis"]
    assert not [a for a in diciembre["r"]["assignments"] if a["employee_id"] == luis and "2026-12-10" <= a["date"] <= "2026-12-19"]
    row = next(x for x in diciembre["analysis"]["report"] if x["employee_id"] == luis)
    assert row["libres_objetivo"] == 4  # 6 * 21/31


def test_otras_tareas_se_mantienen_y_no_cubren_puesto(diciembre):
    maycol = diciembre["maycol"]
    dia = [a for a in diciembre["r"]["assignments"] if a["date"] == "2026-12-05"]
    mine = [a for a in dia if a["employee_id"] == maycol]
    assert len(mine) == 1 and mine[0]["position"] == "otro" and mine[0]["note"] == "Corte de pasto"
    pisteros_t1 = [a for a in dia if a["shift"] == "T1" and a["position"] == "pistero"]
    assert len(pisteros_t1) >= 2


def test_sin_turnos_pegados(diciembre):
    assert not [i for i in diciembre["analysis"]["issues"] if "sin descanso" in i["msg"]]


def test_rehacer_final_de_mes_respeta_tope_de_libres(conn):
    """Rehacer sólo los últimos días tiene que seguir viendo los libres de todo el mes."""
    emps, settings, lic = db.get_employees(conn), db.get_settings(conn), db.get_licenses(conn)
    f, l = db.month_bounds(2026, 12)
    r = scheduler.generate(2026, 12, emps, db.get_assignments(conn, f - timedelta(days=20), l), lic, settings)
    db.replace_assignments(conn, f, l, r["assignments"])
    cur = db.get_assignments(conn, f - timedelta(days=20), l)
    r2 = scheduler.generate(2026, 12, emps, cur, lic, settings, desde="2026-12-28", mode="rehacer")
    assert r2["ok"] and r2["generated_days"] == ["2026-12-28", "2026-12-29", "2026-12-30", "2026-12-31"]
    full = db.get_assignments(conn, f - timedelta(days=20), f - timedelta(days=1)) + r2["assignments"]
    rep = rules.analyze(2026, 12, full, emps, lic, settings)["report"]
    assert all(row["libres"] <= row["libres_objetivo"] + 2 for row in rep if not row["jornalero"]), [(x["name"], x["libres"]) for x in rep]


def test_generador_no_pone_ocasionales_solo(diciembre):
    sol = [e["id"] for e in db.get_employees(diciembre["conn"]) if e["name"] == "Soledad"][0]
    assert not [a for a in diciembre["r"]["assignments"] if a["employee_id"] == sol and a["position"] == "auxiliar"]


def test_generador_respeta_cierres(conn):
    emps, settings, lic = db.get_employees(conn), db.get_settings(conn), db.get_licenses(conn)
    f, l = db.month_bounds(2026, 12)
    cl = rules.expand_closures(db.get_closures(conn), f - timedelta(days=20), l + timedelta(days=1))
    r = scheduler.generate(2026, 12, emps, db.get_assignments(conn, f - timedelta(days=20), l), lic, settings, closures=cl)
    assert r["ok"] and r["faltantes"] == []
    assert not [x for x in r["assignments"] if x["date"] == "2026-12-25"]
    assert {x["shift"] for x in r["assignments"] if x["date"] == "2026-12-26"} == {"T1", "T2", "T3"}
    full = db.get_assignments(conn, f - timedelta(days=20), f - timedelta(days=1)) + r["assignments"]
    rep = rules.analyze(2026, 12, full, emps, lic, settings, cl)["report"]
    assert all(x["libres"] <= x["libres_objetivo"] + 2 for x in rep if not x["jornalero"])


def test_reglas_fijas_y_sin_refuerzos(diciembre):
    """Belén sólo mañana, Soledad sólo tarde, Maycol nunca noche; nunca 2 en el spot ni pisteros de más."""
    from collections import Counter
    conn = diciembre["conn"]
    names = {e["id"]: e["name"] for e in db.get_employees(conn)}
    st = db.get_settings(conn)["staffing"]
    gen = [a for a in diciembre["r"]["assignments"] if a["date"] >= "2026-12-01"]
    assert not [a for a in gen if names[a["employee_id"]] == "Belen" and a["shift"] != "T1"]
    assert not [a for a in gen if names[a["employee_id"]] == "Soledad" and a["shift"] != "T2"]
    assert not [a for a in gen if names[a["employee_id"]] == "Maycol" and a["shift"] == "T3"]
    cnt = Counter((a["date"], a["shift"], a["position"]) for a in gen if a["position"] != "otro")
    assert all(n <= st[s].get(p, 0) for (d, s, p), n in cnt.items()), [k for k, n in cnt.items() if n > st[k[1]].get(k[2], 0)]


def test_jornalera_absorbe_lo_que_sobra(diciembre):
    rep = {r["name"]: r for r in diciembre["analysis"]["report"]}
    assert rep["Soledad"]["jornalero"]
    assert not [i for i in diciembre["analysis"]["issues"] if "Soledad" in i["msg"] and "libres" in i["msg"]]


def _generar(conn, extras=None):
    emps, settings, lic = db.get_employees(conn), db.get_settings(conn), db.get_licenses(conn)
    f, l = db.month_bounds(2026, 12)
    cl = rules.expand_closures(db.get_closures(conn), f - timedelta(days=20), l + timedelta(days=1))
    return scheduler.generate(2026, 12, emps, db.get_assignments(conn, f - timedelta(days=20), l), lic, settings,
                              closures=cl, extras=extras or {})


def test_dia_especial_agrega_pistero(conn):
    conn.execute("INSERT INTO special_days(date, shift, extra_pisteros, note, yearly) VALUES ('2026-12-20', 'T2', 1, 'Feriado', 0)")
    conn.commit()
    ex = rules.expand_special_days(db.get_special_days(conn), "2026-12-01", "2026-12-31")
    assert ex == {("2026-12-20", "T2"): 1}
    r = _generar(conn, ex)
    assert r["ok"]
    pist = [a for a in r["assignments"] if a["date"] == "2026-12-20" and a["shift"] == "T2" and a["position"] == "pistero"]
    assert len(pist) == 3
    otros = [a for a in r["assignments"] if a["date"] == "2026-12-21" and a["shift"] == "T2" and a["position"] == "pistero"]
    assert len(otros) == 2


def test_encargado_fuera_de_planilla(conn):
    db.save_settings(conn, {"encargado_fuera": True})
    r = _generar(conn)
    assert r["ok"]
    maycol = [e["id"] for e in db.get_employees(conn) if e["name"] == "Maycol"][0]
    mine = [a for a in r["assignments"] if a["employee_id"] == maycol and a["date"] >= "2026-12-01"]
    assert mine and all(a["position"] == "otro" and a["note"] == "Otros servicios" for a in mine)



def test_auxiliares_fabiana_cubre_libres(diciembre):
    """Silvia y Valeria con sus 6 libres; Fabiana (jornalera) cubre lo que falta."""
    rep = {r["name"]: r for r in diciembre["analysis"]["report"]}
    assert rep["Fabiana"]["jornalero"]
    assert rep["Silvia"]["libres"] == rep["Silvia"]["libres_objetivo"]
    assert rep["Valeria"]["libres"] == rep["Valeria"]["libres_objetivo"]


def test_ciclo_de_12_dias_de_los_cajeros(conn):
    """Bloques de 4 días de calendario: Jairo mañana 28/11-01/12 y noche desde el 02/12; noches siempre de un titular."""
    emps, settings, lic = db.get_employees(conn), db.get_settings(conn), db.get_licenses(conn)
    for y, m, desde in [(2026, 11, "2026-11-30"), (2026, 12, None)]:
        f, l = db.month_bounds(y, m)
        cl = rules.expand_closures(db.get_closures(conn), f - timedelta(days=20), l + timedelta(days=1))
        r = scheduler.generate(y, m, emps, db.get_assignments(conn, f - timedelta(days=20), l), lic, settings,
                               desde=desde, closures=cl)
        db.replace_assignments(conn, f, l, r["assignments"])
    A = db.get_assignments(conn, "2026-11-28", "2026-12-31")
    ids = {e["name"]: e["id"] for e in emps}
    jairo = {a["date"]: a["shift"] for a in A if a["employee_id"] == ids["Jairo"]}
    assert all(jairo.get(d) in (None, "T1") for d in ["2026-11-28", "2026-11-29", "2026-11-30", "2026-12-01"])
    assert all(jairo.get(d) == "T3" for d in ["2026-12-02", "2026-12-03", "2026-12-04", "2026-12-05"])
    titulares = {ids["Jairo"], ids["Guillermo"], ids["Fabricio"]}
    for d in db.date_range("2026-12-01", "2026-12-31"):
        ds = d.isoformat()
        if ds == "2026-12-25":
            continue   # estación cerrada
        assert any(a["date"] == ds and a["shift"] == "T3" and a["employee_id"] in titulares for a in A), ds
    # nadie con libre en medio de sus noches
    f, l = db.month_bounds(2026, 12)
    cl = rules.expand_closures(db.get_closures(conn), f - timedelta(days=20), l + timedelta(days=1))
    an = rules.analyze(2026, 12, db.get_assignments(conn, f - timedelta(days=14), l), emps, lic, settings, cl)
    assert not [i for i in an["issues"] if "en medio de sus noches" in i["msg"]]


def test_noches_de_pisteros_en_bloques_y_parejas(conn):
    """Pisteros: noches en bloques de 4 seguidas y nadie repite muy seguido."""
    from collections import Counter
    emps, settings, lic = db.get_employees(conn), db.get_settings(conn), db.get_licenses(conn)
    f, l = db.month_bounds(2026, 12)
    cl = rules.expand_closures(db.get_closures(conn), f - timedelta(days=40), l + timedelta(days=1))
    r = scheduler.generate(2026, 12, emps, db.get_assignments(conn, f - timedelta(days=35), l), lic, settings, closures=cl)
    assert r["ok"]
    noches = Counter(a["employee_id"] for a in r["assignments"] if a["shift"] == "T3" and a["position"] == "pistero")
    assert max(noches.values()) <= 8, noches
    names = {e["id"]: e["name"] for e in emps}
    sueltas = 0
    for eid in noches:
        ds = sorted(a["date"] for a in r["assignments"] if a["employee_id"] == eid and a["shift"] == "T3")
        runs, run = [], 1
        for p, q in zip(ds, ds[1:]):
            if (db.parse_date(q) - db.parse_date(p)).days == 1 or q == "2026-12-26" and p == "2026-12-24":
                run += 1
            else:
                runs.append(run)
                run = 1
        runs.append(run)
        sueltas += sum(1 for x in runs if x == 1)
    assert sueltas <= 1, sueltas   # como mucho una noche suelta en todo el mes
