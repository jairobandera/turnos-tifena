import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))


@pytest.fixture
def conn(tmp_path):
    import db
    import seed
    c = db.connect(str(tmp_path / "t.db"))
    seed.seed(c)
    return c


def emp_id(conn, name):
    return conn.execute("SELECT id FROM employees WHERE name = ?", (name,)).fetchone()[0]
