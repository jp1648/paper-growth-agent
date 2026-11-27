"""Pinned cloud executor ownership; transfer requires verified shutdown, never lease expiry."""

import httpx

from .checkpoints import storage_headers


class ExecutionFenceError(RuntimeError):
    pass


def verify_executor_owner(bucket):
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            identity = {}
            for field, path in (
                ("instance_id", "instance/id"),
                ("project_number", "project/numeric-project-id"),
                ("zone", "instance/zone"),
            ):
                response = client.get(
                    "http://169.254.169.254/computeMetadata/v1/" + path, headers={"Metadata-Flavor": "Google"}
                )
                response.raise_for_status()
                if response.headers.get("Metadata-Flavor") != "Google":
                    raise ValueError("Unverified executor identity response")
                identity[field] = response.text.strip()
            response = client.get(
                f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/executor-owner.json",
                params={"alt": "media"},
                headers=storage_headers(client),
            )
            response.raise_for_status()
            owner = response.json()
            if (
                type(owner.get("version")) is not int
                or owner["version"] != 1
                or not identity["instance_id"].isdecimal()
                or not identity["project_number"].isdecimal()
                or not all(owner.get(key) == value and value for key, value in identity.items())
            ):
                raise ValueError("This VM is not the designated executor")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise ExecutionFenceError("Cloud executor ownership is unavailable or does not match") from error
