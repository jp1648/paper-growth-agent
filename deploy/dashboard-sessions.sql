-- Apply as database owner before deploying database-backed dashboard sessions.
CREATE TABLE IF NOT EXISTS public.dashboard_sessions (
  token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS dashboard_sessions_expiry ON public.dashboard_sessions(expires_at);
REVOKE ALL ON public.dashboard_sessions FROM PUBLIC;
ALTER TABLE public.dashboard_sessions ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS public.dashboard_login_limit (
  id boolean PRIMARY KEY CHECK (id),
  window_started timestamptz NOT NULL,
  attempts integer NOT NULL CHECK (attempts BETWEEN 1 AND 20)
);
REVOKE ALL ON public.dashboard_login_limit FROM PUBLIC;
ALTER TABLE public.dashboard_login_limit ENABLE ROW LEVEL SECURITY;
DO $$ DECLARE role_name text; BEGIN
  FOREACH role_name IN ARRAY ARRAY['trader_dashboard', 'dashboard_reader'] LOOP
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('GRANT SELECT, INSERT, DELETE ON public.dashboard_sessions TO %I', role_name);
      EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.dashboard_login_limit TO %I', role_name);
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.dashboard_login_limit', role_name || '_login_limit');
      EXECUTE format('CREATE POLICY %I ON public.dashboard_login_limit TO %I USING (true) WITH CHECK (true)',
        role_name || '_login_limit', role_name);
      EXECUTE format('DROP POLICY IF EXISTS %I ON public.dashboard_sessions', role_name || '_sessions');
      EXECUTE format('CREATE POLICY %I ON public.dashboard_sessions TO %I USING (true) WITH CHECK (true)',
        role_name || '_sessions', role_name);
    END IF;
  END LOOP;
END $$;
