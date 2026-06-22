"""plasma_mvp — local-first SDK for the MVP Plasma AI Agent (Milestone 1).

Modules:
  config   - load configuration from .env / environment
  aws      - boto3 clients wired to LocalStack
  storage  - content-addressed object store (LocalStack S3)
  keyvault - agent key custody via KMS-encrypted keystore in Secrets Manager
  registry - agent index mirror (LocalStack DynamoDB)
  adapter  - LocalAdapter: on-chain identity + funding against Anvil (web3.py)
"""

from .config import Config, load_config  # noqa: F401
