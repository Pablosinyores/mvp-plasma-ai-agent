"""Milestone 1 — AWS code-path tests that run WITHOUT Docker/LocalStack.

Uses `moto` to mock AWS in-process, so storage / keyvault / registry are exercised against real
boto3 calls (S3, KMS, Secrets Manager, DynamoDB) in CI without spinning up LocalStack. The
LocalStack-backed end-to-end (incl. the on-chain identity) lives in test_m1.py.
"""
import os

import pytest

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402


@pytest.fixture
def aws_stack(monkeypatch):
    # force boto3 to hit moto's in-process mock rather than a pinned LocalStack endpoint
    monkeypatch.setenv("AWS_ENDPOINT_URL", "")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        from plasma_mvp.aws import Aws
        from plasma_mvp.config import load_config

        cfg = load_config()
        aws = Aws(cfg)
        aws.s3.create_bucket(Bucket=cfg.s3_bucket)
        key = aws.kms.create_key(Description="agent-master")["KeyMetadata"]["KeyId"]
        aws.kms.create_alias(AliasName=cfg.kms_key_alias, TargetKeyId=key)
        aws.dynamodb.create_table(
            TableName=cfg.ddb_table,
            AttributeDefinitions=[{"AttributeName": "name", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "name", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield aws, cfg


def test_storage_content_addressed_roundtrip(aws_stack):
    from plasma_mvp.storage import Storage

    aws, cfg = aws_stack
    st = Storage(aws, cfg)
    data = b'{"hello":"world"}'
    uri = st.put(data)
    assert uri.startswith("s3://{}/".format(cfg.s3_bucket))
    assert st.get(uri) == data
    # same content => same key (content addressing)
    assert st.put(data) == uri


def test_keyvault_no_plaintext_at_rest(aws_stack):
    from plasma_mvp.keyvault import KeyVault

    aws, cfg = aws_stack
    kv = KeyVault(aws, cfg)
    addr = kv.new_agent_key("alpha")
    ciphertext = kv.ciphertext_for("alpha")
    account = kv.signer_for("alpha")
    # decrypt round-trips to the same address
    assert account.address == addr
    # the raw private key must NOT appear in the stored ciphertext
    assert account.key not in ciphertext
    assert len(ciphertext) > 32


def test_registry_dynamodb_mirror(aws_stack):
    from plasma_mvp.registry import Registry

    aws, cfg = aws_stack
    reg = Registry(aws, cfg)
    reg.put_agent("alpha", 7, "0xABC", "s3://agent-cards/deadbeef")
    row = reg.get_agent("alpha")
    assert row == {
        "name": "alpha",
        "agentId": 7,
        "address": "0xABC",
        "cardURI": "s3://agent-cards/deadbeef",
        "status": "registered",
    }
    assert reg.get_agent("missing") is None
