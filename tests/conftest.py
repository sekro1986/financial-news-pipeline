"""Isolation des tests : une base SQLite temporaire, remise a zero avant chaque test.

Les modules de test partagent le meme engine (singleton lie a DATABASE_URL au 1er
import). On fixe donc la base ici, avant tout import de ma_signals.db, et on
recree les tables vierges avant chaque test pour eviter toute pollution croisee
(ex: un signal de prix resilient deduplique un test voisin via story_key)."""
import os
import tempfile

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/ma_signals_test.db"

import pytest  # noqa: E402

from ma_signals.db import engine  # noqa: E402
from ma_signals.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
