-- Runs once on first container start. Migrations create per-service schemas themselves;
-- this only enables cluster-wide extensions.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- Temporal's auto-setup creates its own databases (temporal, temporal_visibility).
