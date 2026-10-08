# ADR 0003 - Approvals are bound to the exact arguments, not to a signed token

Status: accepted (Phase 2)

Decision: an approval is a database row keyed by `(run_id, tool, args_hash)`. The executor runs a
sensitive tool only after atomically moving a matching `approved`, unexpired row to `used`
(compare-and-set). A different argument (e.g. another recipient) hashes differently and needs its
own approval; a used or rejected approval cannot be replayed.

Why no HMAC token (the first design had one): in a single-process deployment the row and the
executor trust the same database, so a signature adds no protection against anything the database
itself does not already cover. Evidence of tampering comes from the hash-chained `audit_log`
(append-only trigger on PostgreSQL). If approvals ever cross a trust boundary (a separate approval
service, a mobile app signing decisions), signed tokens should be reintroduced at that boundary.

Related rules (enforced in code, covered by tests in `tests/security/`):
- Policy cannot auto-allow EXTERNAL or DESTRUCTIVE tools; overrides can only tighten.
- Destructive actions need `confirm_tool` (the user repeats the tool name).
- Once untrusted content enters a run, local writes also require approval (taint).
- Tool names sent to models use `domain__action` (providers reject dots).
