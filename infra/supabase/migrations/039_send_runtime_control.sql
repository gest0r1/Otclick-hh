-- ============================================================
-- 039_send_runtime_control.sql
-- Persistent per-user sender controls + single-account DB lease.
-- This does NOT activate the sender in worker_main.
-- ============================================================

CREATE TABLE IF NOT EXISTS application_send_control (
  user_id uuid PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
  desired_state text NOT NULL DEFAULT 'paused' CHECK (
    desired_state IN ('paused', 'running', 'stop_after_current')
  ),
  safety_interval_seconds integer NOT NULL DEFAULT 10 CHECK (
    safety_interval_seconds BETWEEN 0 AND 300
  ),
  lease_owner text,
  lease_expires_at timestamptz,
  last_cycle_at timestamptz,
  last_outcome text,
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE application_send_control ENABLE ROW LEVEL SECURITY;
-- No anon/authenticated policy: backend service-role only.

CREATE OR REPLACE FUNCTION acquire_application_send_lease(
  p_user_id uuid,
  p_owner text,
  p_ttl_seconds integer DEFAULT 120
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  did_acquire boolean := false;
BEGIN
  IF p_owner IS NULL OR btrim(p_owner) = '' THEN
    RAISE EXCEPTION 'lease owner is required';
  END IF;
  IF p_ttl_seconds < 10 OR p_ttl_seconds > 900 THEN
    RAISE EXCEPTION 'lease ttl must be between 10 and 900 seconds';
  END IF;

  INSERT INTO application_send_control (user_id)
  VALUES (p_user_id)
  ON CONFLICT (user_id) DO NOTHING;

  UPDATE application_send_control
  SET
    lease_owner = p_owner,
    lease_expires_at = now() + make_interval(secs => p_ttl_seconds),
    updated_at = now()
  WHERE user_id = p_user_id
    AND desired_state = 'running'
    AND (
      lease_expires_at IS NULL
      OR lease_expires_at <= now()
      OR lease_owner = p_owner
    )
    AND (
      last_cycle_at IS NULL
      OR last_cycle_at + make_interval(secs => safety_interval_seconds) <= now()
    );

  GET DIAGNOSTICS did_acquire = ROW_COUNT;
  RETURN did_acquire;
END;
$$;

CREATE OR REPLACE FUNCTION release_application_send_lease(
  p_user_id uuid,
  p_owner text,
  p_outcome text DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  did_release boolean := false;
BEGIN
  UPDATE application_send_control
  SET
    lease_owner = NULL,
    lease_expires_at = NULL,
    last_cycle_at = now(),
    last_outcome = left(p_outcome, 200),
    desired_state = CASE
      WHEN desired_state = 'stop_after_current' THEN 'paused'
      ELSE desired_state
    END,
    updated_at = now()
  WHERE user_id = p_user_id
    AND lease_owner = p_owner;

  GET DIAGNOSTICS did_release = ROW_COUNT;
  RETURN did_release;
END;
$$;
