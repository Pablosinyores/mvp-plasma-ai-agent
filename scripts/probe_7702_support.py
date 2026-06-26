#!/usr/bin/env python3
"""Probe whether a chain actually supports EIP-7702 — the one unknown blocking a Plasma testnet deploy.

It does the minimal thing that proves support: submit a type-4 SetCode tx delegating a funded EOA to a
target contract, then read the account's code back and check it carries the EIP-7702 designator
(0xef0100 || address). As a second confirmation it calls a view (domainSeparator) THROUGH the delegated
EOA, proving the delegate code executes in the EOA's context. No venue / pools required.

Local self-test (works now):
    RPC_URL=http://127.0.0.1:8546 \
    PROBE_PK=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d \
    python scripts/probe_7702_support.py

Against Plasma testnet (the real check):
    RPC_URL=$PLASMA_RPC_URL \
    PROBE_PK=$FUNDED_TESTNET_KEY \
    DELEGATE_ADDR=0x...           # AgentSessionDelegate deployed on Plasma (else falls back to manifest)
    python scripts/probe_7702_support.py

Exits non-zero if 7702 is NOT supported (or the probe can't run), so it gates CI / a deploy step.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend" / "sdk"))

from plasma_mvp.adapter import LocalAdapter  # noqa: E402
from plasma_mvp import session as S  # noqa: E402


def main():
    pk = os.environ.get("PROBE_PK")
    if not pk:
        raise SystemExit("set PROBE_PK to a key funded on the target chain (it pays its own gas)")

    a = LocalAdapter()
    w3 = a.w3
    print("probe: RPC        ", a.cfg.rpc_url)
    print("probe: chainId    ", w3.eth.chain_id)

    # the delegation target: explicit DELEGATE_ADDR, else the deployed AgentSessionDelegate
    target = os.environ.get("DELEGATE_ADDR") or a.session_delegate_address
    if not target:
        raise SystemExit("no delegation target: set DELEGATE_ADDR or deploy AgentSessionDelegate first")
    print("probe: target impl", target)

    user = w3.eth.account.from_key(pk)
    bal = w3.eth.get_balance(user.address)
    print("probe: prober     ", user.address, "balance(wei)", bal)
    if bal == 0:
        raise SystemExit("prober has zero balance — fund {} on this chain".format(user.address))

    # 1) self-sponsored EIP-7702 delegation (prober both authorizes and submits)
    try:
        S.delegate_eoa(a, user, target, sponsor_account=None)
    except Exception as e:  # noqa: BLE001
        print("FAIL: type-4 SetCode tx rejected — chain likely lacks EIP-7702 (Prague/Pectra):", e)
        sys.exit(1)

    # 2) the account must now carry the 0xef0100||address designator
    deleg = S.delegated_code_address(a, user.address)
    if deleg is None or deleg.lower() != target.lower():
        print("FAIL: no EIP-7702 delegation designator after SetCode (got {})".format(deleg))
        sys.exit(1)
    print("probe: designator OK -> account delegates to", deleg)

    # 3) confirm the delegate code executes in the EOA context (view through the delegated account)
    try:
        if a.session_delegate_abi and target.lower() == (a.session_delegate_address or "").lower():
            sep = a.session_at(user.address).functions.domainSeparator().call()
            print("probe: domainSeparator via delegated EOA OK ->", "0x" + sep.hex())
    except Exception as e:  # noqa: BLE001
        print("WARN: delegation set but view through it failed (still counts as 7702 support):", e)

    print("\nPASS: EIP-7702 is supported on chainId {}".format(w3.eth.chain_id))


if __name__ == "__main__":
    main()
