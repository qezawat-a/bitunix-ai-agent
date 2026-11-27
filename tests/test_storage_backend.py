from bitunix_agent.storage import backend
from bitunix_agent.storage.backend import SqliteConn, connect, translate


def test_translate_sqlite_to_postgres():
    assert translate("SELECT * FROM t WHERE a=? AND b=?") == ["SELECT * FROM t WHERE a=%s AND b=%s"]
    assert "BIGSERIAL PRIMARY KEY" in translate("CREATE TABLE m (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL)")[0]
    assert "DOUBLE PRECISION" in translate("CREATE TABLE m (ts REAL)")[0]
    assert translate("UPDATE trailing SET best_price=? WHERE position_id=?")[0].startswith('UPDATE "trailing"')
    d, ins = translate("INSERT OR REPLACE INTO notes VALUES (?,?,?)")
    assert d == 'DELETE FROM "notes" WHERE key=%s' and ins == "INSERT INTO notes VALUES (%s,%s,%s)"
    d, ins = translate("INSERT OR REPLACE INTO trailing VALUES (?,?)")
    assert d == 'DELETE FROM "trailing" WHERE position_id=%s' and ins.startswith('INSERT INTO "trailing"')


def test_unreachable_database_falls_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db?sslmode=disable")
    monkeypatch.setattr(backend, "_PG", {})
    c = connect(str(tmp_path / "a.db"))
    assert isinstance(c, SqliteConn)
    assert "fallback" in backend.STATUS["backend"] and backend.STATUS["error"]
    assert connect(":memory:").execute("SELECT 1 AS one")[0]["one"] == 1


def test_memory_db_ignores_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@example.invalid/db")
    assert isinstance(connect(":memory:"), SqliteConn)
