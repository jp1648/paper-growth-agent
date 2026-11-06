"""Create-only GCS checkpoints, verified by reading the exact saved generation."""

import hashlib
import re
from urllib.parse import quote

import httpx


def replicate_checkpoint(path, bucket):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Invalid recovery bucket")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    size = path.stat().st_size
    name = "checkpoints/" + path.name
    metadata_url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}"
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        token = client.get(
            "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        token.raise_for_status()
        if token.headers.get("Metadata-Flavor") != "Google":
            raise ValueError("Unverified metadata token response")
        access_token = token.json()["access_token"]
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("Missing recovery credential")
        headers = {"Authorization": "Bearer " + access_token}
        try:
            with path.open("rb") as source:
                uploaded = client.post(
                    f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
                    params={"uploadType": "media", "name": name, "ifGenerationMatch": "0"},
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size),
                    },
                    content=iter(lambda: source.read(1024 * 1024), b""),
                )
                if uploaded.status_code != 412:
                    uploaded.raise_for_status()
        except httpx.TransportError:
            # Resolve ambiguous writes only by verified read-back, never a second POST.
            pass
        metadata = client.get(metadata_url, headers=headers)
        metadata.raise_for_status()
        saved = metadata.json()
        generation = saved.get("generation")
        if (
            saved.get("bucket") != bucket
            or saved.get("name") != name
            or str(saved.get("size")) != str(size)
            or not isinstance(generation, str)
            or not generation.isdecimal()
        ):
            raise ValueError("Recovery object identity/size/generation mismatch")
        checksum, received = hashlib.sha256(), 0
        with client.stream(
            "GET", metadata_url, params={"alt": "media", "generation": generation}, headers=headers
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes(1024 * 1024):
                received += len(chunk)
                if received > size:
                    raise ValueError("Recovery object exceeds expected size")
                checksum.update(chunk)
        if received != size or checksum.hexdigest() != digest:
            raise ValueError("Recovery object checksum mismatch")
    return {"bucket": bucket, "object": name, "generation": generation, "sha256": digest, "bytes": size}
