-- Apply after creating telemetry_events from trader.observability.SCHEMA.
-- Dedicated roles are NOLOGIN until private credentials are provisioned.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trader_writer') THEN
    CREATE ROLE trader_writer NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'trader_dashboard') THEN
    CREATE ROLE trader_dashboard NOLOGIN;
  END IF;
END $$;

ALTER TABLE public.telemetry_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.telemetry_events FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
  FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = role_name) THEN
      EXECUTE format('REVOKE ALL ON public.telemetry_events FROM %I', role_name);
    END IF;
  END LOOP;
END $$;
GRANT USAGE ON SCHEMA public TO trader_writer, trader_dashboard;
GRANT SELECT, INSERT ON public.telemetry_events TO trader_writer;
GRANT SELECT ON public.telemetry_events TO trader_dashboard;

DROP POLICY IF EXISTS trader_writer_read ON public.telemetry_events;
CREATE POLICY trader_writer_read ON public.telemetry_events FOR SELECT TO trader_writer USING (true);
DROP POLICY IF EXISTS trader_writer_insert ON public.telemetry_events;
CREATE POLICY trader_writer_insert ON public.telemetry_events FOR INSERT TO trader_writer WITH CHECK (true);
DROP POLICY IF EXISTS trader_dashboard_read ON public.telemetry_events;
CREATE POLICY trader_dashboard_read ON public.telemetry_events FOR SELECT TO trader_dashboard USING (true);
