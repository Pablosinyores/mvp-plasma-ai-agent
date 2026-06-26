"""Content-addressed object storage.

Keys are the keccak-256 hash of the content, so the same bytes always map to the same URI and the
on-chain `resultHash` / card hash can be verified against the stored object.

Two layers live here:
  * `Storage` — the original S3-backed helper (kept for the earning-loop path).
  * `StorageProvider` — a pluggable interface with Local / S3 / IPFS backends selected by the
    `STORAGE_BACKEND` env var via `get_storage()`. On-chain anchors only the content hash; the full
    payload lives behind one of these backends.
"""
import json
import os
from abc import ABC, abstractmethod

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


# --- pluggable provider interface -------------------------------------------------------------

#: per-object size ceiling (defends the remote backends against accidental large writes)
MAX_OBJECT_BYTES = 5 * 1024 * 1024


def content_key(data: bytes) -> str:
    """keccak-256 hex of the content — the stable, dedup-friendly address for a blob."""
    return keccak(data).hex()


class StorageProvider(ABC):
    """Content-addressed blob store. `upload` returns a backend-scheme URI; `download` resolves it
    back to bytes; `exists` reports presence. Implementations are interchangeable behind
    `get_storage()`."""

    @abstractmethod
    def upload(self, data: bytes) -> str: ...

    @abstractmethod
    def download(self, uri: str) -> bytes: ...

    @abstractmethod
    def exists(self, uri: str) -> bool: ...

    # put/get aliases so a provider is a drop-in for the legacy `Storage` (.put/.get) call sites.
    def put(self, data) -> str:
        return self.upload(data)

    def get(self, uri: str) -> bytes:
        return self.download(uri)

    @staticmethod
    def _coerce(data) -> bytes:
        if isinstance(data, str):
            return data.encode("utf-8")
        if isinstance(data, (dict, list)):
            return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return bytes(data)

    @staticmethod
    def _guard_size(data: bytes) -> None:
        if len(data) > MAX_OBJECT_BYTES:
            raise ValueError(
                "object too large: {} bytes > {} cap".format(len(data), MAX_OBJECT_BYTES)
            )


class LocalStorageProvider(StorageProvider):
    """Filesystem backend. Files are named by their keccak hash under `root`; URIs are `local://<hash>`."""

    SCHEME = "local://"

    def __init__(self, root: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.root, key)

    def _key(self, uri: str) -> str:
        return uri[len(self.SCHEME):] if uri.startswith(self.SCHEME) else uri

    def upload(self, data) -> str:
        data = self._coerce(data)
        self._guard_size(data)
        key = content_key(data)
        path = self._path(key)
        if not os.path.exists(path):  # content-addressed: identical bytes never rewrite
            with open(path, "wb") as f:
                f.write(data)
        return self.SCHEME + key

    def download(self, uri: str) -> bytes:
        path = self._path(self._key(uri))
        if not os.path.exists(path):
            raise KeyError("no object at {}".format(uri))
        with open(path, "rb") as f:
            return f.read()

    def exists(self, uri: str) -> bool:
        return os.path.exists(self._path(self._key(uri)))


class S3StorageProvider(StorageProvider):
    """S3 backend (LocalStack locally, real AWS in cloud). URIs are `s3://<bucket>/<key>`."""

    def __init__(self, aws: Aws = None, cfg: Config = None, bucket: str = None):
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.bucket = bucket or self.cfg.s3_bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        existing = {b["Name"] for b in self.aws.s3.list_buckets().get("Buckets", [])}
        if self.bucket not in existing:
            self.aws.s3.create_bucket(Bucket=self.bucket)

    def _key(self, uri: str) -> str:
        if uri.startswith("s3://"):
            return uri.split("/", 3)[3]
        return uri

    def upload(self, data) -> str:
        data = self._coerce(data)
        self._guard_size(data)
        key = content_key(data)
        self.aws.s3.put_object(Bucket=self.bucket, Key=key, Body=data)
        return "s3://{}/{}".format(self.bucket, key)

    def download(self, uri: str) -> bytes:
        obj = self.aws.s3.get_object(Bucket=self.bucket, Key=self._key(uri))
        return obj["Body"].read()

    def exists(self, uri: str) -> bool:
        try:
            self.aws.s3.head_object(Bucket=self.bucket, Key=self._key(uri))
            return True
        except Exception:
            return False


class IPFSStorageProvider(StorageProvider):
    """IPFS backend via a Kubo (go-ipfs) HTTP API. URIs are `ipfs://<cid>`."""

    SCHEME = "ipfs://"

    def __init__(self, api_url: str = None, cfg: Config = None):
        self.cfg = cfg or load_config()
        self.api_url = (api_url or self.cfg.ipfs_api_url).rstrip("/")

    def _cid(self, uri: str) -> str:
        return uri[len(self.SCHEME):] if uri.startswith(self.SCHEME) else uri

    def upload(self, data) -> str:
        import requests

        data = self._coerce(data)
        self._guard_size(data)
        resp = requests.post(
            "{}/api/v0/add".format(self.api_url),
            files={"file": ("blob", data)},
            params={"cid-version": "1", "pin": "true"},
            timeout=30,
        )
        resp.raise_for_status()
        return self.SCHEME + resp.json()["Hash"]

    def download(self, uri: str) -> bytes:
        import requests

        resp = requests.post(
            "{}/api/v0/cat".format(self.api_url),
            params={"arg": self._cid(uri)},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content

    def exists(self, uri: str) -> bool:
        import requests

        try:
            resp = requests.post(
                "{}/api/v0/block/stat".format(self.api_url),
                params={"arg": self._cid(uri)},
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False


def get_storage(backend: str = None, cfg: Config = None) -> StorageProvider:
    """Factory: resolve a `StorageProvider` from `backend` (or the `STORAGE_BACKEND` env)."""
    cfg = cfg or load_config()
    backend = (backend or cfg.storage_backend or "s3").lower()
    if backend == "local":
        return LocalStorageProvider(cfg.storage_local_path)
    if backend == "s3":
        return S3StorageProvider(cfg=cfg)
    if backend == "ipfs":
        return IPFSStorageProvider(cfg=cfg)
    raise ValueError("unknown STORAGE_BACKEND: {}".format(backend))
