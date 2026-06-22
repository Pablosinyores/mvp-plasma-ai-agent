"""Milestone 1 end-to-end acceptance test.

Proves, against the real local stack (Anvil + LocalStack), that:
  - an agent gets an on-chain identity NFT owned by its address,
  - the on-chain cardURI resolves to the Agent Card stored in S3,
  - the private key is NOT stored in plaintext (only KMS-decryptable),
  - the DynamoDB mirror row exists.

Requires `make up` first (infra healthy + contracts deployed). Skips cleanly otherwise.
"""
import json
import uuid

import pytest

from plasma_mvp.adapter import LocalAdapter
from plasma_mvp.aws import Aws
from plasma_mvp.config import load_config
from plasma_mvp.keyvault import KeyVault
from plasma_mvp.registry import Registry
from plasma_mvp.storage import Storage


@pytest.fixture(scope="module")
def stack():
    cfg = load_config()
    if not cfg.deployments_path.exists():
        pytest.skip("contracts not deployed — run `make up` first")
    try:
        aws = Aws(cfg)
        aws.ping()
        adapter = LocalAdapter(cfg)
    except Exception as e:  # noqa: BLE001
        pytest.skip("local stack not reachable ({}) — run `make up` first".format(e))
    return {
        "cfg": cfg,
        "aws": aws,
        "adapter": adapter,
        "kv": KeyVault(aws, cfg),
        "storage": Storage(aws, cfg),
        "reg": Registry(aws, cfg),
    }


@pytest.fixture(scope="module")
def agent(stack):
    name = "test-" + uuid.uuid4().hex[:8]
    kv, storage, adapter, reg = stack["kv"], stack["storage"], stack["adapter"], stack["reg"]
    storage.ensure_bucket()

    address = kv.new_agent_key(name)
    adapter.fund_eth(address, 1.0)

    card = {"name": name, "address": address, "skills": []}
    card_uri = storage.put(json.dumps(card, sort_keys=True).encode("utf-8"))

    account = kv.signer_for(name)
    agent_id, _ = adapter.register(account, card_uri)
    reg.put_agent(name, agent_id, address, card_uri)
    return {"name": name, "address": address, "agentId": agent_id, "cardURI": card_uri, "card": card}


def test_identity_nft_owned_by_agent(stack, agent):
    owner = stack["adapter"].owner_of(agent["agentId"])
    assert owner.lower() == agent["address"].lower()


def test_cardURI_resolves_to_s3_card(stack, agent):
    on_chain_uri = stack["adapter"].resolve(agent["agentId"])
    assert on_chain_uri == agent["cardURI"]
    fetched = json.loads(stack["storage"].get(on_chain_uri))
    assert fetched == agent["card"]


def test_private_key_not_plaintext(stack, agent):
    kv = stack["kv"]
    ciphertext = kv.ciphertext_for(agent["name"])
    # the stored blob must NOT contain the raw private key bytes
    raw_pk = kv.signer_for(agent["name"]).key  # decrypt path (the only way to plaintext)
    assert raw_pk not in ciphertext
    assert len(ciphertext) > 32  # KMS ciphertext is larger than the 32-byte key
    # and decryption round-trips to the same address
    assert kv.signer_for(agent["name"]).address.lower() == agent["address"].lower()


def test_dynamodb_mirror_row(stack, agent):
    row = stack["reg"].get_agent(agent["name"])
    assert row is not None
    assert row["agentId"] == agent["agentId"]
    assert row["address"].lower() == agent["address"].lower()
    assert row["status"] == "registered"
