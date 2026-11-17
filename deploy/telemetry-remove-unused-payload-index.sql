-- Run outside a transaction; event payloads and run/trace/name indexes are preserved.
DROP INDEX CONCURRENTLY IF EXISTS public.telemetry_payload;
