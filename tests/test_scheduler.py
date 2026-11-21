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
    return {"r": r, "analysis": rules.analyze(2026, 12, full, emps, lic, settings), "luis": luis, "maycol": maycol,
            "settings": settings}


def test_todo_cubierto_y_cajeros_estrictos(diciembre):
    r = diciembre["r"]
    assert r["faltantes"] == []
    assert r["estricto"] is True
    msgs = [i["msg"] for i in diciembre["analysis"]["issues"] if i["level"] in ("error", "warn")]
    assert not [m for m in msgs if "antes de cambiar" in m or "le toca" in m or "seguidas" in m], msgs


def test_nadie_supera_sus_libres_ni_horas4(diciembre):
    for row in diciembre["analysis"]["report"]:
        assert row["libres"] <= row["libres_objetivo"], row["name"]
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
    assert all(row["libres"] <= row["libres_objetivo"] for row in rep), [(x["name"], x["libres"]) for x in rep]
