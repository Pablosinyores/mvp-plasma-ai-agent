"""Configuration loading for the MVP SDK.

Reads from process environment, falling back to a `.env` file at the repo root. No hard dependency
on python-dotenv — a tiny parser keeps the dependency surface minimal.
"""
import os
from dataclasses import dataclass
from pathlib import Path

# monorepo root: backend/sdk/plasma_mvp/config.py -> repo root (contracts/, infra/, .env live here)
REPO_ROOT = Path(__file__).resolve().parents[3]

# Canonical ERC-4337 EntryPoint v0.7 (same address on every chain it is deployed to).
DEFAULT_ENTRYPOINT_V07 = "0x0000000071727De22E5E9d8BAf0edAc6f37da032"

# GAS_TIER selects how the user pays for gas (issue #8 — gasless single-token UX):
#   native-float : user holds native gas + USDT (baseline; no abstraction).
#   usdt-as-gas  : USDT is the gas token; no native float needed.
#   7702-4337    : EOA delegates (EIP-7702) to a smart account and an ERC-4337 paymaster
#                  sponsors gas, settling the user's spend in USDT — fully gasless single-token.
VALID_GAS_TIERS = ("native-float", "usdt-as-gas", "7702-4337")
DEFAULT_GAS_TIER = "native-float"


def _load_dotenv(path: Path) -> None:
    """Populate os.environ from a .env file without overriding already-set vars."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Config:
    # chain
    rpc_url: str
    chain_id: int
    relayer_pk: str
    # aws / localstack
    aws_endpoint_url: str
    aws_region: str
    s3_bucket: str
    kms_key_alias: str
    ddb_table: str
    sqs_queue: str
    # storage
    storage_backend: str
    storage_local_path: str
    ipfs_api_url: str
    # gas abstraction (ERC-4337 / EIP-7702 — issue #8)
    entrypoint_address: str
    paymaster_address: str
    gas_tier: str
    # paths
    deployments_path: Path
    contracts_out: Path

    @property
    def aws_creds(self) -> dict:
        creds = {
            "aws_access_key_id": os.environ.get("AWS_ACCESS_KEY_ID", "test"),
            "aws_secret_access_key": os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
            "region_name": self.aws_region,
        }
        # Only pin a custom endpoint when one is set (LocalStack). Empty/unset => real AWS.
        if self.aws_endpoint_url:
            creds["endpoint_url"] = self.aws_endpoint_url
        return creds


def load_config() -> Config:
    return Config(
        rpc_url=os.environ.get("RPC_URL", "http://localhost:8545"),
        chain_id=int(os.environ.get("CHAIN_ID", "31337")),
        relayer_pk=os.environ.get(
            "RELAYER_PK",
            # default = Anvil account[0] (well-known test key, local only)
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        ),
        aws_endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        aws_region=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        s3_bucket=os.environ.get("S3_BUCKET", "agent-cards"),
        kms_key_alias=os.environ.get("KMS_KEY_ALIAS", "alias/agent-master"),
        ddb_table=os.environ.get("DDB_TABLE", "agents"),
        sqs_queue=os.environ.get("SQS_QUEUE", "settle"),
        storage_backend=os.environ.get("STORAGE_BACKEND", "s3"),
        storage_local_path=os.environ.get(
            "STORAGE_LOCAL_PATH", str(REPO_ROOT / ".agent" / "storage")
        ),
        ipfs_api_url=os.environ.get("IPFS_API_URL", "http://localhost:5001"),
        entrypoint_address=os.environ.get("ENTRYPOINT_ADDRESS", DEFAULT_ENTRYPOINT_V07),
        paymaster_address=os.environ.get("PAYMASTER_ADDRESS", ""),
        gas_tier=_gas_tier(),
        deployments_path=REPO_ROOT / "contracts" / "deployments" / "local.json",
        contracts_out=REPO_ROOT / "contracts" / "out",
    )


def _gas_tier() -> str:
    """Read GAS_TIER, validating it against the supported tiers so a typo fails loudly."""
    tier = os.environ.get("GAS_TIER", DEFAULT_GAS_TIER)
    if tier not in VALID_GAS_TIERS:
        raise ValueError(
            "GAS_TIER={!r} is not one of {}".format(tier, VALID_GAS_TIERS)
        )
    return tier
