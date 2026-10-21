-- 0003_reviewers: individually authenticated human reviewers (M4.1).
-- Additive only. Existing approvals are not rewritten: they keep reviewer_id NULL. The trigger
-- below applies to new approvals, which must name an active reviewer.

-- One row per human reviewer. Only the SHA-256 of the reviewer's random token is stored.
CREATE TABLE reviewers (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 64),
    token_sha256 TEXT NOT NULL UNIQUE CHECK (length(token_sha256) = 64),
    created_at   TEXT NOT NULL,
    revoked_at   TEXT
);

ALTER TABLE approvals ADD COLUMN reviewer_id INTEGER REFERENCES reviewers (id);

-- Guardrail: a new approval must be made by an active reviewer, recorded under that reviewer's
-- name. A self-declared or revoked identity cannot be written, whatever the application does.
CREATE TRIGGER approvals_require_active_reviewer
BEFORE INSERT ON approvals
WHEN NEW.reviewer_id IS NULL OR NOT EXISTS (
    SELECT 1 FROM reviewers
    WHERE reviewers.id = NEW.reviewer_id
      AND reviewers.revoked_at IS NULL
      AND reviewers.name = NEW.decided_by
)
BEGIN
    SELECT RAISE(ABORT, 'approval requires an active reviewer whose name matches decided_by');
END;

-- Reviewers are history: never deleted, identity and token hash fixed, revocation permanent.
CREATE TRIGGER reviewers_no_delete
BEFORE DELETE ON reviewers
BEGIN
    SELECT RAISE(ABORT, 'reviewers cannot be deleted; revoke instead');
END;

CREATE TRIGGER reviewers_identity_immutable
BEFORE UPDATE OF id, name, token_sha256, created_at ON reviewers
BEGIN
    SELECT RAISE(ABORT, 'reviewer identity is immutable');
END;

CREATE TRIGGER reviewers_revocation_permanent
BEFORE UPDATE OF revoked_at ON reviewers
WHEN OLD.revoked_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'a revoked reviewer cannot be reinstated');
END;
