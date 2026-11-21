import db
import rules
from conftest import emp_id


def a(d, shift, pos, eid):
    return {"date": d, "shift": shift, "position": pos, "employee_id": eid, "ord": 0}


def events(assigns):
    return [(e[0], e[2], e[3]) for e in rules.rest_events(rules.employee_timeline(assigns)[1])]


def test_hora4_tarde_a_manana():
    assert events([a("2026-12-01", "T2", "cajero", 1), a("2026-12-02", "T1", "cajero", 1)]) == [("hora4", "T2", "T1")]


def test_hora4_manana_a_noche_siguiente():
    # la noche del 02 empieza el 01 a las 22:00: sale 14:00 y entra 22:00
    assert events([a("2026-12-01", "T1", "cajero", 1), a("2026-12-02", "T3", "cajero", 1)]) == [("hora4", "T1", "T3")]


def test_noche_a_tarde_mismo_dia_es_hora4():
    assert events([a("2026-12-02", "T3", "cajero", 1), a("2026-12-02", "T2", "cajero", 1)]) == [("hora4", "T3", "T2")]


def test_tarde_a_noche_es_turno_pegado():
    assert events([a("2026-12-01", "T2", "cajero", 1), a("2026-12-02", "T3", "cajero", 1)]) == [("pegado", "T2", "T3")]


def test_rotacion_normal_sin_avisos():
    assert events([a("2026-12-01", "T3", "cajero", 1), a("2026-12-02", "T2", "cajero", 1), a("2026-12-04", "T1", "cajero", 1)]) == []


def test_libres_tope_y_licencia(conn):
    emps = db.get_employees(conn)
    settings = db.get_settings(conn)
    luis = emp_id(conn, "Luis")
    days = [db.iso(d) for d in db.date_range("2026-12-01", "2026-12-31")]
    # Luis trabaja sólo los primeros 20 días; todos los días tienen alguien cargado
    assigns = [a(d, "T1", "pistero", luis) for d in days[:20]]
    assigns += [a(d, "T1", "cajero", emp_id(conn, "Jairo")) for d in days]
    lic = [{"employee_id": luis, "start": "2026-12-26", "end": "2026-12-31"}]
    res = rules.analyze(2026, 12, assigns, emps, lic, settings)
    row = next(r for r in res["report"] if r["employee_id"] == luis)
    assert row["licencia"] == 6
    assert row["libres"] == 5            # 21..25 (los días de licencia no son libres)
    assert row["libres_objetivo"] == 5   # 6 * 25/31 redondeado
    assert not any("más de los" in i["msg"] and i["employee_id"] == luis for i in res["issues"])


def test_libres_de_mas_es_error(conn):
    emps = db.get_employees(conn)
    days = [db.iso(d) for d in db.date_range("2026-12-01", "2026-12-31")]
    assigns = [a(d, "T1", "cajero", emp_id(conn, "Jairo")) for d in days]
    res = rules.analyze(2026, 12, assigns, emps, [], db.get_settings(conn))
    luis = emp_id(conn, "Luis")
    assert any(i["level"] == "error" and i["employee_id"] == luis and "más de los" in i["msg"] for i in res["issues"])


def test_otras_tareas_solo_encargado(conn):
    emps = db.get_employees(conn)
    assigns = [a("2026-12-01", "T1", "otro", emp_id(conn, "Luis")), a("2026-12-01", "T1", "otro", emp_id(conn, "Maycol"))]
    res = rules.analyze(2026, 12, assigns, emps, [], db.get_settings(conn))
    errs = [i for i in res["issues"] if "otras tareas" in i["msg"]]
    assert [i["employee_id"] for i in errs] == [emp_id(conn, "Luis")]


def test_layout_planilla(conn):
    emps = db.get_employees(conn)
    assigns = db.get_assignments(conn, "2026-11-16", "2026-11-16")
    lay = rules.day_layout("2026-11-16", assigns, emps, db.license_days(db.get_licenses(conn)), db.get_settings(conn)["staffing"])
    assert lay["title"] == "LUNES 16/11"
    assert lay["T3"] == [("CAJERO:", "GUILLERMO"), ("PISTERO:", "LUIS")]
    assert lay["T1"][0] == ("CAJERO PISTA:", "JAIRO")
    assert lay["libres"][0] == "LAZARO"  # de licencia, primero
