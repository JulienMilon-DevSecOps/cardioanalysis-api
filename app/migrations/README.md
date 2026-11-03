# API database migrations

SQL files versioning the API's own tables, applied in order by
`python -m app.migrate` (see `app/services/migrations.py`). cardiolab's tables are
not here: cardiolab ships and applies its own `V<nnn>` migrations.

## Rules

* **Name**: `A<nnn>__<description>.sql`, lowercase description with underscores
  (e.g. `A001__accounts.sql`). Numbers are unique and never reused.
* **Privileged** migration (objects in the `auth` schema, e.g. a trigger on
  `auth.users`): `A<nnn>__<description>.privileged.sql`. It is applied only by
  `python -m app.migrate --privileged`, with an administrator role, never by the
  application role. Both kinds share the same numbering: a run stops at the first
  pending migration of the other kind, so the order is always kept.
* **Never edit** a migration once applied anywhere: its checksum is recorded and
  the next run stops. Write a new migration instead.
* Objects without a schema are created in the application schema (`DB_SCHEMA`,
  default `cardioanalysis`); always qualify objects of other schemas (`auth.users`).
* One migration = one transaction: no `BEGIN` / `COMMIT` in the file, and no
  statement that cannot run in a transaction (`CREATE INDEX CONCURRENTLY`,
  `VACUUM`).

Applied migrations are recorded in `<DB_SCHEMA>.api_schema_migrations` (version,
name, kind, checksum, date), separate from cardiolab's `schema_migrations`.
