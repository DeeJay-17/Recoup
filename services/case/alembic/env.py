from recoup_case.models import SCHEMA, Base
from recoup_common.alembic_support import run_migrations

run_migrations(Base.metadata, SCHEMA)
