-- 0001_initial: core lead pipeline, approval/outbox guardrails, audit log, idempotency.
-- Timestamps are ISO-8601 UTC strings supplied by the application (injectable clock).

CREATE TABLE leads (
    id               INTEGER PRIMARY KEY,
    company_name     TEXT NOT NULL,
    company_key      TEXT NOT NULL,            -- normalized company name, used for dedupe
    contact_name     TEXT NOT NULL,
    contact_email    TEXT NOT NULL,            -- normalized (lowercase), synthetic domains only
    company_size     TEXT NOT NULL CHECK (company_size IN
                        ('1-10', '11-50', '51-200', '201-1000', '1000+')),
    industry         TEXT NOT NULL CHECK (industry IN
                        ('saas', 'fintech', 'healthcare', 'retail_ecommerce', 'manufacturing',
                         'logistics', 'professional_services', 'nonprofit', 'other')),
    service_interest TEXT NOT NULL CHECK (service_interest IN
                        ('process_automation', 'data_analytics', 'ai_strategy',
                         'systems_integration', 'digital_transformation', 'other')),
    budget_band      TEXT NOT NULL CHECK (budget_band IN
                        ('under_10k', '10k_25k', '25k_50k', '50k_100k', 'over_100k',
                         'undisclosed')),
    timeline         TEXT NOT NULL CHECK (timeline IN
                        ('immediate', '1_3_months', '3_6_months', 'exploring')),
    source           TEXT NOT NULL CHECK (source IN
                        ('web_form', 'referral', 'event', 'partner')),
    message          TEXT NOT NULL,
    -- Full planned lifecycle; M1 only produces 'new' -> 'scored'.
    status           TEXT NOT NULL DEFAULT 'new' CHECK (status IN
                        ('new', 'scored', 'drafted', 'awaiting_approval', 'approved',
                         'rejected', 'sent', 'closed')),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (contact_email, company_key)
);

CREATE INDEX idx_leads_status ON leads (status);

CREATE TABLE lead_scores (
    id             INTEGER PRIMARY KEY,
    lead_id        INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    rules_version  TEXT NOT NULL,
    total          INTEGER NOT NULL CHECK (total BETWEEN 0 AND 100),
    tier           TEXT NOT NULL CHECK (tier IN ('hot', 'warm', 'cold')),
    route          TEXT NOT NULL CHECK (route IN ('partner_review', 'sales_queue', 'nurture')),
    breakdown_json TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    UNIQUE (lead_id, rules_version)
);

CREATE INDEX idx_lead_scores_tier ON lead_scores (tier);

-- Drafts are versioned; an approval is bound to one exact draft version.
CREATE TABLE drafts (
    id         INTEGER PRIMARY KEY,
    lead_id    INTEGER NOT NULL REFERENCES leads (id) ON DELETE CASCADE,
    version    INTEGER NOT NULL CHECK (version >= 1),
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    generator  TEXT NOT NULL CHECK (generator IN ('template', 'llm', 'human_edit')),
    model      TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (lead_id, version)
);

-- Guardrail: approvals can only be recorded by a human actor. There is no auto-approval path.
CREATE TABLE approvals (
    id         INTEGER PRIMARY KEY,
    draft_id   INTEGER NOT NULL UNIQUE REFERENCES drafts (id),
    decision   TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    actor_type TEXT NOT NULL CHECK (actor_type = 'human'),
    decided_by TEXT NOT NULL CHECK (length(trim(decided_by)) > 0),
    reason     TEXT,
    decided_at TEXT NOT NULL
);

-- Guardrail: only simulated sending exists, and every outbox row needs a matching approval.
CREATE TABLE outbox (
    id              INTEGER PRIMARY KEY,
    approval_id     INTEGER NOT NULL UNIQUE REFERENCES approvals (id),
    draft_id        INTEGER NOT NULL REFERENCES drafts (id),
    channel         TEXT NOT NULL DEFAULT 'simulated' CHECK (channel = 'simulated'),
    recipient       TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at      TEXT NOT NULL,
    sent_at         TEXT
);

CREATE TRIGGER outbox_requires_approved_draft
BEFORE INSERT ON outbox
WHEN NOT EXISTS (
    SELECT 1 FROM approvals
    WHERE approvals.id = NEW.approval_id
      AND approvals.draft_id = NEW.draft_id
      AND approvals.decision = 'approved'
)
BEGIN
    SELECT RAISE(ABORT, 'outbox insert requires a human approval of this exact draft');
END;

CREATE TRIGGER outbox_approval_immutable
BEFORE UPDATE OF approval_id, draft_id, recipient, subject, body ON outbox
BEGIN
    SELECT RAISE(ABORT, 'outbox message content is immutable after approval');
END;

CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY,
    occurred_at TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    request_id  TEXT
);

CREATE INDEX idx_audit_entity ON audit_log (entity_type, entity_id);

CREATE TRIGGER audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TABLE idempotency_keys (
    key           TEXT NOT NULL,
    scope         TEXT NOT NULL,               -- e.g. 'POST /leads'
    request_hash  TEXT NOT NULL,               -- sha256 of canonical request body
    status_code   INTEGER NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (scope, key)
);
