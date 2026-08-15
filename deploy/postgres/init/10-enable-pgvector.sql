-- The official Runtime compose image ships pgvector.  This is applied only
-- while PostgreSQL initializes a brand-new data directory.
CREATE EXTENSION IF NOT EXISTS vector;
