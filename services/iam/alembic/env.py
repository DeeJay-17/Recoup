from recoup_common.alembic_support import run_migrations
from recoup_iam.models import SCHEMA, Base

run_migrations(Base.metadata, SCHEMA)
