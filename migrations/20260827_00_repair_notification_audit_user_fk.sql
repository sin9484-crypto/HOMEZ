-- Repair legacy notification audit rows written with the sentinel user_id=0.
-- The current delivery code records system-originated events with NULL. Limit
-- this data correction to the exact legacy action and only when user 0 does
-- not exist, so unrelated audit history is never rewritten.
BEGIN;

UPDATE audit_logs
SET user_id = NULL
WHERE user_id = 0
  AND action = 'NOTIFICATION_EMAIL_SKIPPED'
  AND NOT EXISTS (SELECT 1 FROM users WHERE id = 0);

COMMIT;

-- Rollback is intentionally not provided: NULL is the truthful representation
-- for a system event and restoring a dangling foreign key would re-corrupt the
-- database.
