CREATE TABLE IF NOT EXISTS employees (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    active    INTEGER NOT NULL DEFAULT 1,
    shifts    TEXT NOT NULL DEFAULT 'T3,T1,T2',   -- turnos que puede hacer (ej. Belén sólo 'T1')
    jornalero INTEGER NOT NULL DEFAULT 0,         -- jornalero: sin tope de libres, cubre a los demás
    jornal_dias INTEGER NOT NULL DEFAULT 0        -- días por mes que trabaja el jornalero (0 = sólo lo necesario)
);

-- level: 'principal' | 'suplente'
CREATE TABLE IF NOT EXISTS employee_positions (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    position    TEXT NOT NULL,
    level       TEXT NOT NULL DEFAULT 'principal',
    PRIMARY KEY (employee_id, position)
);

-- shift: T1 (mañana) | T2 (tarde) | T3 (noche, empieza 22:00 del día anterior)
CREATE TABLE IF NOT EXISTS assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    shift       TEXT NOT NULL,
    position    TEXT NOT NULL,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    ord         INTEGER NOT NULL DEFAULT 0,
    locked      INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT ''   -- descripción para 'otro' (ej. Corte de pasto)
);
CREATE INDEX IF NOT EXISTS idx_assignments_date ON assignments(date);

-- status: 'borrador' | 'publicado'
CREATE TABLE IF NOT EXISTS months (
    year       INTEGER NOT NULL,
    month      INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'borrador',
    updated_at TEXT,
    PRIMARY KEY (year, month)
);

CREATE TABLE IF NOT EXISTS licenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    start       TEXT NOT NULL,
    end         TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'Licencia',
    note        TEXT DEFAULT '',
    created_at  TEXT
);

-- cierres de la estación (ej. 24/12 20:00 -> 25/12 20:00). yearly = se repite todos los años
CREATE TABLE IF NOT EXISTS closures (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    start  TEXT NOT NULL,     -- 'YYYY-MM-DDTHH:MM'
    end    TEXT NOT NULL,
    note   TEXT NOT NULL DEFAULT '',
    yearly INTEGER NOT NULL DEFAULT 0
);

-- días especiales (feriados, días movidos): pisteros extra en un turno. yearly = todos los años
CREATE TABLE IF NOT EXISTS special_days (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,     -- 'YYYY-MM-DD'
    shift          TEXT NOT NULL,     -- T1 | T2 | T3
    extra_pisteros INTEGER NOT NULL DEFAULT 1,
    note           TEXT NOT NULL DEFAULT '',
    yearly         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    must_change   INTEGER NOT NULL DEFAULT 0,
    employee_id   INTEGER REFERENCES employees(id)   -- empleado que es este admin (para "otras tareas")
);
