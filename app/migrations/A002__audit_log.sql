-- Journal of administration actions (account approvals, role changes...).
-- Append only: rows are never updated nor deleted by the application.
-- actor_id is NULL for actions made outside the API (python -m app.accounts).
-- No health data here: details only hold statuses, roles and the source.
CREATE TABLE audit_log (
    id       BIGSERIAL   PRIMARY KEY,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id UUID,
    action   TEXT        NOT NULL,
    target   TEXT,
    details  JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_at_idx ON audit_log (at DESC);
CREATE INDEX audit_log_action_at_idx ON audit_log (action, at DESC);
