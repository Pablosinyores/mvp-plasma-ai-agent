"""x402 paid-resource server + client (M3 spend flow).

Server: a `GET /resource` that answers `402 Payment Required` + a quote until it receives a valid
`X-PAYMENT` header; then it verifies the signed EIP-3009 authorization, settles it on-chain (the
server is the facilitator — it only pays gas; funds move per the signature), and returns `200` + the
goods. Client: `X402Client.get()` transparently pays a 402 once via an `X402Signer` and retries.

Kept deliberately small and transport-agnostic: the client talks to anything exposing `.get(url,
headers=...)` (httpx.Client in prod, FastAPI TestClient in tests), so the spend assertions run with
no extra Docker.
"""
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sdk"))

from fastapi import FastAPI, Header, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from plasma_mvp import x402  # noqa: E402
from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp.config import load_config  # noqa: E402


class X402ResourceServer:
    """The settlement brain behind the /resource endpoint (host-agnostic so tests can drive it)."""

    def __init__(self, adapter, pay_to, price, result_payload=None, settler=None,
                 timeout_seconds=120, events=None, policy=None):
        self.adapter = adapter
        self.pay_to = pay_to
        self.price = int(price)
        self.result_payload = result_payload or {"ok": True, "data": "premium-resource"}
        self.settler = settler or adapter.relayer
        self.timeout_seconds = min(int(timeout_seconds), x402.MAX_VALIDITY_WINDOW)
        self.events = events
        self.policy = policy or x402.SigningPolicy()

    def quote(self) -> x402.PaymentQuote:
        now = self.adapter.w3.eth.get_block("latest")["timestamp"]
        return x402.PaymentQuote(
            pay_to=self.pay_to,
            value=self.price,
            asset=self.adapter.addresses["MockUSDT"],
            chain_id=self.adapter.cfg.chain_id,
            valid_after=now - 1,
            valid_before=now + self.timeout_seconds,
            nonce="0x" + secrets.token_hex(32),
            resource="/resource",
        )

    def quote_402_body(self) -> dict:
        return {
            "x402Version": x402.X402_VERSION,
            "error": "payment required",
            "accepts": [self.quote().to_dict()],
        }

    def settle(self, header_value: str) -> dict:
        """Verify + settle an X-PAYMENT header on-chain. Returns the result payload on success.

        Raises x402.PolicyViolation / ValueError on any failure (caller maps to 402/400)."""
        payload = x402.decode_payment_header(header_value)
        auth = payload["payload"]["authorization"]
        signature = payload["payload"]["signature"]

        # rebuild the typed data and re-run the policy gate (defense in depth on the server side)
        quote = x402.PaymentQuote(
            pay_to=auth["to"], value=auth["value"], asset=self.adapter.addresses["MockUSDT"],
            chain_id=self.adapter.cfg.chain_id, valid_after=auth["validAfter"],
            valid_before=auth["validBefore"], nonce=auth["nonce"],
        )
        typed = x402.build_transfer_authorization_typed_data(quote, auth["from"])
        self.policy.check(typed)

        # the payment must actually pay THIS resource, at least the asking price
        if not _addr_eq(auth["to"], self.pay_to):
            raise ValueError("payment payee {} != resource payee {}".format(auth["to"], self.pay_to))
        if int(auth["value"]) < self.price:
            raise ValueError("underpaid: {} < price {}".format(auth["value"], self.price))

        v, r, s = x402.split_signature(signature)
        tx = self.adapter.transfer_with_authorization(
            self.settler, auth["from"], auth["to"], int(auth["value"]),
            auth["validAfter"], auth["validBefore"], auth["nonce"], v, r, s,
        )
        if self.events is not None:
            self.events.record(
                kind="spend", payer=auth["from"], payee=auth["to"],
                amount=int(auth["value"]), tx=tx, resource="/resource",
            )
        return {"result": self.result_payload, "settlement_tx": tx,
                "paid": int(auth["value"]), "payer": auth["from"]}


def _addr_eq(a, b) -> bool:
    """Byte-equal address comparison on the underlying 20 bytes (case/checksum-insensitive)."""
    def norm(x):
        x = str(x)
        return bytes.fromhex(x[2:] if x.startswith("0x") else x)
    try:
        return norm(a) == norm(b)
    except ValueError:
        return False


def make_resource_app(server: X402ResourceServer) -> FastAPI:
    app = FastAPI(title="x402 paid resource")

    @app.get("/resource")
    def resource(x_payment: str = Header(default=None, alias="X-PAYMENT")):
        if not x_payment:
            return JSONResponse(status_code=402, content=server.quote_402_body())
        try:
            return JSONResponse(status_code=200, content=server.settle(x_payment))
        except x402.PolicyViolation as e:
            return JSONResponse(status_code=402, content={"error": "policy: {}".format(e)})
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=400, content={"error": str(e)})

    @app.get("/health")
    def health():
        return {"ok": True, "price": server.price, "payTo": server.pay_to}

    return app


class X402Client:
    """Wraps any `.get(url, headers=...)` transport; pays a single 402 via an X402Signer and retries."""

    def __init__(self, http, signer):
        self.http = http
        self.signer = signer

    def get(self, url: str):
        resp = self.http.get(url)
        if getattr(resp, "status_code", 200) != 402:
            return resp
        body = resp.json()
        quote = x402.PaymentQuote.from_dict(body["accepts"][0])
        header = self.signer.sign_payment(quote)  # caps + policy enforced here
        return self.http.get(url, headers={"X-PAYMENT": header})


def build_default_server(pay_to, price=1_000_000, cfg=None, events=None) -> X402ResourceServer:
    cfg = cfg or load_config()
    adapter = LocalAdapter(cfg)
    return X402ResourceServer(adapter, pay_to=pay_to, price=price, events=events)
