from recoup_common.alembic_support import run_migrations
from recoup_tool_gateway.models import SCHEMA, Base

run_migrations(Base.metadata, SCHEMA)
