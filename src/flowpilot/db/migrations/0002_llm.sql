-- 0002_llm: advisory LLM qualification + drafting with deterministic fallback.
-- LLM output never touches lead_scores, approvals, or outbox; those M1 guardrails are unchanged.

-- One row per qualification/draft attempt, including fallbacks that never called the model.
CREATE TABLE llm_runs (
    id              INTEGER PRIMARY KEY,
    lead_id         INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    task            TEXT NOT NULL CHECK (task IN ('qualification', 'draft')),
    mode            TEXT NOT NULL CHECK (mode IN ('off', 'ollama')),
    model           TEXT,
    prompt_version  TEXT NOT NULL,
    outcome         TEXT NOT NULL CHECK (outcome IN ('ok', 'fallback')),
    fallback_reason TEXT CHECK (fallback_reason IN
                        ('disabled', 'injection_suspected', 'unavailable', 'timeout',
                         'model_missing', 'busy', 'invalid_output', 'guard_rejected')),
    error           TEXT,
    latency_ms      INTEGER CHECK (latency_ms >= 0),
    input_sha256    TEXT NOT NULL,
    raw_output      TEXT,                       -- truncated model output, for debugging
    created_at      TEXT NOT NULL,
    CHECK ((outcome = 'ok') = (fallback_reason IS NULL))
);

CREATE INDEX idx_llm_runs_lead ON llm_runs (lead_id);

-- Advisory only: no column here feeds scoring, routing, or approval.
CREATE TABLE lead_assessments (
    id           INTEGER PRIMARY KEY,
    lead_id      INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    llm_run_id   INTEGER NOT NULL REFERENCES llm_runs (id),
    source       TEXT NOT NULL CHECK (source IN ('llm', 'rules')),
    summary      TEXT NOT NULL,
    service_line TEXT NOT NULL CHECK (service_line IN
                    ('process_automation', 'data_analytics', 'ai_strategy',
                     'systems_integration', 'digital_transformation', 'other')),
    urgency      TEXT NOT NULL CHECK (urgency IN ('low', 'medium', 'high')),
    flags_json   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_lead_assessments_lead ON lead_assessments (lead_id);

ALTER TABLE drafts ADD COLUMN prompt_version TEXT;
ALTER TABLE drafts ADD COLUMN llm_run_id INTEGER REFERENCES llm_runs (id);
ALTER TABLE drafts ADD COLUMN fallback_reason TEXT;

-- Guardrail: an 'llm' draft must be attributable to a successful run of a named model.
CREATE TRIGGER drafts_llm_provenance
BEFORE INSERT ON drafts
WHEN NEW.generator = 'llm'
 AND (NEW.model IS NULL OR NEW.llm_run_id IS NULL OR NEW.fallback_reason IS NOT NULL
      OR NOT EXISTS (SELECT 1 FROM llm_runs
                     WHERE id = NEW.llm_run_id AND outcome = 'ok' AND lead_id = NEW.lead_id))
BEGIN
    SELECT RAISE(ABORT, 'llm draft requires a successful llm_run for the same lead');
END;

-- Guardrail: drafts are immutable once written; edits become a new version.
CREATE TRIGGER drafts_immutable
BEFORE UPDATE ON drafts
BEGIN
    SELECT RAISE(ABORT, 'drafts are immutable; create a new version instead');
END;
