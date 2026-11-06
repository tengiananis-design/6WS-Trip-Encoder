"""Local-development and S3-compatible shared object storage.

Configure S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY and optionally
S3_ENDPOINT_URL/S3_REGION for a shared bucket used by both web and worker.
"""
import os
from pathlib import Path
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parents[1] / "data")) )
BUCKET = os.getenv("S3_BUCKET")
_PREFIX = os.getenv("S3_PREFIX", "6ws").strip("/")
_client = None


def shared_storage_enabled() -> bool:
    return bool(BUCKET and os.getenv("S3_ACCESS_KEY_ID") and os.getenv("S3_SECRET_ACCESS_KEY"))


def _s3():
    global _client
    if not shared_storage_enabled():
        raise RuntimeError("Shared object storage is not configured. Set S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY.")
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            region_name=os.getenv("S3_REGION", "auto"),
            aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        )
    return _client


def _key(key: str) -> str:
    clean = str(key).replace("\\", "/").lstrip("/")
    if any(part in ("..", ".") for part in clean.split("/")):
        raise ValueError("Invalid storage key.")
    return f"{_PREFIX}/{clean}" if _PREFIX else clean


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream"):
    if shared_storage_enabled():
        _s3().put_object(Bucket=BUCKET, Key=_key(key), Body=data, ContentType=content_type)
        return
    path = DATA_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.replace(path)


def get_bytes(key: str) -> bytes:
    if shared_storage_enabled():
        response = _s3().get_object(Bucket=BUCKET, Key=_key(key))
        return response["Body"].read()
    return (DATA_DIR / key).read_bytes()


def exists(key: str) -> bool:
    if shared_storage_enabled():
        try:
            _s3().head_object(Bucket=BUCKET, Key=_key(key))
            return True
        except ClientError as error:
            if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return False
            raise
    return (DATA_DIR / key).is_file()


def delete_prefix(prefix: str):
    if shared_storage_enabled():
        paginator = _s3().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=_key(prefix).rstrip("/") + "/"):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                _s3().delete_objects(Bucket=BUCKET, Delete={"Objects": objects})
        return
    path = DATA_DIR / prefix
    if path.exists():
        import shutil
        shutil.rmtree(path)


def list_keys(prefix: str):
    if shared_storage_enabled():
        paginator = _s3().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=BUCKET, Prefix=_key(prefix).rstrip("/") + "/"):
            for item in page.get("Contents", []):
                yield item["Key"][len(_PREFIX) + 1:] if _PREFIX else item["Key"]
        return
    root = DATA_DIR / prefix
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                yield path.relative_to(DATA_DIR).as_posix()


def save_uploaded_file(batch_id: str, filename: str, source_path: Path):
    key = f"uploads/{batch_id}/{filename}"
    if shared_storage_enabled():
        with source_path.open("rb") as stream:
            _s3().upload_fileobj(stream, BUCKET, _key(key), ExtraArgs={"ContentType": "application/pdf"})
    else:
        target = DATA_DIR / key
        target.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target)
    return key


def download_batch(batch_id: str, target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"uploads/{batch_id}/"
    count = 0
    for key in list_keys(prefix):
        name = key[len(prefix):]
        if not name.lower().endswith(".pdf"):
            continue
        target = target_dir / Path(name).name
        target.write_bytes(get_bytes(key))
        count += 1
    if count == 0:
        raise FileNotFoundError(f"No uploaded PDFs found for batch {batch_id}.")
    return count


def save_json(key: str, value: dict):
    import json
    put_bytes(key, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"), "application/json")


def load_json(key: str):
    import json
    return json.loads(get_bytes(key).decode("utf-8"))


def upload_file(key: str, source_path: Path, content_type: str = "application/octet-stream"):
    if shared_storage_enabled():
        _s3().upload_file(str(source_path), BUCKET, _key(key), ExtraArgs={"ContentType": content_type})
        return
    target = DATA_DIR / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != target.resolve():
        import shutil
        shutil.copy2(source_path, target)
