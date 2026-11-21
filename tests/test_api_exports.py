from io import BytesIO

import pytest
from openpyxl import load_workbook

from conftest import emp_id


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app as app_module
    import db
    import seed
    path = str(tmp_path / "api.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    seed.seed(db.connect(path))
    app_module.app.config["TESTING"] = True
    c = app_module.app.test_client()
    c.db = db.connect(path)
    return c


def login(client):
    r = client.post("/api/login", json={"username": "maycol", "password": "tifena"})
    assert r.status_code == 200
    return r.get_json()


def test_publico_solo_ve_publicado(client):
    r = client.get("/api/schedule?start=2026-11-16&end=2026-11-22").get_json()
    assert len(r["assignments"]) > 50
    client.post("/api/login", json={"username": "maycol", "password": "tifena"})
    client.post("/api/admin/month/status", json={"year": 2026, "month": 11, "status": "borrador"})
    client.post("/api/logout")
    r = client.get("/api/schedule?start=2026-11-16&end=2026-11-22").get_json()
    assert r["assignments"] == []


def test_admin_requiere_login(client):
    assert client.get("/api/admin/month?year=2026&month=12").status_code == 401


def test_otras_tareas_solo_para_si_mismo(client):
    me = login(client)
    maycol, luis = emp_id(client.db, "Maycol"), emp_id(client.db, "Luis")
    assert me["employee_id"] == maycol
    bad = {"year": 2026, "month": 12, "assignments": [{"date": "2026-12-03", "shift": "T1", "position": "otro", "employee_id": luis, "note": "Pasto"}]}
    assert client.put("/api/admin/month", json=bad).status_code == 403
    ok = {"year": 2026, "month": 12, "assignments": [{"date": "2026-12-03", "shift": "T1", "position": "otro", "employee_id": maycol, "note": "Pasto"}]}
    assert client.put("/api/admin/month", json=ok).status_code == 200


def test_exportar_excel_y_pdf(client):
    login(client)
    r = client.get("/api/admin/export/excel?start=2026-11-16&end=2026-11-22")
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.data))
    ws = wb.worksheets[0]
    titles = [c.value for c in ws["A"] if isinstance(c.value, str) and c.value.split(" ")[0] in
              ("LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO")]
    assert titles[0] == "LUNES 16/11" and len(titles) == 7
    assert ws["A2"].value == "TURNO 3 (Noche)" and ws["C2"].value == "TURNO 1 (Mañana)" and ws["E2"].value == "TURNO 2 (Tarde)" and ws["G2"].value == "LIBRES"
    r = client.get("/api/admin/export/pdf?start=2026-11-01&end=2026-11-30")
    assert r.status_code == 200 and r.data[:4] == b"%PDF"


def test_reflow_pasa_a_limpieza_y_no_toca_dias_anteriores(client):
    login(client)
    client.put("/api/admin/settings", json={"tiempo_generador": 15})
    r = client.post("/api/admin/generate", json={"year": 2026, "month": 12, "mode": "completar"}).get_json()
    month = r["assignments"]
    sol = emp_id(client.db, "Soledad")
    target = next(a for a in month if a["employee_id"] == sol and a["position"] == "shop" and a["date"] >= "2026-12-10")
    r2 = client.post("/api/admin/reflow", json={"year": 2026, "month": 12, "date": target["date"], "shift": target["shift"],
                                                "employee_id": sol, "to_position": "auxiliar", "assignments": month})
    assert r2.status_code == 200
    out = r2.get_json()
    mine = [a for a in out["assignments"] if a["date"] == target["date"] and a["employee_id"] == sol]
    assert mine and mine[0]["position"] == "auxiliar" and mine[0]["locked"]
    key = lambda a: (a["date"], a["shift"], a["position"], a["employee_id"])
    assert sorted(key(a) for a in month if a["date"] < target["date"]) == sorted(key(a) for a in out["assignments"] if a["date"] < target["date"])
    assert out["faltantes"] == []
    assert "suggestions" in out["analysis"]
