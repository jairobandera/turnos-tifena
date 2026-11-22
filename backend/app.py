"""Servidor Flask: API + archivos del frontend.

Ejecutar:  python backend/app.py   ->  http://localhost:5000
"""
import os
import secrets
from datetime import date, timedelta
from functools import wraps

from flask import Flask, jsonify, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from io import BytesIO

import db
import rules
import scheduler
import seed
from export_common import MONTHS, build_weeks
from export_excel import build_excel
from export_pdf import build_pdf

FRONTEND = os.path.join(db.BASE_DIR, "frontend")
HISTORY = 31   # el generador mira el mes anterior completo

app = Flask(__name__, static_folder=FRONTEND, static_url_path="")


def _secret_key():
    path = os.path.join(db.BASE_DIR, "data", "secret.key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  PERMANENT_SESSION_LIFETIME=timedelta(days=30))


def conn():
    return db.connect()


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            return jsonify({"error": "No autorizado"}), 401
        return fn(*a, **kw)
    return wrapper


def bad(msg, code=400):
    return jsonify({"error": msg}), code


def month_args(src):
    y, m = int(src.get("year")), int(src.get("month"))
    if not (1 <= m <= 12):
        raise ValueError("mes inválido")
    return y, m


def with_history(c, year, month, month_assignments=None):
    """Asignaciones del mes (las del editor si vienen) + 14 días previos guardados."""
    first, last = db.month_bounds(year, month)
    hist = db.get_assignments(c, first - timedelta(days=HISTORY), first - timedelta(days=1))
    if month_assignments is None:
        month_assignments = db.get_assignments(c, first, last)
    return hist + [a for a in month_assignments if db.iso(first) <= a["date"] <= db.iso(last)]


def closures(c, start, end):
    return rules.expand_closures(db.get_closures(c), start, end)


def extras_for(c, start, end):
    """Pisteros extra de los días especiales: {(fecha, turno): n}."""
    return rules.expand_special_days(db.get_special_days(c), start, end)


def month_extras(c, year, month):
    first, last = db.month_bounds(year, month)
    return extras_for(c, first, last)


def month_closures(c, year, month):
    first, last = db.month_bounds(year, month)
    return closures(c, first - timedelta(days=HISTORY + 1), last + timedelta(days=1))


def analysis(c, year, month, assignments):
    emps, settings, lics = db.get_employees(c), db.get_settings(c), db.get_licenses(c)
    extras = month_extras(c, year, month)
    res = rules.analyze(year, month, assignments, emps, lics, settings, month_closures(c, year, month), extras)
    # "otras tareas" sólo se sugieren al encargado logueado, para sí mismo
    res["suggestions"] = rules.suggest(year, month, assignments, emps, settings, date.today(),
                                       only_encargado=my_employee_id(c) or -1, licenses=lics,
                                       extras=extras, report=res["report"])
    return res


def my_employee_id(c):
    if not session.get("user_id"):
        return None
    r = c.execute("SELECT employee_id FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    return r["employee_id"] if r else None


# ---------------- páginas ----------------

@app.get("/")
def page_index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/admin")
def page_admin():
    return send_from_directory(FRONTEND, "admin.html")


# ---------------- público ----------------

@app.get("/api/employees")
def api_employees():
    c = conn()
    return jsonify([e for e in db.get_employees(c) if e["active"]])


@app.get("/api/settings")
def api_settings():
    return jsonify(db.get_settings(conn()))


@app.get("/api/licenses")
def api_licenses():
    c = conn()
    out = [{k: l[k] for k in ("id", "employee_id", "employee_name", "start", "end", "kind")} for l in db.get_licenses(c)]
    return jsonify(out)


@app.get("/api/schedule")
def api_schedule():
    """Sólo meses publicados."""
    c = conn()
    start, end = request.args.get("start"), request.args.get("end")
    if not start or not end:
        return bad("Faltan fechas")
    pub = db.published_months(c)
    rows = [a for a in db.get_assignments(c, start, end)
            if (int(a["date"][:4]), int(a["date"][5:7])) in pub]
    published_days = [db.iso(d) for d in db.date_range(start, end) if (d.year, d.month) in pub]
    return jsonify({"assignments": rows, "published_days": published_days,
                    "special": rules.special_hours(start, end, closures(c, start, end)),
                    "extra": rules.extras_json(extras_for(c, start, end))})


# ---------------- sesión ----------------

@app.post("/api/login")
def api_login():
    data = request.get_json(force=True)
    c = conn()
    u = c.execute("SELECT * FROM users WHERE username = ?", (data.get("username", "").strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], data.get("password", "")):
        return bad("Usuario o contraseña incorrectos", 401)
    session.permanent = True
    session["user_id"] = u["id"]
    session["username"] = u["username"]
    return jsonify({"username": u["username"], "must_change": bool(u["must_change"]), "employee_id": u["employee_id"]})


@app.post("/api/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def api_me():
    if not session.get("user_id"):
        return jsonify({"user": None})
    u = conn().execute("SELECT username, must_change, employee_id FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not u:
        session.clear()
        return jsonify({"user": None})
    return jsonify({"user": u["username"], "must_change": bool(u["must_change"]), "employee_id": u["employee_id"]})


# ---------------- admin: turnos ----------------

@app.get("/api/admin/month")
@admin_required
def admin_month():
    c = conn()
    year, month = month_args(request.args)
    first, last = db.month_bounds(year, month)
    month_rows = db.get_assignments(c, first, last)
    filled = sorted({a["date"] for a in month_rows})
    desde = db.iso(first)
    if filled and filled[-1] < db.iso(last):
        desde = db.iso(db.parse_date(filled[-1]) + timedelta(days=1))
    st = db.get_month_status(c, year, month)
    return jsonify({
        "assignments": month_rows,
        "history": db.get_assignments(c, first - timedelta(days=HISTORY), first - timedelta(days=1)),
        "next_days": db.get_assignments(c, last + timedelta(days=1), last + timedelta(days=7)),
        "status": st["status"], "updated_at": st["updated_at"],
        "suggested_desde": desde,
        "analysis": analysis(c, year, month, with_history(c, year, month, month_rows)),
    })


def check_tasks(c, first, last, incoming):
    """'Otras tareas' sólo las puede asignar el encargado a sí mismo.
    Las que ya estaban guardadas se conservan aunque guarde otro administrador."""
    me = c.execute("SELECT employee_id FROM users WHERE id = ?", (session["user_id"],)).fetchone()["employee_id"]
    saved = {(a["date"], a["shift"], a["employee_id"], a["note"]) for a in db.get_assignments(c, first, last) if a["position"] == db.TASK}
    fuera = db.get_settings(c).get("encargado_fuera")
    encargados = {e["id"] for e in db.get_employees(c) if db.TASK in e["positions"]}
    for a in incoming:
        if a.get("position") != db.TASK:
            continue
        key = (a["date"], a["shift"], int(a["employee_id"]), (a.get("note") or "").strip()[:60])
        if key in saved:
            continue
        # lo que arma el generador con el encargado fuera de planilla ("Otros servicios") se acepta
        if int(a["employee_id"]) in encargados and key[3] == scheduler.OTROS_SERVICIOS:
            continue
        if not me or int(a["employee_id"]) != me:
            raise PermissionError("Sólo el encargado puede asignarse \"otras tareas\" a sí mismo.")


@app.put("/api/admin/month")
@admin_required
def admin_save_month():
    c = conn()
    data = request.get_json(force=True)
    year, month = month_args(data)
    first, last = db.month_bounds(year, month)
    try:
        check_tasks(c, first, last, data.get("assignments", []))
    except PermissionError as e:
        return bad(str(e), 403)
    db.replace_assignments(c, first, last, data.get("assignments", []))
    db.set_month_status(c, year, month)
    st = db.get_month_status(c, year, month)
    return jsonify({"ok": True, **st, "analysis": analysis(c, year, month, with_history(c, year, month))})


@app.post("/api/admin/month/status")
@admin_required
def admin_month_status():
    c = conn()
    data = request.get_json(force=True)
    year, month = month_args(data)
    status = data.get("status")
    if status not in ("borrador", "publicado"):
        return bad("Estado inválido")
    db.set_month_status(c, year, month, status)
    return jsonify(db.get_month_status(c, year, month))


@app.post("/api/admin/analyze")
@admin_required
def admin_analyze():
    c = conn()
    data = request.get_json(force=True)
    year, month = month_args(data)
    return jsonify(analysis(c, year, month, with_history(c, year, month, data.get("assignments", []))))


@app.post("/api/admin/generate")
@admin_required
def admin_generate():
    c = conn()
    data = request.get_json(force=True)
    year, month = month_args(data)
    mode = data.get("mode", "completar")
    if mode not in ("completar", "rehacer"):
        return bad("Modo inválido")
    current = data.get("assignments")
    assigns = with_history(c, year, month, current)
    res = scheduler.generate(year, month, db.get_employees(c), assigns, db.get_licenses(c), db.get_settings(c),
                             desde=data.get("desde"), mode=mode, closures=month_closures(c, year, month),
                             extras=month_extras(c, year, month), jornales=data.get("jornales"))
    if not res["ok"]:
        return bad(res["msg"], 422)
    res["analysis"] = analysis(c, year, month, with_history(c, year, month, res["assignments"]))
    return jsonify(res)


@app.post("/api/admin/reflow")
@admin_required
def admin_reflow():
    """Cambia a alguien de puesto un día (ej. Soledad a limpieza), lo fija y reacomoda desde ese día a fin de mes."""
    c = conn()
    data = request.get_json(force=True)
    year, month = month_args(data)
    day, shift, eid = data.get("date"), data.get("shift"), int(data.get("employee_id", 0))
    to = data.get("to_position")
    current = [dict(a) for a in data.get("assignments", [])]
    target = next((a for a in current if a["date"] == day and a["shift"] == shift and int(a["employee_id"]) == eid), None)
    if not target:
        return bad("Esa persona ya no está en ese turno; actualizá la página.")
    if to == db.TASK:
        emp = next((e for e in db.get_employees(c) if e["id"] == eid), None)
        if eid != my_employee_id(c) or not emp or db.TASK not in emp["positions"]:
            return bad("Sólo el encargado puede asignarse \"otras tareas\" a sí mismo.", 403)
        target["position"] = db.TASK
        target["note"] = (data.get("note") or "Corte de pasto").strip()[:60]
    elif to:
        if to not in db.POSITIONS or not db.get_settings(c)["staffing"].get(shift, {}).get(to):
            return bad("Puesto inválido para ese turno")
        target["position"] = to
    target["locked"] = True
    res = scheduler.generate(year, month, db.get_employees(c), with_history(c, year, month, current),
                             db.get_licenses(c), db.get_settings(c), desde=day, mode="rehacer",
                             closures=month_closures(c, year, month), extras=month_extras(c, year, month))
    if not res["ok"]:
        return bad(res["msg"], 422)
    res["analysis"] = analysis(c, year, month, with_history(c, year, month, res["assignments"]))
    return jsonify(res)


# ---------------- cierres de la estación ----------------

@app.get("/api/closures")
def api_closures():
    return jsonify(db.get_closures(conn()))


def _closure_payload(data):
    start, end = (data.get("start") or "").strip(), (data.get("end") or "").strip()
    try:
        s_abs, e_abs = rules._abs_hour(start), rules._abs_hour(end)
    except Exception:
        raise ValueError("Fechas u horas inválidas")
    if e_abs <= s_abs:
        raise ValueError("La reapertura tiene que ser después del cierre")
    return start[:16], end[:16], (data.get("note") or "").strip()[:60], 1 if data.get("yearly") else 0


@app.post("/api/admin/closures")
@admin_required
def admin_closure_create():
    c = conn()
    cur = c.execute("INSERT INTO closures(start, end, note, yearly) VALUES (?, ?, ?, ?)", _closure_payload(request.get_json(force=True)))
    c.commit()
    return jsonify({"id": cur.lastrowid})


@app.put("/api/admin/closures/<int:cid>")
@admin_required
def admin_closure_update(cid):
    c = conn()
    c.execute("UPDATE closures SET start = ?, end = ?, note = ?, yearly = ? WHERE id = ?", (*_closure_payload(request.get_json(force=True)), cid))
    c.commit()
    return jsonify({"ok": True})


@app.delete("/api/admin/closures/<int:cid>")
@admin_required
def admin_closure_delete(cid):
    c = conn()
    c.execute("DELETE FROM closures WHERE id = ?", (cid,))
    c.commit()
    return jsonify({"ok": True})


# ---------------- días especiales (refuerzos) ----------------

@app.get("/api/admin/special-days")
@admin_required
def admin_special_days():
    return jsonify(db.get_special_days(conn()))


def _special_payload(data):
    d = (data.get("date") or "").strip()[:10]
    try:
        db.parse_date(d)
    except Exception:
        raise ValueError("Fecha inválida")
    shift = data.get("shift")
    if shift not in db.SHIFTS:
        raise ValueError("Elegí el turno")
    n = int(data.get("extra_pisteros") or 0)
    if not 1 <= n <= 3:
        raise ValueError("Entre 1 y 3 pisteros extra")
    return d, shift, n, (data.get("note") or "").strip()[:60], 1 if data.get("yearly") else 0


@app.post("/api/admin/special-days")
@admin_required
def admin_special_create():
    c = conn()
    cur = c.execute("INSERT INTO special_days(date, shift, extra_pisteros, note, yearly) VALUES (?, ?, ?, ?, ?)",
                    _special_payload(request.get_json(force=True)))
    c.commit()
    return jsonify({"id": cur.lastrowid})


@app.put("/api/admin/special-days/<int:sid>")
@admin_required
def admin_special_update(sid):
    c = conn()
    c.execute("UPDATE special_days SET date = ?, shift = ?, extra_pisteros = ?, note = ?, yearly = ? WHERE id = ?",
              (*_special_payload(request.get_json(force=True)), sid))
    c.commit()
    return jsonify({"ok": True})


@app.delete("/api/admin/special-days/<int:sid>")
@admin_required
def admin_special_delete(sid):
    c = conn()
    c.execute("DELETE FROM special_days WHERE id = ?", (sid,))
    c.commit()
    return jsonify({"ok": True})


# ---------------- admin: licencias ----------------

@app.get("/api/admin/licenses")
@admin_required
def admin_licenses():
    return jsonify(db.get_licenses(conn()))


def _license_payload(data):
    start, end = data.get("start"), data.get("end")
    if not data.get("employee_id") or not start or not end:
        raise ValueError("Completá empleado y fechas")
    if end < start:
        raise ValueError("La fecha de regreso es anterior a la de salida")
    return (int(data["employee_id"]), start, end, (data.get("kind") or "Licencia").strip(), (data.get("note") or "").strip())


@app.post("/api/admin/licenses")
@admin_required
def admin_license_create():
    c = conn()
    try:
        vals = _license_payload(request.get_json(force=True))
    except ValueError as e:
        return bad(str(e))
    cur = c.execute("INSERT INTO licenses(employee_id, start, end, kind, note, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))", vals)
    c.commit()
    return jsonify({"id": cur.lastrowid})


@app.put("/api/admin/licenses/<int:lid>")
@admin_required
def admin_license_update(lid):
    c = conn()
    try:
        vals = _license_payload(request.get_json(force=True))
    except ValueError as e:
        return bad(str(e))
    c.execute("UPDATE licenses SET employee_id = ?, start = ?, end = ?, kind = ?, note = ? WHERE id = ?", (*vals, lid))
    c.commit()
    return jsonify({"ok": True})


@app.delete("/api/admin/licenses/<int:lid>")
@admin_required
def admin_license_delete(lid):
    c = conn()
    c.execute("DELETE FROM licenses WHERE id = ?", (lid,))
    c.commit()
    return jsonify({"ok": True})


# ---------------- admin: empleados ----------------

@app.get("/api/admin/employees")
@admin_required
def admin_employees():
    return jsonify(db.get_employees(conn()))


@app.post("/api/admin/employees")
@admin_required
def admin_employee_create():
    data = request.get_json(force=True)
    if not (data.get("name") or "").strip():
        return bad("Falta el nombre")
    try:
        return jsonify({"id": db.save_employee(conn(), data)})
    except ValueError as e:
        return bad(str(e))
    except Exception:
        return bad("Ya existe un empleado con ese nombre")


@app.put("/api/admin/employees/<int:eid>")
@admin_required
def admin_employee_update(eid):
    data = request.get_json(force=True)
    if not (data.get("name") or "").strip():
        return bad("Falta el nombre")
    try:
        db.save_employee(conn(), data, eid)
    except ValueError as e:
        return bad(str(e))
    except Exception:
        return bad("Ya existe un empleado con ese nombre")
    return jsonify({"ok": True})


# ---------------- admin: configuración y usuarios ----------------

@app.put("/api/admin/settings")
@admin_required
def admin_settings():
    c = conn()
    data = request.get_json(force=True)
    clean = {}
    for k in ("max_horas4", "libres_objetivo", "bloque", "max_dias_seguidos", "tiempo_generador"):
        if k in data:
            clean[k] = max(0, int(data[k]))
    if "cajeros_estrictos" in data:
        clean["cajeros_estrictos"] = bool(data["cajeros_estrictos"])
    if "encargado_fuera" in data:
        clean["encargado_fuera"] = bool(data["encargado_fuera"])
    if "staffing" in data:
        st = {}
        for s in db.SHIFTS:
            st[s] = {p: max(0, int(n)) for p, n in data["staffing"].get(s, {}).items() if p in db.POSITIONS and int(n) > 0}
        clean["staffing"] = st
    clean["bloque"] = max(1, clean.get("bloque", db.get_settings(c)["bloque"]))
    db.save_settings(c, clean)
    return jsonify(db.get_settings(c))


@app.get("/api/admin/users")
@admin_required
def admin_users():
    q = "SELECT u.id, u.username, u.employee_id, e.name AS employee_name FROM users u LEFT JOIN employees e ON e.id = u.employee_id ORDER BY u.username"
    return jsonify([dict(r) for r in conn().execute(q)])


@app.post("/api/admin/users")
@admin_required
def admin_user_create():
    data = request.get_json(force=True)
    user, pwd = (data.get("username") or "").strip().lower(), data.get("password") or ""
    if not user or len(pwd) < 4:
        return bad("Usuario y contraseña (mínimo 4 caracteres)")
    c = conn()
    try:
        emp = int(data["employee_id"]) if data.get("employee_id") else None
        c.execute("INSERT INTO users(username, password_hash, must_change, employee_id) VALUES (?, ?, 1, ?)", (user, generate_password_hash(pwd), emp))
        c.commit()
    except Exception:
        return bad("Ese usuario ya existe")
    return jsonify({"ok": True})


@app.delete("/api/admin/users/<int:uid>")
@admin_required
def admin_user_delete(uid):
    c = conn()
    if uid == session["user_id"]:
        return bad("No podés borrar tu propio usuario")
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] <= 1:
        return bad("Tiene que quedar al menos un administrador")
    c.execute("DELETE FROM users WHERE id = ?", (uid,))
    c.commit()
    return jsonify({"ok": True})


@app.put("/api/admin/password")
@admin_required
def admin_password():
    data = request.get_json(force=True)
    c = conn()
    u = c.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not check_password_hash(u["password_hash"], data.get("current", "")):
        return bad("La contraseña actual no es correcta")
    if len(data.get("new", "")) < 6:
        return bad("La nueva contraseña debe tener al menos 6 caracteres")
    c.execute("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?", (generate_password_hash(data["new"]), u["id"]))
    c.commit()
    return jsonify({"ok": True})


# ---------------- exportación ----------------

@app.get("/api/admin/export/<fmt>")
@admin_required
def admin_export(fmt):
    c = conn()
    start, end = request.args.get("start"), request.args.get("end")
    if not start or not end or fmt not in ("excel", "pdf"):
        return bad("Parámetros inválidos")
    s, e = db.parse_date(start), db.parse_date(end)
    wk_start = s - timedelta(days=s.weekday())
    wk_end = e + timedelta(days=6 - e.weekday())
    assigns = db.get_assignments(c, wk_start, wk_end)
    weeks = build_weeks(s, e, assigns, db.get_employees(c), db.get_licenses(c), db.get_settings(c)["staffing"],
                        closures(c, wk_start - timedelta(days=1), wk_end + timedelta(days=1)),
                        extras_for(c, wk_start, wk_end))
    if s.day == 1 and e == db.month_bounds(s.year, s.month)[1]:
        name = f"Turnos {MONTHS[s.month - 1].capitalize()} {s.year}"
    else:
        name = f"Turnos {s.day:02d}-{s.month:02d} al {e.day:02d}-{e.month:02d}"
    if fmt == "excel":
        data, mime, ext = build_excel(weeks), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    else:
        data, mime, ext = build_pdf(weeks, name), "application/pdf", "pdf"
    return send_file(BytesIO(data), mimetype=mime, as_attachment=True, download_name=f"{name}.{ext}")


@app.errorhandler(ValueError)
def on_value_error(e):
    return bad(str(e))


def create_app():
    seed.seed(db.connect())
    return app


if __name__ == "__main__":
    create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
