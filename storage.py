"""
storage.py — thin wrapper around S3.

No access keys anywhere in the code: boto3 picks up credentials from the
EC2 / Beanstalk instance profile (IAM role). Locally it uses whatever
`aws configure` set up.

The bucket stays PRIVATE. The browser only ever sees short-lived presigned
URLs, and every object key starts with users/<user_id>/ so one user's files
are namespaced away from another's.
"""

from __future__ import annotations

import boto3
from botocore.config import Config as BotoConfig

_client = None


def _s3(app):
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            region_name=app.config["AWS_REGION"],
            config=BotoConfig(signature_version="s3v4"),
        )
    return _client


def enabled(app) -> bool:
    return bool(app.config["S3_BUCKET"])


def make_key(user_id: int, image_uid: str, kind: str, ext: str) -> str:
    # kind is "original" or "result"
    return f"users/{user_id}/{image_uid}/{kind}.{ext}"


def upload_bytes(app, key: str, data: bytes, content_type: str) -> None:
    _s3(app).put_object(
        Bucket=app.config["S3_BUCKET"],
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def presigned_url(app, key: str, download_name: str | None = None) -> str:
    params = {"Bucket": app.config["S3_BUCKET"], "Key": key}
    if download_name:
        safe = download_name.replace('"', "").replace("\r", "").replace("\n", "")
        params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
    return _s3(app).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=app.config["PRESIGN_SECONDS"]
    )


def delete_keys(app, keys: list[str]) -> None:
    if not keys:
        return
    _s3(app).delete_objects(
        Bucket=app.config["S3_BUCKET"],
        Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True},
    )


def thumb_key(key: str) -> str:
    """users/1/<uid>/result.jpg -> users/1/<uid>/result_thumb.jpg (small gallery image)."""
    return key.rsplit(".", 1)[0] + "_thumb.jpg"


def all_keys_for(original_key: str, result_key: str) -> list[str]:
    return [original_key, result_key, thumb_key(original_key), thumb_key(result_key)]


def ping(app) -> None:
    """Tiny write+delete under users/_status/ to prove the role can really use the bucket."""
    key = "users/_status/ping.txt"
    upload_bytes(app, key, b"ok", "text/plain")
    delete_keys(app, [key])
