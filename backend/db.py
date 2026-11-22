"""Acceso a SQLite y helpers de lectura/escritura compartidos por la API y el generador."""
import json
import os
import sqlite3
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("TURNOS_DB", os.path.join(BASE_DIR, "data", "turnos.db"))

SHIFTS = ["T3", "T1", "T2"]  # orden en la planilla
POSITIONS = ["cajero", "shop", "pistero", "auxiliar"]   # puestos que cuentan para la dotación
TASK = "otro"                                           # otras tareas: trabaja pero no ocupa puesto
ASSIGNABLE = POSITIONS + [TASK]

DEFAULT_SETTINGS = {
    "max_horas4": 2,
    "libres_objetivo": 6,
    "max_libres_trabajados": 1,   # si hace falta, cuántos de sus libres puede trabajar alguien en el mes
    "bloque": 4,
    "cajeros_estrictos": True,
    "max_dias_seguidos": 6,
    "tiempo_generador": 45,
    "encargado_fuera": False,   # el encargado no entra en la planilla: queda en "Otros servicios"
    "staffing": {
        "T3": {"cajero": 1, "pistero": 1},
        "T1": {"cajero": 1, "shop": 1, "pistero": 2, "auxiliar": 1},
        "T2": {"cajero": 1, "shop": 1, "pistero": 2, "auxiliar": 1},
    },
}


# La estación cierra siempre Nochebuena y Fin de Año de 20:00 a 20:00 del día siguiente.
DEFAULT_CLOSURES = [
    ("2026-12-24T20:00", "2026-12-25T20:00", "Navidad"),
    ("2026-12-31T20:00", "2027-01-01T20:00", "Año Nuevo"),
]


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn):
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8") as f:
        conn.executescript(f.read())
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(assignments)")}
    if "note" not in cols:
        conn.execute("ALTER TABLE assignments ADD COLUMN note TEXT NOT NULL DEFAULT ''")
    if not conn.execute("SELECT 1 FROM settings WHERE key = 'closures_seeded'").fetchone():
        for start, end, note in DEFAULT_CLOSURES:
            conn.execute("INSERT INTO closures(start, end, note, yearly) VALUES (?, ?, ?, 1)", (start, end, note))
        conn.execute("INSERT INTO settings(key, value) VALUES ('closures_seeded', 'true')")
    ecols = {r["name"] for r in conn.execute("PRAGMA table_info(employees)")}
    if "shifts" not in ecols:
        conn.execute("ALTER TABLE employees ADD COLUMN shifts TEXT NOT NULL DEFAULT 'T3,T1,T2'")
    if "jornalero" not in ecols:
        conn.execute("ALTER TABLE employees ADD COLUMN jornalero INTEGER NOT NULL DEFAULT 0")
    if "jornal_dias" not in ecols:
        conn.execute("ALTER TABLE employees ADD COLUMN jornal_dias INTEGER NOT NULL DEFAULT 0")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "employee_id" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN employee_id INTEGER REFERENCES employees(id)")
    conn.commit()


# ---------- fechas ----------

def parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) else s


def iso(d):
    return d.isoformat()


def date_range(start, end):
    d = parse_date(start)
    end = parse_date(end)
    while d <= end:
        yield d
        d += timedelta(days=1)


def month_bounds(year, month):
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    return first, nxt - timedelta(days=1)


# ---------- settings ----------

def get_settings(conn):
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    for row in conn.execute("SELECT key, value FROM settings"):
        s[row["key"]] = json.loads(row["value"])
    return s


def save_settings(conn, values):
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, json.dumps(v)))
    conn.commit()


# ---------- empleados ----------

def get_employees(conn, include_inactive=True):
    emps = []
    q = "SELECT * FROM employees" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY name"
    for row in conn.execute(q):
        pos = {
            r["position"]: r["level"]
            for r in conn.execute("SELECT position, level FROM employee_positions WHERE employee_id = ?", (row["id"],))
        }
        shifts = [s for s in (row["shifts"] or "").split(",") if s in SHIFTS] or list(SHIFTS)
        emps.append({"id": row["id"], "name": row["name"], "active": bool(row["active"]), "positions": pos,
                     "shifts": shifts, "jornalero": bool(row["jornalero"]), "jornal_dias": int(row["jornal_dias"] or 0)})
    return emps


def _shifts_str(data, default="T3,T1,T2"):
    if "shifts" not in data:
        return None if default is None else default
    lst = [s for s in SHIFTS if s in (data.get("shifts") or [])]
    if not lst:
        raise ValueError("Tiene que poder hacer al menos un turno")
    return ",".join(lst)


