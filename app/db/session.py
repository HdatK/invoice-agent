from __future__ import annotations
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from ..core.config import settings
from .models import Base, Category

_kw = {"connect_args": {"check_same_thread": False}} if settings.db_url.startswith("sqlite") else {}
engine = create_engine(settings.db_url, **_kw)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(conn, _):
    if settings.db_url.startswith("sqlite"):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

SYSTEM_CATEGORIES = [("مواد اولیه", "expense"), ("حمل‌ونقل", "expense"), ("تجهیزات", "expense"), ("خدمات", "expense"),
                     ("اجاره", "expense"), ("فروش کالا", "income"), ("فروش خدمات", "income"), ("سایر", "both")]


def init_db():
    Base.metadata.create_all(engine)
    with session_scope() as s:
        have = {c.name_fa for c in s.query(Category)}
        for name, kind in SYSTEM_CATEGORIES:
            if name not in have:
                s.add(Category(name_fa=name, kind=kind))


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
