"""Carga inicial: empleados, puestos, admin por defecto y las 2 semanas de ejemplo (16-29/11/2026).

Uso:  python backend/seed.py            (solo si la base está vacía)
      python backend/seed.py --sin-ejemplo
"""
import sys
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

import db

P, S = "principal", "suplente"

EMPLOYEES = {
    "Jairo": {"cajero": P},
    "Guillermo": {"cajero": P},
    "Fabricio": {"cajero": P},
    "Maycol": {"cajero": S, "pistero": P, "otro": P},  # encargado: puede asignarse "otras tareas"
    "Christian": {"cajero": S, "pistero": P, "shop": S},
    "Nicolas": {"cajero": S, "pistero": P, "shop": S},
    "Luis": {"pistero": P},
    "Elias": {"pistero": P},
    "Lazaro": {"pistero": P},
    "Rafael": {"pistero": P, "shop": S},
    "Camilo": {"pistero": P},
    "Andres": {"pistero": P, "shop": S},
    "Belen": {"shop": P},
    "Soledad": {"shop": P},
    "Silvia": {"auxiliar": P},
    "Valeria": {"auxiliar": P},
    "Fabiana": {"auxiliar": P},
}

# Transcripción de las planillas en papel. Por día: (T3, T1, T2)
# T3 = (cajero, pistero); T1/T2 = (cajero, shop, [pisteros], auxiliar)
EXAMPLE = {
    16: (("Guillermo", "Luis"), ("Jairo", "Belen", ["Andres", "Rafael"], "Silvia"), ("Maycol", "Nicolas", ["Valeria", "Elias"], "Soledad")),
    17: (("Guillermo", "Luis"), ("Jairo", "Belen", ["Christian", "Rafael"], "Silvia"), ("Maycol", "Fabiana", ["Valeria", "Elias"], "Soledad")),
    18: (("Guillermo", "Luis"), ("Maycol", "Belen", ["Andres", "Christian"], "Silvia"), ("Fabricio", "Fabiana", ["Valeria", "Nicolas"], "Soledad")),
    19: (("Guillermo", "Luis"), ("Jairo", "Rafael", ["Maycol", "Andres"], "Silvia"), ("Fabricio", "Christian", ["Valeria", "Elias"], "Fabiana")),
    20: (("Jairo", "Rafael"), ("Fabricio", "Belen", ["Nicolas", "Valeria"], "Silvia"), ("Maycol", "Christian", ["Andres", "Elias"], "Soledad")),
    21: (("Jairo", "Rafael"), ("Fabricio", "Belen", ["Maycol", "Valeria"], "Silvia"), ("Guillermo", "Fabiana", ["Nicolas", "Elias"], "Silvia")),
    22: (("Jairo", "Rafael"), ("Maycol", "Belen", ["Valeria", "Luis"], "Soledad"), ("Guillermo", "Fabiana", ["Nicolas", "Christian"], "Soledad")),
    23: (("Jairo", "Rafael"), ("Fabricio", "Belen", ["Andres", "Elias"], "Silvia"), ("Christian", "Nicolas", ["Luis", "Camilo"], "Valeria")),
    24: (("Fabricio", "Andres"), ("Guillermo", "Belen", ["Christian", "Elias"], "Silvia"), ("Nicolas", "Soledad", ["Luis", "Camilo"], "Valeria")),
    25: (("Fabricio", "Andres"), ("Guillermo", "Rafael", ["Luis", "Elias"], "Silvia"), ("Maycol", "Soledad", ["Nicolas", "Camilo"], "Valeria")),
    26: (("Fabricio", "Andres"), ("Christian", "Belen", ["Luis", "Camilo"], "Silvia"), ("Jairo", "Soledad", ["Rafael", "Maycol"], "Fabiana")),
    27: (("Fabricio", "Andres"), ("Guillermo", "Nicolas", ["Luis", "Elias"], "Silvia"), ("Jairo", "Soledad", ["Christian", "Rafael"], "Fabiana")),
    28: (("Guillermo", "Nicolas"), ("Jairo", "Belen", ["Maycol", "Rafael"], "Silvia"), ("Christian", "Soledad", ["Luis", "Elias"], "Fabiana")),
    29: (("Guillermo", "Nicolas"), ("Maycol", "Belen", ["Christian", "Camilo"], "Fabiana"), ("Fabricio", "Soledad", ["Elias", "Andres"], "Fabiana")),
}


def example_assignments(ids):
    out = []
    for day, (t3, t1, t2) in EXAMPLE.items():
        d = date(2026, 11, day).isoformat()
        out.append({"date": d, "shift": "T3", "position": "cajero", "employee_id": ids[t3[0]], "ord": 0})
        out.append({"date": d, "shift": "T3", "position": "pistero", "employee_id": ids[t3[1]], "ord": 0})
        for shift, (caj, shop, pists, aux) in (("T1", t1), ("T2", t2)):
            out.append({"date": d, "shift": shift, "position": "cajero", "employee_id": ids[caj], "ord": 0})
            out.append({"date": d, "shift": shift, "position": "shop", "employee_id": ids[shop], "ord": 0})
            for i, p in enumerate(pists):
                out.append({"date": d, "shift": shift, "position": "pistero", "employee_id": ids[p], "ord": i})
            out.append({"date": d, "shift": shift, "position": "auxiliar", "employee_id": ids[aux], "ord": 0})
    return out


def seed(conn, with_example=True):
    db.init_schema(conn)
    if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]:
        return False
    ids = {}
    for name, pos in EMPLOYEES.items():
        ids[name] = db.save_employee(conn, {"name": name, "active": True, "positions": pos})
    conn.execute(
        "INSERT INTO users(username, password_hash, must_change, employee_id) VALUES (?, ?, 1, ?)",
        ("maycol", generate_password_hash("tifena"), ids["Maycol"]),
    )
    db.save_settings(conn, db.DEFAULT_SETTINGS)
    if with_example:
        db.replace_assignments(conn, "2026-11-16", "2026-11-29", example_assignments(ids))
        db.set_month_status(conn, 2026, 11, "publicado")
        for name, start, end, note in (
            ("Lazaro", "2026-11-16", "2026-11-30", "Vuelve a trabajar el 01/12"),
            ("Luis", "2026-12-01", "2026-12-16", ""),
        ):
            conn.execute(
                "INSERT INTO licenses(employee_id, start, end, kind, note, created_at) VALUES (?, ?, ?, 'Licencia', ?, datetime('now'))",
                (ids[name], start, end, note),
            )
    conn.commit()
    return True


if __name__ == "__main__":
    c = db.connect()
    print("Base inicializada" if seed(c, "--sin-ejemplo" not in sys.argv) else "La base ya tenía datos; no se cambió nada")
