-- 006_reasoning_pattern_locks.sql
-- Expand reasoning_patterns with distributed pattern-claim locking state machine.
-- Enables idempotent resolution across concurrent batches and anti-thundering-herd protection.

ALTER TABLE reasoning_patterns
    ALTER COLUMN reason_template DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'COMPLETED',
    ADD COLUMN IF NOT EXISTS locked_by VARCHAR(64),
    ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_reasoning_patterns_status
    ON reasoning_patterns (status);
