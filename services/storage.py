import json
import os
from pathlib import Path

import boto3


class S3Storage:
    def __init__(self, bucket, client):
        self.bucket = bucket
        self.client = client

    @classmethod
    def from_environment(cls, required=False):
        bucket = os.getenv("S3_BUCKET")
        if not bucket:
            if required:
                raise RuntimeError("S3_BUCKET is required for durable storage.")
            return None
        kwargs = {"region_name": os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"}
        if os.getenv("S3_ENDPOINT_URL"):
            kwargs["endpoint_url"] = os.getenv("S3_ENDPOINT_URL")
        if os.getenv("AWS_ACCESS_KEY_ID"):
            kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID")
        if os.getenv("AWS_SECRET_ACCESS_KEY"):
            kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY")
        return cls(bucket, boto3.client("s3", **kwargs))

    def put_json(self, key, value):
        self.client.put_object(Bucket=self.bucket, Key=key,
            Body=json.dumps(value, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json")

    def get_json(self, key):
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=key)
            return json.loads(obj["Body"].read().decode("utf-8"))
        except self.client.exceptions.NoSuchKey:
            return None

    def put_file(self, key, path):
        self.client.upload_file(str(path), self.bucket, key)

    def get_file(self, key, destination):
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))
        return destination

    def list_keys(self, prefix):
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]
