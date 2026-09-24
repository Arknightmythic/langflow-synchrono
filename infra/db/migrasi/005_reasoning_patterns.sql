-- 005_reasoning_patterns.sql
-- Tables for AI Reasoning: pattern cache and asynchronous job management

-- 1. Reasoning Patterns Cache
CREATE TABLE IF NOT EXISTS reasoning_patterns (
    pattern_hash      VARCHAR(64) PRIMARY KEY,
    pattern_name      VARCHAR(255) NOT NULL,
    pattern_signature TEXT NOT NULL,
    reason_template   TEXT NOT NULL,
    sample_id         TEXT,
    hit_count         INTEGER DEFAULT 1,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reasoning_patterns_signature
    ON reasoning_patterns (pattern_signature);

-- 2. Reasoning Jobs (Async tracking)
CREATE TABLE IF NOT EXISTS reasoning_jobs (
    job_id          VARCHAR(64) PRIMARY KEY,
    file_id         VARCHAR(64) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'QUEUED',
    stage           TEXT,
    error           TEXT,
    result          JSONB,
    heartbeat_at    TIMESTAMPTZ DEFAULT now(),
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_reasoning_jobs_file
    ON reasoning_jobs (file_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_reasoning_jobs_status
    ON reasoning_jobs (status);
