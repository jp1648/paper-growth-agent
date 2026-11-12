# Testing budget policy

Standing user authorization, November 9, 2026: testing may exceed ordinary API
budgets without another per-run permission request. Do not postpone development
acceptance work solely because the normal daily allowance is depleted.

Use the broker-free evaluation command:

```sh
paper-agent --config config.toml evaluate-run --testing --allow-paid \
  --suite PATH --output-dir NEW_DIRECTORY --run-id UNIQUE_ID --max-cost 20
```

`--testing` uses the explicit testing allowance instead of the normal daily and
per-run ceilings. Choose an allowance appropriate to the investigation and verified
available provider credit; it can be increased for subsequent diagnosed tests.
The existing configuration validation permits a maximum of $100 for one bounded
run. The switch does not buy credits, change billing, authorize live endpoints,
clear pause, reset history, enable unbounded loops, or waive research acceptance.
Time, model/tool-count, citation and data-quality checks remain in force.

Every testing reservation records `testing_budget_override` and the ordinary daily
limit. All actual spend remains included in the shared ledger, including when the
normal limit has been exceeded. An interrupted or unknown charge must be settled
before another testing run. Stable run IDs and existing artifact protections still
prevent duplicate paid evaluations. Active measured studies reject this override:
changing a frozen trial's cost policy needs an explicit recorded amendment, not a
hidden change to its evidence. Tests do not count as prospective trial observations.

This is a project-local record of the user's preference, not a claim of a saved
cross-session agent memory. No automatic funding purchase is authorized.
