"""Content-addressed object storage backed by LocalStack S3.

Keys are the keccak-256 hash of the content, so the same bytes always map to the same URI and the
on-chain `resultHash` / card hash can be verified against the stored object.
"""
from eth_utils import keccak

from .aws import Aws
from .config import Config, load_config


class Storage:
    def __init__(self, aws: Aws = None, cfg: Config = None):
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.bucket = self.cfg.s3_bucket

    def ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self.aws.s3.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self.aws.s3.create_bucket(Bucket=self.bucket)

    def put(self, data: bytes) -> str:
        """Store bytes; return an s3:// URI keyed by keccak(content)."""
        if isinstance(data, str):
            data = data.encode("utf-8")
        key = keccak(data).hex()
        self.aws.s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return "s3://{}/{}".format(self.bucket, key)

    def get(self, uri: str) -> bytes:
        """Fetch bytes for an s3://bucket/key URI (or a bare key)."""
        key = self._key(uri)
        obj = self.aws.s3.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def hash_of(self, uri: str) -> str:
        return self._key(uri)

    def _key(self, uri: str) -> str:
        if uri.startswith("s3://"):
            return uri.split("/", 3)[3]
        return uri
