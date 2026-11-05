"""Create local telemetry credentials once; never print or replace existing secrets."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
secret = root / "state" / "observability" / "postgres-password"
secret.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
if not secret.exists():
    fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secrets.token_hex(32))
secret.chmod(0o600)
env = root / ".env"
existing = env.read_text() if env.exists() else ""
lines = existing.splitlines()
if not any(
    line.startswith("OBSERVABILITY_DATABASE_URL=") and line.partition("=")[2].strip() for line in lines
):
    lines = [line for line in lines if not line.startswith("OBSERVABILITY_DATABASE_URL=")]
    lines.append(
        "OBSERVABILITY_DATABASE_URL=postgresql://trader:"
        + secret.read_text().strip()
        + "@postgres:5432/observability"
    )
    env.touch(mode=0o600, exist_ok=True)
    env.chmod(0o600)
    env.write_text("\n".join(lines) + "\n")
env.chmod(0o600)
print("Observability credentials ready; values not displayed.")