def save_employee(conn, data, emp_id=None):
    name = data["name"].strip()
    active = 1 if data.get("active", True) else 0
    if emp_id is None:
        cur = conn.execute("INSERT INTO employees(name, active, shifts, jornalero) VALUES (?, ?, ?, ?)",
                           (name, active, _shifts_str(data), 1 if data.get("jornalero") else 0))
        emp_id = cur.lastrowid
    else:
        conn.execute("UPDATE employees SET name = ?, active = ? WHERE id = ?", (name, active, emp_id))
        if "shifts" in data:
            conn.execute("UPDATE employees SET shifts = ? WHERE id = ?", (_shifts_str(data), emp_id))
        if "jornalero" in data:
            conn.execute("UPDATE employees SET jornalero = ? WHERE id = ?", (1 if data["jornalero"] else 0, emp_id))
    if "jornal_dias" in data:
        conn.execute("UPDATE employees SET jornal_dias = ? WHERE id = ?", (max(0, min(31, int(data["jornal_dias"] or 0))), emp_id))
    if "positions" in data:
        conn.execute("DELETE FROM employee_positions WHERE employee_id = ?", (emp_id,))
        for pos, level in data["positions"].items():
            if pos in ASSIGNABLE and level in ("principal", "suplente", "ocasional"):
                conn.execute("INSERT INTO employee_positions(employee_id, position, level) VALUES (?, ?, ?)", (emp_id, pos, level))
    conn.commit()
    return emp_id


# ---------- asignaciones ----------

def get_assignments(conn, start, end):
    rows = conn.execute(
        "SELECT date, shift, position, employee_id, ord, locked, note FROM assignments WHERE date BETWEEN ? AND ? ORDER BY date, shift, position, ord",
        (iso(parse_date(start)), iso(parse_date(end))),
    )
    return [
        {"date": r["date"], "shift": r["shift"], "position": r["position"], "employee_id": r["employee_id"], "ord": r["ord"], "locked": bool(r["locked"]), "note": r["note"] or ""}
        for r in rows
    ]


def replace_assignments(conn, start, end, assignments):
    start, end = iso(parse_date(start)), iso(parse_date(end))
    conn.execute("DELETE FROM assignments WHERE date BETWEEN ? AND ?", (start, end))
    for i, a in enumerate(assignments):
        if not (start <= a["date"] <= end):
            continue
        if a["shift"] not in SHIFTS or a["position"] not in ASSIGNABLE:
            continue
        conn.execute(
            "INSERT INTO assignments(date, shift, position, employee_id, ord, locked, note) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a["date"], a["shift"], a["position"], int(a["employee_id"]), int(a.get("ord", i)), 1 if a.get("locked") else 0,
             (a.get("note") or "").strip()[:60] if a["position"] == TASK else ""),
        )
    conn.commit()


# ---------- meses ----------

def get_month_status(conn, year, month):
    r = conn.execute("SELECT status, updated_at FROM months WHERE year = ? AND month = ?", (year, month)).fetchone()
    return {"status": r["status"], "updated_at": r["updated_at"]} if r else {"status": None, "updated_at": None}


def set_month_status(conn, year, month, status=None):
    now = datetime.now().isoformat(timespec="seconds")
    cur = get_month_status(conn, year, month)
    status = status or cur["status"] or "borrador"
    conn.execute("INSERT OR REPLACE INTO months(year, month, status, updated_at) VALUES (?, ?, ?, ?)", (year, month, status, now))
    conn.commit()


def published_months(conn):
    return {(r["year"], r["month"]) for r in conn.execute("SELECT year, month FROM months WHERE status = 'publicado'")}


# ---------- cierres ----------

def get_closures(conn):
    return [{**dict(r), "yearly": bool(r["yearly"])} for r in conn.execute("SELECT * FROM closures ORDER BY substr(start, 6)")]


def get_special_days(conn):
    return [{**dict(r), "yearly": bool(r["yearly"])} for r in conn.execute("SELECT * FROM special_days ORDER BY substr(date, 6), shift")]


# ---------- licencias ----------

def get_licenses(conn, start=None, end=None):
    q = "SELECT l.*, e.name AS employee_name FROM licenses l JOIN employees e ON e.id = l.employee_id"
    args = []
    if start and end:
        q += " WHERE l.end >= ? AND l.start <= ?"
        args = [iso(parse_date(start)), iso(parse_date(end))]
    q += " ORDER BY l.start DESC"
    return [dict(r) for r in conn.execute(q, args)]


def license_days(licenses):
    """{employee_id: set(iso_date)}"""
    out = {}
    for l in licenses:
        for d in date_range(l["start"], l["end"]):
            out.setdefault(l["employee_id"], set()).add(iso(d))
    return out
