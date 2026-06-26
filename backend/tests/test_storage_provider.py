"""StorageProvider backend tests.

LocalStorageProvider is pure-filesystem and always runs. S3 and IPFS backends are exercised only
when their infra (LocalStack / Kubo) is reachable, otherwise they skip.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))
from plasma_mvp import storage as st  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402


# --- LocalStorageProvider (no infra) ---

def test_local_roundtrip(tmp_path):
    p = st.LocalStorageProvider(str(tmp_path))
    uri = p.upload(b"hello world")
    assert uri.startswith("local://")
    assert p.exists(uri)
    assert p.download(uri) == b"hello world"


def test_local_content_address_dedup(tmp_path):
    p = st.LocalStorageProvider(str(tmp_path))
    a = p.upload(b"same bytes")
    b = p.upload("same bytes")  # str coerced to identical bytes
    assert a == b  # identical content -> identical address
    assert len(list(tmp_path.iterdir())) == 1


def test_local_binary_roundtrip(tmp_path):
    p = st.LocalStorageProvider(str(tmp_path))
    blob = bytes(range(256)) * 8
    uri = p.upload(blob)
    assert p.download(uri) == blob


def test_local_missing_raises(tmp_path):
    p = st.LocalStorageProvider(str(tmp_path))
    with pytest.raises(KeyError):
        p.download("local://deadbeef")
    assert p.exists("local://deadbeef") is False


def test_local_dict_serialized(tmp_path):
    p = st.LocalStorageProvider(str(tmp_path))
    uri = p.upload({"b": 2, "a": 1})
    # canonical (sorted-key) json so dedup is stable regardless of insertion order
    assert p.download(uri) == b'{"a":1,"b":2}'


def test_size_guard(tmp_path):
    p = st.LocalStorageProvider(str(tmp_path))
    with pytest.raises(ValueError):
        p.upload(b"x" * (st.MAX_OBJECT_BYTES + 1))


def test_get_storage_factory_local(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
    prov = st.get_storage(cfg=load_config())
    assert isinstance(prov, st.LocalStorageProvider)


def test_get_storage_unknown_raises():
    with pytest.raises(ValueError):
        st.get_storage(backend="bogus")


# --- S3StorageProvider (needs LocalStack) ---

@pytest.fixture()
def s3_provider():
    try:
        prov = st.S3StorageProvider(bucket="agent-cards-test")
        prov.aws.s3.list_buckets()
    except Exception:
        pytest.skip("LocalStack S3 not reachable")
    return prov


def test_s3_roundtrip(s3_provider):
    uri = s3_provider.upload(b"payload-s3")
    assert uri.startswith("s3://")
    assert s3_provider.exists(uri)
    assert s3_provider.download(uri) == b"payload-s3"


def test_s3_missing(s3_provider):
    assert s3_provider.exists("s3://agent-cards-test/nothere") is False


# --- IPFSStorageProvider (needs Kubo) ---

@pytest.fixture()
def ipfs_provider():
    import requests

    cfg = load_config()
    prov = st.IPFSStorageProvider(cfg=cfg)
    try:
        requests.post("{}/api/v0/version".format(prov.api_url), timeout=3)
    except Exception:
        pytest.skip("IPFS (Kubo) not reachable")
    return prov


def test_ipfs_roundtrip(ipfs_provider):
    uri = ipfs_provider.upload(b"payload-ipfs")
    assert uri.startswith("ipfs://")
    assert ipfs_provider.download(uri) == b"payload-ipfs"
    assert ipfs_provider.exists(uri)
