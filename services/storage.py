import json
import os
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError


class S3Storage:
    """S3-compatible durable object storage for PDF source files and batch state."""

    def __init__(self, bucket: str, client):
        self.bucket = bucket
        self.client = client

    @classmethod
    def from_environment(cls, required: bool = True) -> Optional["S3Storage"]:
        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            if required:
                raise RuntimeError("S3_BUCKET is not configured.")
            return None
        kwargs = {}
        endpoint = os.getenv("S3_ENDPOINT_URL")
        region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION")
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        if region:
            kwargs["region_name"] = region
        access = os.getenv("AWS_ACCESS_KEY_ID")
        secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        if access and secret:
            kwargs["aws_access_key_id"] = access
            kwargs["aws_secret_access_key"] = secret
        return cls(bucket, boto3.client("s3", **kwargs))

    def put_json(self, key: str, value: dict):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body,
                               ContentType="application/json")

    def get_json(self, key: str):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except self.client.exceptions.NoSuchKey:
            return None
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise

    def put_file(self, key: str, path: str | Path):
        self.client.upload_file(str(path), self.bucket, key)

    def download_file(self, key: str, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(path))

    def list_keys(self, prefix: str):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]
