"""X402Signer — the scoped spend authority. THE prompt-injection-to-wallet-drain defense (design §24).

The agent's tool code (the `on_job` logic, the model output) NEVER holds the raw private key. It holds
an `X402Signer` and may only ask it to "pay this quote". The signer:

  1. enforces a **per-call cap**   — no single payment exceeds `max_value_per_call`,
  2. enforces a **session budget** — cumulative spend across the signer's life ≤ `session_budget`,
  3. enforces a **byte-equal payee** — if constructed with an allow-list, the payee must match exactly,
  4. runs the **SigningPolicy gate** — only bounded transfer-authorizations, never Permit/approvals,

and only then fetches the key (via a `signer_factory` callable — e.g. `keyvault.signer_for(name)`),
signs the EIP-3009 authorization in-memory, and drops it. So even if the model is fully compromised
and emits "pay attacker 1e6", the most it can move is one `max_value_per_call`, capped again by the
remaining session budget — and never to an address outside the allow-list.
"""
from eth_utils import to_checksum_address

from . import x402


class SpendCapExceeded(Exception):
    """Raised when a requested payment violates the per-call cap or session budget."""


class PayeeNotAllowed(Exception):
    """Raised when the payee is not byte-equal to an allow-listed address."""


class X402Signer:
    def __init__(self, signer_factory, max_value_per_call, session_budget,
                 allowed_payees=None, policy=None, address=None):
        """
        signer_factory: zero-arg callable returning an eth_account LocalAccount (key fetched per sign).
        max_value_per_call / session_budget: base units (USDT 6dp).
        allowed_payees: optional iterable of addresses; if set, payee must byte-equal one of them.
        """
        if not callable(signer_factory):
            raise TypeError("signer_factory must be a zero-arg callable returning a LocalAccount")
        self._signer_factory = signer_factory
        self.max_value_per_call = int(max_value_per_call)
        self.session_budget = int(session_budget)
        self.policy = policy or x402.SigningPolicy()
        self._spent = 0
        self._address = to_checksum_address(address) if address else None
        # store allow-listed payees as raw 20-byte values for byte-equal comparison
        self._allowed_payee_bytes = None
        if allowed_payees is not None:
            self._allowed_payee_bytes = {self._addr_bytes(a) for a in allowed_payees}

    # --- introspection --------------------------------------------------------
    @property
    def spent(self) -> int:
        return self._spent

    @property
    def remaining(self) -> int:
        return self.session_budget - self._spent

    @property
    def address(self) -> str:
        if self._address is None:
            self._address = to_checksum_address(self._signer_factory().address)
        return self._address

    @staticmethod
    def _addr_bytes(addr) -> bytes:
        return bytes.fromhex(to_checksum_address(addr)[2:])

    # --- the guarded sign -----------------------------------------------------
    def _enforce(self, value: int, pay_to: str) -> None:
        value = int(value)
        if value <= 0:
            raise SpendCapExceeded("payment value must be positive")
        if value > self.max_value_per_call:
            raise SpendCapExceeded(
                "payment {} exceeds per-call cap {}".format(value, self.max_value_per_call)
            )
        if self._spent + value > self.session_budget:
            raise SpendCapExceeded(
                "payment {} would exceed remaining session budget {}".format(value, self.remaining)
            )
        if self._allowed_payee_bytes is not None:
            if self._addr_bytes(pay_to) not in self._allowed_payee_bytes:
                raise PayeeNotAllowed("payee {} is not in the allow-list".format(pay_to))

    def sign_payment(self, quote: x402.PaymentQuote) -> str:
        """Validate against caps + policy, sign the authorization, return an X-PAYMENT header.

        Raises SpendCapExceeded / PayeeNotAllowed / PolicyViolation WITHOUT touching the key.
        On success the session budget is debited by the quote value.
        """
        # 1) hard spend limits + payee allow-list — checked before the key is ever fetched
        self._enforce(quote.value, quote.pay_to)

        # 2) the policy gate on the actual EIP-712 payload (denies Permit/Permit2, window > 600s)
        payer = self.address
        typed_data = x402.build_transfer_authorization_typed_data(quote, payer)
        self.policy.check(typed_data)

        # 3) only now fetch the key, sign in-memory, and let it fall out of scope
        account = self._signer_factory()
        signature = x402.sign_authorization(typed_data, account)

        # 4) debit the session budget only after a successful sign
        self._spent += int(quote.value)
        return x402.encode_payment_header(quote, payer, signature)
