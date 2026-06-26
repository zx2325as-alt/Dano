CREATE TABLE IF NOT EXISTS option_references (
    ref_hash TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    value_json JSONB NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    context_hash TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_option_references_expiry
    ON option_references (expires_at);

CREATE INDEX IF NOT EXISTS idx_option_references_scope
    ON option_references (tenant, skill_id, field_name, source_fingerprint);
