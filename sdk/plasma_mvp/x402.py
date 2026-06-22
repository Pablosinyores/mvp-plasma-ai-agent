"""x402 payment primitives — EIP-3009 ("exact" scheme) authorizations + the signing-policy gate.

This is the build/verify half of x402 (the scoped *signer* with the spend caps lives in `signer.py`).
A paid resource answers `402` with a **quote** (PaymentRequirements); the client signs an EIP-3009
`TransferWithAuthorization` over MockUSDT and replays with an `X-PAYMENT` header; the resource server
verifies and settles the authorization on-chain.

The **SigningPolicy** gate is one of the design's "three that sink teams" (§24): it allows ONLY
transfer-authorization typed-data, hard-denies `Permit`/`Permit2`/arbitrary approvals, and rejects any
validity window longer than 600s. It inspects the EIP-712 payload itself, so it catches a malicious
quote regardless of where the values came from (e.g. prompt-injected into the agent).
"""
import base64
import json

from eth_account import Account
from eth_utils import to_bytes
from web3 import Web3

EIP712_DOMAIN_NAME = "Mock USDT"
EIP712_DOMAIN_VERSION = "1"
MAX_VALIDITY_WINDOW = 600  # seconds — hard ceiling on an authorization's lifetime

# EIP-712 primary types the policy gate will permit / refuse.
ALLOWED_TYPES = {"TransferWithAuthorization", "ReceiveWithAuthorization"}
DENIED_TYPES = {
    "Permit",            # EIP-2612
    "PermitSingle",      # Permit2
    "PermitBatch",       # Permit2
    "PermitTransferFrom",  # Permit2 SignatureTransfer
    "Permit2",
}

X402_VERSION = 1


class PolicyViolation(Exception):
    """Raised when a signing request fails the policy gate (the drain defense)."""


# --- the quote a 402 response carries ----------------------------------------
class PaymentQuote:
    """PaymentRequirements: what a 402-gated resource demands. Times are chain-authoritative."""

    def __init__(self, pay_to, value, asset, chain_id, valid_after, valid_before, nonce,
                 scheme="exact", network="anvil", resource=""):
        self.pay_to = Web3.to_checksum_address(pay_to)
        self.value = int(value)
        self.asset = Web3.to_checksum_address(asset)
        self.chain_id = int(chain_id)
        self.valid_after = int(valid_after)
        self.valid_before = int(valid_before)
        self.nonce = nonce if str(nonce).startswith("0x") else "0x" + str(nonce)
        self.scheme = scheme
        self.network = network
        self.resource = resource

    @property
    def window(self) -> int:
        return self.valid_before - self.valid_after

    def to_dict(self) -> dict:
        return {
            "scheme": self.scheme,
            "network": self.network,
            "asset": self.asset,
            "payTo": self.pay_to,
            "value": str(self.value),
            "chainId": self.chain_id,
            "validAfter": self.valid_after,
            "validBefore": self.valid_before,
            "nonce": self.nonce,
            "resource": self.resource,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PaymentQuote":
        return cls(
            pay_to=d["payTo"],
            value=int(d["value"]),
            asset=d["asset"],
            chain_id=int(d["chainId"]),
            valid_after=int(d["validAfter"]),
            valid_before=int(d["validBefore"]),
            nonce=d["nonce"],
            scheme=d.get("scheme", "exact"),
            network=d.get("network", "anvil"),
            resource=d.get("resource", ""),
        )


def build_transfer_authorization_typed_data(quote: PaymentQuote, payer: str) -> dict:
    """EIP-712 typed data for a MockUSDT `TransferWithAuthorization` matching the on-chain hash."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "domain": {
            "name": EIP712_DOMAIN_NAME,
            "version": EIP712_DOMAIN_VERSION,
            "chainId": quote.chain_id,
            "verifyingContract": quote.asset,
        },
        "primaryType": "TransferWithAuthorization",
        "message": {
            "from": Web3.to_checksum_address(payer),
            "to": quote.pay_to,
            "value": quote.value,
            "validAfter": quote.valid_after,
            "validBefore": quote.valid_before,
            "nonce": to_bytes(hexstr=quote.nonce),
        },
    }


class SigningPolicy:
    """Allow-list gate over EIP-712 typed data. Refuses anything that isn't a bounded transfer auth."""

    def __init__(self, max_validity_window=MAX_VALIDITY_WINDOW, allowed_types=None, denied_types=None):
        self.max_validity_window = int(max_validity_window)
        self.allowed_types = set(allowed_types) if allowed_types else set(ALLOWED_TYPES)
        self.denied_types = set(denied_types) if denied_types else set(DENIED_TYPES)

    def check(self, typed_data: dict) -> bool:
        ptype = typed_data.get("primaryType")
        if ptype in self.denied_types:
            raise PolicyViolation("denied signing type: {} (Permit/approval not allowed)".format(ptype))
        if ptype not in self.allowed_types:
            raise PolicyViolation("signing type not allow-listed: {}".format(ptype))

        msg = typed_data.get("message", {})
        if "validBefore" not in msg or "validAfter" not in msg:
            raise PolicyViolation("authorization missing validity window")
        valid_before = int(msg["validBefore"])
        valid_after = int(msg["validAfter"])
        if valid_before == 0:
            raise PolicyViolation("open-ended authorization (validBefore == 0) rejected")
        window = valid_before - valid_after
        if window > self.max_validity_window:
            raise PolicyViolation(
                "validity window {}s exceeds max {}s".format(window, self.max_validity_window)
            )
        return True


def sign_authorization(typed_data: dict, account) -> str:
    """Sign EIP-712 typed data with an eth_account LocalAccount; return a 0x-prefixed signature."""
    signed = Account.sign_typed_data(account.key, full_message=typed_data)
    sig = signed.signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def split_signature(signature: str):
    """Split a 65-byte 0x signature into (v, r, s) for the EIP-3009 contract call."""
    sig = to_bytes(hexstr=signature) if isinstance(signature, str) else bytes(signature)
    if len(sig) != 65:
        raise ValueError("expected 65-byte signature, got {}".format(len(sig)))
    r = sig[0:32]
    s = sig[32:64]
    v = sig[64]
    if v < 27:
        v += 27
    return v, r, s


# --- X-PAYMENT header codec ---------------------------------------------------
def encode_payment_header(quote: PaymentQuote, payer: str, signature: str) -> str:
    """base64(JSON) X-PAYMENT payload carrying the signed authorization."""
    payload = {
        "x402Version": X402_VERSION,
        "scheme": quote.scheme,
        "network": quote.network,
        "payload": {
            "authorization": {
                "from": Web3.to_checksum_address(payer),
                "to": quote.pay_to,
                "value": str(quote.value),
                "validAfter": quote.valid_after,
                "validBefore": quote.valid_before,
                "nonce": quote.nonce,
            },
            "signature": signature if signature.startswith("0x") else "0x" + signature,
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_payment_header(header: str) -> dict:
    return json.loads(base64.b64decode(header.encode("ascii")))
