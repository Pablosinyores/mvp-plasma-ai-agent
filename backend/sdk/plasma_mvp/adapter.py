"""LocalAdapter — on-chain access against Anvil via web3.py.

This is the M1 slice of the ChainAdapter interface from the design: identity registration + resolve,
plus the funding helpers (ETH for gas, MockUSDT for settlement). Gas is paid by whichever account
signs; in M1 the relayer funds each agent with a little ETH so the agent can self-sign its own
registration (the agent owns its identity).

The full `send_tx` gas-decision tree (native paymaster / USDT-gas / AppPaymaster) lands in M2/M3;
here the relayer/agent simply pay gas directly on the free local chain.
"""
import json

from web3 import Web3
from web3.logs import DISCARD

from .config import Config, load_config


def _load_abi(out_dir, contract: str):
    path = out_dir / "{}.sol".format(contract) / "{}.json".format(contract)
    with open(path) as f:
        return json.load(f)["abi"]


# Commerce.Status enum order (keep in sync with contracts/src/Commerce.sol)
JOB_STATUS = ["NONE", "OPEN", "FUNDED", "SUBMITTED", "COMPLETED", "REJECTED", "EXPIRED"]


class LocalAdapter:
    def __init__(self, cfg: Config = None):
        self.cfg = cfg or load_config()
        self.w3 = Web3(Web3.HTTPProvider(self.cfg.rpc_url))
        if not self.w3.is_connected():
            raise ConnectionError("cannot reach RPC at {}".format(self.cfg.rpc_url))

        self.relayer = self.w3.eth.account.from_key(self.cfg.relayer_pk)

        deployments = json.loads(self.cfg.deployments_path.read_text())
        self.addresses = deployments
        self.usdt = self.w3.eth.contract(
            address=Web3.to_checksum_address(deployments["MockUSDT"]),
            abi=_load_abi(self.cfg.contracts_out, "MockUSDT"),
        )
        self.identity = self.w3.eth.contract(
            address=Web3.to_checksum_address(deployments["IdentityRegistry"]),
            abi=_load_abi(self.cfg.contracts_out, "IdentityRegistry"),
        )
        self.commerce = self.w3.eth.contract(
            address=Web3.to_checksum_address(deployments["Commerce"]),
            abi=_load_abi(self.cfg.contracts_out, "Commerce"),
        )
        self.dispute_window = int(deployments.get("disputeWindow", 5))

        # Optional local swap venue. `tokens` maps symbol -> ERC-20 contract; `pools` maps a
        # frozenset({symA,symB}) -> MiniAMM. Only built when the manifest carries them, so older
        # deployments keep working unchanged.
        self.tokens = {}
        self.pools = {}
        self._decimals = {}
        erc20_abi = _load_abi(self.cfg.contracts_out, "MiniERC20")
        for sym in ("USDC", "WETH", "WXPL"):
            if sym in deployments:
                abi = _load_abi(self.cfg.contracts_out, "WXPL") if sym == "WXPL" else erc20_abi
                self.tokens[sym] = self.w3.eth.contract(
                    address=Web3.to_checksum_address(deployments[sym]), abi=abi
                )
        amm_abi = _load_abi(self.cfg.contracts_out, "MiniAMM")
        for key, addr in deployments.items():
            if key.startswith("Pool_"):
                a, b = key[len("Pool_"):].split("_")
                self.pools[frozenset((a, b))] = self.w3.eth.contract(
                    address=Web3.to_checksum_address(addr), abi=amm_abi
                )

        # Optional EIP-7702 session-delegate (the "trade from the user's own address" rail). Only the
        # implementation ADDRESS lives in the manifest; at runtime the user EOA delegates its code to
        # it, so calls are made against the *user's* address with this ABI. Kept optional so older
        # deployments load unchanged.
        self.session_delegate_address = None
        self.session_delegate_abi = None
        if "AgentSessionDelegate" in deployments:
            self.session_delegate_address = Web3.to_checksum_address(
                deployments["AgentSessionDelegate"]
            )
            self.session_delegate_abi = _load_abi(self.cfg.contracts_out, "AgentSessionDelegate")

        # back-compat handles for the original USDC->WETH swap path (SwapGuard/test_swap)
        self.usdc = self.tokens.get("USDC")
        self.weth = self.tokens.get("WETH")
        self.amm = self.pools.get(frozenset(("USDC", "WETH")))

    # ---- deployment sanity ---------------------------------------------------
    def assert_chain_ready(self, *, require_session=False) -> None:
        """Fail LOUDLY if the connected chain doesn't actually have the manifest's contracts deployed.
        Catches the classic footgun where RPC points at the wrong node (e.g. a Docker proxy shadowing
        localhost:8545 with a fresh default-balance chain) — the manifest loads fine but every call
        reverts cryptically. Raises a single actionable error instead."""
        checks = [("MockUSDT", self.addresses.get("MockUSDT")),
                  ("Commerce", self.addresses.get("Commerce"))]
        if require_session:
            checks.append(("AgentSessionDelegate", self.session_delegate_address))
        missing = []
        for name, addr in checks:
            if not addr:
                missing.append("{} (absent from manifest)".format(name))
                continue
            if self.w3.eth.get_code(Web3.to_checksum_address(addr)) in (b"", b"0x", "0x"):
                missing.append("{} @ {} has no code".format(name, addr))
        if missing:
            raise RuntimeError(
                "chain at {} is not the deployed one — {}. Re-run the deploy against THIS RPC, and "
                "check `lsof -nP -iTCP:8545 -sTCP:LISTEN` for a Docker proxy shadowing the port.".format(
                    self.cfg.rpc_url, "; ".join(missing)
                )
            )

    # ---- low-level tx helper -------------------------------------------------
    def _send(self, account, tx: dict) -> dict:
        tx = dict(tx)
        tx.setdefault("from", account.address)
        tx.setdefault("nonce", self.w3.eth.get_transaction_count(account.address))
        tx.setdefault("chainId", self.cfg.chain_id)
        # EIP-1559 fees, unless the tx already carries fee fields (e.g. from build_transaction).
        # Priority fee is capped at the chain's current gas price so it can never exceed maxFee —
        # on ultra-cheap chains (e.g. Plasma, base fee ~1e-7 gwei) a hardcoded 1 gwei tip would be
        # larger than maxFee and the node would reject the tx ("max fee < max priority fee").
        if "gasPrice" not in tx and "maxFeePerGas" not in tx:
            base = self.w3.eth.gas_price
            tip = min(self.w3.to_wei(1, "gwei"), base)
            tx["maxPriorityFeePerGas"] = tip
            tx["maxFeePerGas"] = base * 2 + tip
        if "gas" not in tx:
            tx["gas"] = int(self.w3.eth.estimate_gas(tx) * 1.2)
        signed = account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    # ---- funding -------------------------------------------------------------
    def fund_eth(self, to: str, amount_eth: float = 1.0) -> str:
        receipt = self._send(
            self.relayer,
            {"to": Web3.to_checksum_address(to), "value": self.w3.to_wei(amount_eth, "ether")},
        )
        return receipt["transactionHash"].hex()

    def mint_usdt(self, to: str, amount: int) -> str:
        """Mint MockUSDT (6 decimals). `amount` is in base units (1 USDT = 1_000_000)."""
        tx = self.usdt.functions.mint(Web3.to_checksum_address(to), int(amount)).build_transaction(
            {"from": self.relayer.address, "nonce": self.w3.eth.get_transaction_count(self.relayer.address)}
        )
        receipt = self._send(self.relayer, tx)
        return receipt["transactionHash"].hex()

    # ---- identity ------------------------------------------------------------
    def register(self, agent_account, card_uri: str):
        """Agent self-signs its registration. Returns (agent_id, tx_hash)."""
        tx = self.identity.functions.register(card_uri).build_transaction(
            {"from": agent_account.address,
             "nonce": self.w3.eth.get_transaction_count(agent_account.address)}
        )
        receipt = self._send(agent_account, tx)
        logs = self.identity.events.Registered().process_receipt(receipt, errors=DISCARD)
        agent_id = int(logs[0]["args"]["agentId"])
        return agent_id, receipt["transactionHash"].hex()

    def resolve(self, agent_id: int) -> str:
        return self.identity.functions.cardURI(int(agent_id)).call()

    def owner_of(self, agent_id: int) -> str:
        return self.identity.functions.ownerOf(int(agent_id)).call()

    def agent_id_of(self, address: str) -> int:
        return int(self.identity.functions.agentIdOf(Web3.to_checksum_address(address)).call())

    # ---- balances ------------------------------------------------------------
    def usdt_balance(self, address: str) -> int:
        return int(self.usdt.functions.balanceOf(Web3.to_checksum_address(address)).call())

    def eth_balance(self, address: str) -> int:
        return int(self.w3.eth.get_balance(Web3.to_checksum_address(address)))

    # ---- commerce / escrow ---------------------------------------------------
    def _send_fn(self, account, fn):
        """Build + sign + send a contract function call from `account`."""
        tx = fn.build_transaction(
            {"from": account.address, "nonce": self.w3.eth.get_transaction_count(account.address)}
        )
        return self._send(account, tx)

    def approve_usdt(self, account, spender: str, amount: int) -> str:
        receipt = self._send_fn(
            account, self.usdt.functions.approve(Web3.to_checksum_address(spender), int(amount))
        )
        return receipt["transactionHash"].hex()

    # ---- x402 / EIP-3009 (M3) ------------------------------------------------
    def usdt_domain(self) -> dict:
        """EIP-712 domain for MockUSDT (name/version/chainId/verifyingContract)."""
        return {
            "name": "Mock USDT",
            "version": "1",
            "chainId": self.cfg.chain_id,
            "verifyingContract": Web3.to_checksum_address(self.addresses["MockUSDT"]),
        }

    def authorization_used(self, authorizer: str, nonce) -> bool:
        nonce_bytes = nonce if isinstance(nonce, (bytes, bytearray)) else bytes.fromhex(
            nonce[2:] if str(nonce).startswith("0x") else nonce
        )
        return bool(
            self.usdt.functions.authorizationState(
                Web3.to_checksum_address(authorizer), nonce_bytes
            ).call()
        )

    def transfer_with_authorization(self, submitter, frm, to, value, valid_after, valid_before,
                                    nonce, v, r, s) -> str:
        """Settle a signed EIP-3009 authorization on-chain. `submitter` only pays gas (the
        facilitator role); funds move strictly per the signature. Returns the tx hash."""
        nonce_bytes = nonce if isinstance(nonce, (bytes, bytearray)) else bytes.fromhex(
            nonce[2:] if str(nonce).startswith("0x") else nonce
        )
        fn = self.usdt.functions.transferWithAuthorization(
            Web3.to_checksum_address(frm),
            Web3.to_checksum_address(to),
            int(value),
            int(valid_after),
            int(valid_before),
            nonce_bytes,
            int(v),
            r if isinstance(r, (bytes, bytearray)) else bytes.fromhex(str(r)[2:]),
            s if isinstance(s, (bytes, bytearray)) else bytes.fromhex(str(s)[2:]),
        )
        receipt = self._send_fn(submitter, fn)
        return receipt["transactionHash"].hex()

    # ---- EIP-7702 session delegate (trade from the user's own address) -------
    def session_at(self, user_address: str):
        """Web3 contract bound to a *user EOA* that has delegated its code to AgentSessionDelegate.
        Calls (installSession / revokeSession / executeTrade / views) run in the user's context."""
        if self.session_delegate_abi is None:
            raise RuntimeError("no AgentSessionDelegate in this deployment")
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(user_address), abi=self.session_delegate_abi
        )

    # ---- generic multi-pair venue (any allow-listed token pair) --------------
    def token_decimals(self, symbol: str) -> int:
        if symbol not in self._decimals:
            self._decimals[symbol] = int(self.tokens[symbol].functions.decimals().call())
        return self._decimals[symbol]

    def token_balance(self, symbol: str, address: str) -> int:
        return int(self.tokens[symbol].functions.balanceOf(Web3.to_checksum_address(address)).call())

    def mint_token(self, symbol: str, to: str, amount: int) -> str:
        """Open-mint a mintable test token (USDC/WETH). WXPL is acquired by wrapping/trading, not mint."""
        if symbol not in ("USDC", "WETH"):
            raise ValueError("{} is not mintable; wrap or trade for it".format(symbol))
        fn = self.tokens[symbol].functions.mint(Web3.to_checksum_address(to), int(amount))
        return self._send_fn(self.relayer, fn)["transactionHash"].hex()

    def find_pool(self, sym_a: str, sym_b: str):
        return self.pools.get(frozenset((sym_a, sym_b)))

    def quote_trade(self, sell: str, buy: str, amount_in: int) -> int:
        """On-chain quote: `buy` units out for `amount_in` of `sell` via the (sell,buy) pool."""
        pool = self.find_pool(sell, buy)
        if pool is None:
            raise RuntimeError("no pool for {}/{}".format(sell, buy))
        return int(pool.functions.quote(self.tokens[sell].address, int(amount_in)).call())

    def spot_price(self, sym: str) -> float:
        """Live spot price as USDC per 1.0 whole `sym` token, from the (sym,USDC) pool.
        USDC is 1.0 by definition. Used by conditional/limit orders to evaluate a threshold."""
        if sym == "USDC":
            return 1.0
        one_whole = 10 ** self.token_decimals(sym)
        usdc_out = self.quote_trade(sym, "USDC", one_whole)
        return usdc_out / (10 ** self.token_decimals("USDC"))

    def trade(self, account, sell: str, buy: str, amount_in: int, min_out: int) -> dict:
        """Approve the (sell,buy) pool then swap exact `amount_in` of `sell` for `buy`. Output is sent
        to `account` itself — this helper never routes output to any other address. TradeGuard owns
        the policy checks; this just executes."""
        pool = self.find_pool(sell, buy)
        if pool is None:
            raise RuntimeError("no pool for {}/{}".format(sell, buy))
        self.approve_token(account, self.tokens[sell], pool.address, int(amount_in))
        fn = pool.functions.swapExactIn(
            self.tokens[sell].address, int(amount_in), int(min_out), account.address
        )
        receipt = self._send_fn(account, fn)
        return {
            "txHash": receipt["transactionHash"].hex(),
            "buyBalance": self.token_balance(buy, account.address),
        }

    # ---- swap venue (USDC -> WETH via MiniAMM) -------------------------------
    def _require_amm(self):
        if self.amm is None:
            raise RuntimeError("no swap venue in this deployment (USDC/WETH/MiniAMM missing)")

    def usdc_balance(self, address: str) -> int:
        self._require_amm()
        return int(self.usdc.functions.balanceOf(Web3.to_checksum_address(address)).call())

    def weth_balance(self, address: str) -> int:
        self._require_amm()
        return int(self.weth.functions.balanceOf(Web3.to_checksum_address(address)).call())

    def mint_usdc(self, to: str, amount: int) -> str:
        """Mint test USDC (6dp). Open mint, like the other local test tokens."""
        self._require_amm()
        fn = self.usdc.functions.mint(Web3.to_checksum_address(to), int(amount))
        return self._send_fn(self.relayer, fn)["transactionHash"].hex()

    def quote_usdc_to_weth(self, amount_in: int) -> int:
        """On-chain quote: WETH out for `amount_in` USDC (6dp in, 18dp out)."""
        self._require_amm()
        return int(self.amm.functions.quote(self.usdc.address, int(amount_in)).call())

    def swap_usdc_for_weth(self, account, amount_in: int, min_out: int) -> dict:
        """Approve the AMM then swap exact `amount_in` USDC for WETH, output sent to `account`.
        The recipient is always the swapper itself — this helper never sends output elsewhere.
        Returns {txHash, amountOut}. Caller (SwapGuard) is responsible for the policy checks."""
        self._require_amm()
        self.approve_token(account, self.usdc, self.amm.address, int(amount_in))
        fn = self.amm.functions.swapExactIn(
            self.usdc.address, int(amount_in), int(min_out), account.address
        )
        receipt = self._send_fn(account, fn)
        out = int(self.weth.functions.balanceOf(account.address).call())  # post-balance helper for tests
        return {"txHash": receipt["transactionHash"].hex(), "wethBalance": out}

    def approve_token(self, account, token_contract, spender: str, amount: int) -> str:
        fn = token_contract.functions.approve(Web3.to_checksum_address(spender), int(amount))
        return self._send_fn(account, fn)["transactionHash"].hex()

    def transfer_usdt(self, account, to: str, amount: int) -> str:
        """Direct ERC-20 transfer (used by auto-refuel: owner tops up an agent)."""
        receipt = self._send_fn(
            account, self.usdt.functions.transfer(Web3.to_checksum_address(to), int(amount))
        )
        return receipt["transactionHash"].hex()

    def create_job(self, account, provider: str, desc_hash: bytes, expires_at: int) -> int:
        fn = self.commerce.functions.createJob(
            Web3.to_checksum_address(provider), desc_hash, int(expires_at)
        )
        receipt = self._send_fn(account, fn)
        logs = self.commerce.events.JobCreated().process_receipt(receipt, errors=DISCARD)
        return int(logs[0]["args"]["jobId"])

    def fund_job(self, account, job_id: int, amount: int, approve: bool = True) -> str:
        """Approve (optional) then escrow `amount` for the job. Account = the buyer/client."""
        if approve:
            self.approve_usdt(account, self.commerce.address, amount)
        receipt = self._send_fn(account, self.commerce.functions.fund(int(job_id), int(amount)))
        return receipt["transactionHash"].hex()

    def submit_result(self, account, job_id: int, result_hash: bytes, uri: str) -> str:
        fn = self.commerce.functions.submit(int(job_id), result_hash, uri)
        receipt = self._send_fn(account, fn)
        return receipt["transactionHash"].hex()

    def settle(self, account, job_id: int) -> str:
        receipt = self._send_fn(account, self.commerce.functions.settle(int(job_id)))
        return receipt["transactionHash"].hex()

    def reject(self, account, job_id: int) -> str:
        receipt = self._send_fn(account, self.commerce.functions.reject(int(job_id)))
        return receipt["transactionHash"].hex()

    def claim_refund(self, account, job_id: int) -> str:
        receipt = self._send_fn(account, self.commerce.functions.claimRefund(int(job_id)))
        return receipt["transactionHash"].hex()

    def get_job(self, job_id: int) -> dict:
        c, p, budget, desc, result, uri, expires, submitted, status = self.commerce.functions.jobs(
            int(job_id)
        ).call()
        return {
            "jobId": int(job_id),
            "client": c,
            "provider": p,
            "budget": int(budget),
            "descHash": desc,
            "resultHash": result,
            "uri": uri,
            "expiresAt": int(expires),
            "submittedAt": int(submitted),
            "status": JOB_STATUS[status],
        }

    def job_count(self) -> int:
        return int(self.commerce.functions.jobCount().call())

    def poll_funded_jobs(self, provider: str) -> list:
        """Return FUNDED jobs assigned to `provider` (simple full scan; fine for the local MVP)."""
        provider = Web3.to_checksum_address(provider)
        out = []
        for job_id in range(1, self.job_count() + 1):
            job = self.get_job(job_id)
            if job["status"] == "FUNDED" and Web3.to_checksum_address(job["provider"]) == provider:
                out.append(job)
        return out

    def settleable_jobs(self) -> list:
        """SUBMITTED jobs whose dispute window has elapsed (for the settle keeper)."""
        now = self.w3.eth.get_block("latest")["timestamp"]
        out = []
        for job_id in range(1, self.job_count() + 1):
            job = self.get_job(job_id)
            if job["status"] == "SUBMITTED" and now >= job["submittedAt"] + self.dispute_window:
                out.append(job)
        return out
