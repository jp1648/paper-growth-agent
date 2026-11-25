"""Native callback held until process exit; no server or provider access."""

import threading

from temporalio.bridge.runtime import LoggingConfig, Runtime, RuntimeOptions, TelemetryConfig


def pending_native_callback(release, completed):
    # Keep callback globals separate from the child script's runtime references.
    # Exercises the lifecycle fixed upstream in temporalio/sdk-python#2120.
    entered = threading.Event()

    def callback(_entries):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Parent did not reach interpreter cleanup")
        completed.append(True)

    runtime = Runtime(
        options=RuntimeOptions(
            telemetry=TelemetryConfig(
                logging=LoggingConfig(filter="INFO", forward_to=callback, format=None),
                metrics=None,
            )
        )
    )
    runtime.write_test_info_log("offline shutdown probe", "")
    if not entered.wait(10):
        raise RuntimeError("Native callback did not start")
    return runtime
