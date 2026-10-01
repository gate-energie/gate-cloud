"""Open the Dagster instance on real Postgres from inside the built image.

Unit tests run on local sqlite storage, so they cannot see a broken Postgres
driver; 0.1.0 shipped one (SQLAlchemy 2.1 picked psycopg3, which is not
installed). This opens every storage the cluster uses and reads from each.
"""
from dagster import DagsterInstance
from dagster_postgres import PostgresEventLogStorage, PostgresRunStorage, PostgresScheduleStorage

instance = DagsterInstance.get()
assert isinstance(instance.run_storage, PostgresRunStorage), type(instance.run_storage)
assert isinstance(instance.event_log_storage, PostgresEventLogStorage), type(instance.event_log_storage)
assert isinstance(instance.schedule_storage, PostgresScheduleStorage), type(instance.schedule_storage)
instance.get_runs(limit=1)
instance.all_instigator_state()
print("dagster instance opened on postgres")
