"""Backup SQLite : creation, coherence, rotation, cas non-SQLite."""
import sqlite3
import tempfile
from pathlib import Path

from ma_signals import backup
from ma_signals.config import settings


def _tmp_sqlite(tmp: Path) -> Path:
    db = tmp / "src.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.commit(); con.close()
    return db


def test_backup_cree_un_fichier_coherent(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    db = _tmp_sqlite(tmp)
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db}")
    monkeypatch.setattr(settings, "backup_dir", str(tmp / "bk"))
    dest = backup.run_backup()
    assert dest is not None and dest.exists()
    con = sqlite3.connect(dest)
    assert con.execute("SELECT x FROM t").fetchone() == (42,)
    con.close()


def test_rotation_garde_les_n_plus_recentes(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(settings, "backup_keep", 3)
    for i in range(5):
        (tmp / f"ma_signals-2026010{i+1}-000000.db").write_bytes(b"x")
    removed = backup.rotate(tmp)
    rest = sorted(p.name for p in tmp.glob("ma_signals-*.db"))
    assert len(removed) == 2 and len(rest) == 3
    assert rest[0] == "ma_signals-20260103-000000.db"  # les plus vieilles parties


def test_non_sqlite_est_ignore(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://u:p@h/db")
    assert backup.run_backup() is None


def test_base_absente(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "sqlite:////nulle/part/x.db")
    assert backup.run_backup() is None
