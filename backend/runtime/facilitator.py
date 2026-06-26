"""x402 payment facilitator.

A standalone service that verifies and settles EIP-3009 `TransferWithAuthorization` payments. It is
the counterpart to the 402-gated resource: a resource hands a client `paymentRequirements`, the client
signs an authorization, and the facilitator's two endpoints validate it (`/verify`) and submit it
on-chain from a gas-funded relayer (`/settle`).

  POST /verify  { paymentPayload, paymentRequirements } -> { isValid, reason? }
  POST /settle  { paymentPayload, paymentRequirements } -> { txHash }

`paymentPayload` is the decoded X-PAYMENT body ({ payload: { authorization, signature } }, the shape
`x402.decode_payment_header` produces). `paymentRequirements` is a PaymentQuote dict.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from plasma_mvp import x402  # noqa: E402

# secp256k1 order; an s above half-order is the malleable "high-s" form and is rejected (EIP-2).
_SECP256K1N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_HALF_N = _SECP256K1N // 2


class VerificationError(Exception):
    """A payment failed validation; the message is the human-readable reason."""


def _addr_eq(a, b) -> bool:
    def norm(x):
        x = str(x)
        return bytes.fromhex(x[2:] if x.startswith("0x") else x)
    try:
        return norm(a) == norm(b)
    except ValueError:
        return False


class Facilitator:
    """Verifies + settles signed EIP-3009 authorizations against the chain the adapter is wired to."""

    def __init__(self, adapter, policy: x402.SigningPolicy = None):
        self.adapter = adapter
        self.policy = policy or x402.SigningPolicy()
        self.token = adapter.addresses["MockUSDT"]
        self.chain_id = int(adapter.cfg.chain_id)

    def _quote(self, req: dict) -> x402.PaymentQuote:
        return x402.PaymentQuote.from_dict(req)

    def verify(self, payment_payload: dict, requirements: dict) -> dict:
        """Return {isValid: True} or {isValid: False, reason}. Never raises for a bad payment."""
        try:
            self._validate(payment_payload, requirements)
            return {"isValid": True}
        except VerificationError as e:
            return {"isValid": False, "reason": str(e)}
        except x402.PolicyViolation as e:
            return {"isValid": False, "reason": "policy: {}".format(e)}

    def _validate(self, payment_payload: dict, requirements: dict) -> dict:
        auth = payment_payload["payload"]["authorization"]
        signature = payment_payload["payload"]["signature"]
        req = self._quote(requirements)

        # domain must bind to THIS chain and THIS token, else a signature could be replayed elsewhere
        if int(req.chain_id) != self.chain_id:
            raise VerificationError("chainId {} != facilitator {}".format(req.chain_id, self.chain_id))
        if not _addr_eq(req.asset, self.token):
            raise VerificationError("asset {} != settlement token".format(req.asset))

        # scheme / network advertised by the requirements
        if req.scheme != "exact":
            raise VerificationError("unsupported scheme {}".format(req.scheme))

        # rebuild the exact typed data the payer signed and run the signing-policy gate
        quote = x402.PaymentQuote(
            pay_to=auth["to"], value=auth["value"], asset=self.token, chain_id=self.chain_id,
            valid_after=auth["validAfter"], valid_before=auth["validBefore"], nonce=auth["nonce"],
        )
        typed = x402.build_transfer_authorization_typed_data(quote, auth["from"])
        self.policy.check(typed)  # raises PolicyViolation -> caught below

        # recover the signer; it must be the declared payer (from)
        v, r, s = x402.split_signature(signature)
        if int.from_bytes(s, "big") > _HALF_N:
            raise VerificationError("high-s signature rejected")
        recovered = Account.recover_message(encode_typed_data(full_message=typed), signature=signature)
        if not _addr_eq(recovered, auth["from"]):
            raise VerificationError("signature does not match declared payer")

        # economic + temporal checks
        if int(auth["value"]) <= 0:
            raise VerificationError("non-positive value")
        if int(auth["value"]) < int(req.value):
            raise VerificationError("underpaid: {} < {}".format(auth["value"], req.value))
        if not _addr_eq(auth["to"], req.pay_to):
            raise VerificationError("payee {} != required {}".format(auth["to"], req.pay_to))
        now = int(time.time())
        if now < int(auth["validAfter"]):
            raise VerificationError("authorization not yet valid")
        if now > int(auth["validBefore"]):
            raise VerificationError("authorization expired")

        # nonce must be unused on-chain (authoritative replay guard)
        if self.adapter.authorization_used(auth["from"], auth["nonce"]):
            raise VerificationError("nonce already used")

        return {"auth": auth, "v": v, "r": r, "s": s}

    def settle(self, payment_payload: dict, requirements: dict) -> dict:
        """Re-verify then submit `transferWithAuthorization` from the relayer; return {txHash}."""
        try:
            parsed = self._validate(payment_payload, requirements)
        except x402.PolicyViolation as e:
            raise VerificationError("policy: {}".format(e))
        auth = parsed["auth"]
        tx = self.adapter.transfer_with_authorization(
            self.adapter.relayer, auth["from"], auth["to"], int(auth["value"]),
            auth["validAfter"], auth["validBefore"], auth["nonce"],
            parsed["v"], parsed["r"], parsed["s"],
        )
        return {"txHash": tx}


def make_facilitator_app(facilitator: Facilitator) -> FastAPI:
    app = FastAPI(title="x402 facilitator")

    @app.get("/health")
    def health():
        return {"ok": True, "token": facilitator.token, "chainId": facilitator.chain_id}

    @app.post("/verify")
    def verify(body: dict):
        try:
            return facilitator.verify(body["paymentPayload"], body["paymentRequirements"])
        except x402.PolicyViolation as e:
            return {"isValid": False, "reason": "policy: {}".format(e)}
        except (KeyError, TypeError) as e:
            return JSONResponse(status_code=400, content={"error": "malformed request: {}".format(e)})

    @app.post("/settle")
    def settle(body: dict):
        try:
            return facilitator.settle(body["paymentPayload"], body["paymentRequirements"])
        except VerificationError as e:
            return JSONResponse(status_code=402, content={"error": str(e)})
        except (KeyError, TypeError) as e:
            return JSONResponse(status_code=400, content={"error": "malformed request: {}".format(e)})

    return app
