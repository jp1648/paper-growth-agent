# Research worker shutdown investigation

The November 25 worker stop returned exit 139 shortly after Docker accepted the
stop. Later controlled stops exited normally. The application already waits for
Temporal's `Worker` context to exit before returning and restores its signal
handlers afterward. No saved native stack establishes the exact cause of that
historical crash. A successful later stop does not resolve an intermittent fault.

The locked Temporal SDK was 1.36.0. Its native callbacks can still be running when
an awaited Python operation finishes; interpreter finalization can then race the
native runtime. The official [1.37.0 release](https://github.com/temporalio/sdk-python/releases/tag/1.37.0)
contains [upstream fix #2120](https://github.com/temporalio/sdk-python/pull/2120):
it shuts down live native runtimes before Python finalization, joining their
threads while releasing the GIL. This is a closely matching defect class, not
proof of the historical worker's native stack.

Trader now requires Temporal >=1.37 and locks 1.37.0. No custom SDK patch,
`os._exit`, accepted failure exit code, longer shutdown deadline or weaker
reconciliation/fencing rule is used. The release's removed experimental Nexus
hook is unused by Trader. Existing workflow replay/restart tests exercise the
updated SDK; frozen in-flight manifests still require their original code and
configuration and must not be silently migrated.

## Offline verification

`tests/test_temporal_shutdown.py` exercises real native runtimes in disposable
child processes. A native logging callback waits until an `atexit` release;
an observer checks that one or three callbacks finished before normal process
exit. The callbacks live in a separate helper module so their globals cannot
retain the child script's runtime objects. It requires no server, credentials,
model call or broker access, and fails on a nonzero exit or a bounded timeout.

On CPython 3.12.16/macOS ARM64, both cases timed out after 20 seconds with the
original 1.36.0 dependency. Both passed with 1.37.0. The candidate's 28 focused
tests also passed, including real local Temporal workflow replay, restart and
stage integration, plus the existing signal/shutdown tests. The before/after
result demonstrates the finalization defect and its correction; it does not
turn an unrecorded historical SIGSEGV into a proven stack-level diagnosis.

An isolated Linux ARM64 container comparison, with networking disabled during
the tests and only test files mounted read-only, also failed both 1.36.0 cases:
one observed zero completed callbacks and the three-runtime child aborted
(SIGABRT). Both 1.37.0 cases exited normally with all callbacks completed.
This Linux result is an additional native regression demonstration, not an
exact reproduction of the production x86-64 incident.

Production needs a reviewed image containing the new lockfile and a controlled,
idle stop/restart check before this is described as deployed. Keep all runtime
shutdown failures visible and retain the ordinary maintenance/accounting gates.
