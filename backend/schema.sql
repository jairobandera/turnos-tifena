CREATE TABLE IF NOT EXISTS employees (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    active  INTEGER NOT NULL DEFAULT 1
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
