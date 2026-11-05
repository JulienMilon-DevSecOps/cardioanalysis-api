-- One application account per Supabase Auth user (auth.users.id).
-- The status is read on every request (app/dependencies.py): it is not put in
-- the JWT, so that a suspension takes effect immediately.
-- No foreign key to auth.users: the application role cannot reference the auth
-- schema; integrity is kept by the application (and by the signup trigger later).
CREATE TABLE accounts (
    user_id                 UUID        PRIMARY KEY,
    display_name            TEXT        NOT NULL
                            CHECK (char_length(btrim(display_name)) BETWEEN 1 AND 50),
    status                  TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
    role                    TEXT        NOT NULL DEFAULT 'user'
                            CHECK (role IN ('user', 'admin')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_changed_at       TIMESTAMPTZ,
    status_changed_by       UUID,
    onboarding_completed_at TIMESTAMPTZ,
    last_seen_at            TIMESTAMPTZ
);

CREATE INDEX accounts_status_idx ON accounts (status);
