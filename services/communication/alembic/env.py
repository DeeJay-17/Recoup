from recoup_common.alembic_support import run_migrations
from recoup_communication.models import SCHEMA, Base

run_migrations(Base.metadata, SCHEMA)
