"""Agent key custody via KMS-encrypted keystore in Secrets Manager.

Security model (carried from the design, §17):
  - a fresh agent keypair is generated locally,
  - the private key is encrypted with a KMS master key (envelope encryption),
  - only the ciphertext is stored, in Secrets Manager,
  - the plaintext key exists in memory ONLY at the moment of signing, never at rest.

This satisfies the M1 acceptance assertion: "private key is NOT retrievable in plaintext
(only KMS-decryptable)".
"""
import base64

from eth_account import Account

from .aws import Aws
from .config import Config, load_config


class KeyVault:
    def __init__(self, aws: Aws = None, cfg: Config = None):
        self.cfg = cfg or load_config()
        self.aws = aws or Aws(self.cfg)
        self.key_alias = self.cfg.kms_key_alias

    def _secret_name(self, name: str) -> str:
        return "agents/{}".format(name)

    def new_agent_key(self, name: str) -> str:
        """Generate a keypair, KMS-encrypt the private key, store ciphertext. Return the address."""
        acct = Account.create()
        pk_bytes = acct.key  # 32 raw bytes
        ciphertext = self.aws.kms.encrypt(KeyId=self.key_alias, Plaintext=pk_bytes)["CiphertextBlob"]
        secret_value = base64.b64encode(ciphertext).decode("ascii")

        secret_name = self._secret_name(name)
        try:
            self.aws.secrets.create_secret(Name=secret_name, SecretString=secret_value)
        except self.aws.secrets.exceptions.ResourceExistsException:
            self.aws.secrets.put_secret_value(SecretId=secret_name, SecretString=secret_value)
        return acct.address

    def ciphertext_for(self, name: str) -> bytes:
        """Return the raw KMS ciphertext stored for an agent (used by tests to prove no plaintext)."""
        secret_value = self.aws.secrets.get_secret_value(SecretId=self._secret_name(name))
        return base64.b64decode(secret_value["SecretString"])

    def signer_for(self, name: str):
        """Decrypt the agent key (in memory only) and return an eth_account LocalAccount."""
        ciphertext = self.ciphertext_for(name)
        pk_bytes = self.aws.kms.decrypt(CiphertextBlob=ciphertext)["Plaintext"]
        return Account.from_key(pk_bytes)

    def address_of(self, name: str) -> str:
        return self.signer_for(name).address
