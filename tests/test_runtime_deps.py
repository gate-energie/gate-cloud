"""The deployed instance stores runs in Postgres through dagster-postgres.

Its storage only works if a plain postgresql:// URL resolves to psycopg2, the
driver dagster-postgres ships. Unit tests use local storage, so they would not
notice a dependency bump that changes that default (SQLAlchemy 2.1 did).
"""
from sqlalchemy.engine import make_url


def test_postgres_urls_resolve_to_psycopg2():
    import psycopg2  # noqa: F401

    assert make_url("postgresql://u@h/db").get_dialect().driver == "psycopg2"
