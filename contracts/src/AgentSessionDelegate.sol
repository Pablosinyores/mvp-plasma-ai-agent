// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

interface IMiniAMM {
    function token0() external view returns (address);
    function token1() external view returns (address);
    function quote(address tokenIn, uint256 amountIn) external view returns (uint256);
    function swapExactIn(address tokenIn, uint256 amountIn, uint256 minOut, address to)
        external
        returns (uint256);
}

/// @title AgentSessionDelegate
/// @notice EIP-7702 delegation target. The user EOA delegates its code to this implementation; from
///         then on the *user's own address* runs this logic. The user installs a scoped, revocable
///         SESSION KEY (a keypair that custodies nothing) and a money-bound POLICY. An off-chain agent
///         that holds only the session key can then sign trade intents; a keeper relays them. Every
///         money bound — funding token, per-trade cap, session cap, buy-token allow-list, pool
///         allow-list, recipient (pinned to the user), and slippage floor — is enforced HERE, on-chain.
///         A fully compromised session key or keeper cannot widen the allow-list, raise the caps,
///         redirect output (no recipient field exists in the intent — it is always the user), or beat
///         the on-chain slippage floor.
///
/// @dev    Because of 7702, all storage lives at the USER EOA's address and `address(this)` IS the user
///         EOA at call time. The constructor runs at the implementation address (the wrong context), so
///         the EIP-712 domain separator is computed dynamically from `address(this)` on every call —
///         never cached. `installSession`/`revokeSession` require `msg.sender == address(this)`, i.e.
///         only a transaction the user EOA sends to itself can grant or revoke a session.
contract AgentSessionDelegate {
    struct Policy {
        bool active;
        uint48 expiry; // unix seconds; session unusable at/after this time
        address fundingToken; // the one token a session may spend (caps are denominated in it)
        uint128 maxInPerTrade; // max fundingToken in per single trade
        uint128 sessionInCap; // max cumulative fundingToken in over the session's life
        uint128 spentIn; // running total of fundingToken spent
        uint16 maxSlippageBps; // floor: minOut = quote * (10000 - bps) / 10000
    }

    /// @dev Signed by the session key. `minOut` is intentionally absent — it is computed on-chain from
    ///      the live quote and the policy's slippage floor so the agent/keeper cannot weaken it.
    struct TradeIntent {
        address pool;
        address sell;
        address buy;
        uint256 amountIn;
        uint256 nonce;
        uint48 deadline;
    }

    // per-session state (keyed by session key; lives at the user EOA under 7702)
    mapping(address => Policy) public policies;
    mapping(address => mapping(address => bool)) public allowedBuy;
    mapping(address => mapping(address => bool)) public allowedPool;
    mapping(address => uint256) public sessionNonce;

    bytes32 private constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)");
    bytes32 private constant TRADE_INTENT_TYPEHASH = keccak256(
        "TradeIntent(address pool,address sell,address buy,uint256 amountIn,uint256 nonce,uint48 deadline)"
    );

    event SessionInstalled(
        address indexed key, address fundingToken, uint48 expiry, uint128 maxInPerTrade, uint128 sessionInCap
    );
    event SessionRevoked(address indexed key);
    event TradeExecuted(
        address indexed key,
        address indexed pool,
        address sell,
        address buy,
        uint256 amountIn,
        uint256 amountOut,
        uint128 spentIn
    );

    modifier onlySelf() {
        require(msg.sender == address(this), "only self");
        _;
    }

    /// @notice The delegated EOA is still the user's wallet — it must keep accepting plain ETH (gas
    ///         top-ups, refunds). Without this, every value transfer to the user would revert once they
    ///         delegate. Pure receive: no state, no call-out.
    receive() external payable {}

    /// @notice Grant a session key a money-bound policy plus buy/pool allow-lists. Owner-only: the user
    ///         EOA must send this tx to itself (msg.sender == address(this)).
    function installSession(
        address key,
        Policy calldata p,
        address[] calldata buys,
        address[] calldata pools
    ) external onlySelf {
        require(key != address(0), "key zero");
        require(p.fundingToken != address(0), "funding zero");
        require(p.expiry > block.timestamp, "already expired");
        require(p.maxInPerTrade > 0 && p.sessionInCap >= p.maxInPerTrade, "bad caps");
        require(p.maxSlippageBps <= 10000, "bad slippage");

        Policy storage pol = policies[key];
        pol.active = true;
        pol.expiry = p.expiry;
        pol.fundingToken = p.fundingToken;
        pol.maxInPerTrade = p.maxInPerTrade;
        pol.sessionInCap = p.sessionInCap;
        pol.spentIn = 0;
        pol.maxSlippageBps = p.maxSlippageBps;

        for (uint256 i; i < buys.length; ++i) {
            allowedBuy[key][buys[i]] = true;
        }
        for (uint256 i; i < pools.length; ++i) {
            allowedPool[key][pools[i]] = true;
        }

        emit SessionInstalled(key, p.fundingToken, p.expiry, p.maxInPerTrade, p.sessionInCap);
    }

    /// @notice Revoke a session immediately. Owner-only. The agent can no longer trade with that key.
    function revokeSession(address key) external onlySelf {
        policies[key].active = false;
        emit SessionRevoked(key);
    }

    /// @notice Relay a session-key-signed trade. Callable by anyone (a keeper) — authority comes from
    ///         the signature + the on-chain policy, not the caller. Output is pinned to the user EOA.
    function executeTrade(TradeIntent calldata intent, bytes calldata sig)
        external
        returns (uint256 amountOut)
    {
        address key = _recover(intent, sig);
        require(key != address(0), "bad sig");

        Policy storage pol = policies[key];
        require(pol.active, "inactive");
        require(block.timestamp < pol.expiry, "expired");
        require(block.timestamp <= intent.deadline, "intent expired");
        require(intent.nonce == sessionNonce[key], "bad nonce");
        sessionNonce[key] = intent.nonce + 1;

        require(intent.sell == pol.fundingToken, "sell!=funding");
        require(allowedBuy[key][intent.buy], "buy not allowed");
        require(allowedPool[key][intent.pool], "pool not allowed");

        // the pool's pair must be exactly {sell, buy} (no smuggling a hostile pool that happens to be
        // allow-listed for a different pair)
        address t0 = IMiniAMM(intent.pool).token0();
        address t1 = IMiniAMM(intent.pool).token1();
        require(
            (t0 == intent.sell && t1 == intent.buy) || (t0 == intent.buy && t1 == intent.sell),
            "pool pair mismatch"
        );

        require(intent.amountIn <= pol.maxInPerTrade, "over per-trade cap");
        require(uint256(pol.spentIn) + intent.amountIn <= pol.sessionInCap, "over session cap");

        // slippage floor computed on-chain from the live quote — client cannot supply or weaken minOut
        uint256 quoted = IMiniAMM(intent.pool).quote(intent.sell, intent.amountIn);
        uint256 minOut = (quoted * (10000 - pol.maxSlippageBps)) / 10000;

        IERC20(intent.sell).approve(intent.pool, intent.amountIn);
        // recipient PINNED to the user EOA (address(this)) — there is no way for the agent to redirect
        amountOut =
            IMiniAMM(intent.pool).swapExactIn(intent.sell, intent.amountIn, minOut, address(this));

        pol.spentIn += uint128(intent.amountIn);
        emit TradeExecuted(key, intent.pool, intent.sell, intent.buy, intent.amountIn, amountOut, pol.spentIn);
    }

    /// @notice The EIP-712 domain separator, bound to this EOA + chain so signatures can't replay across
    ///         users or chains. Computed dynamically (never cached) because under 7702 the constructor
    ///         ran in the wrong context.
    function domainSeparator() public view returns (bytes32) {
        return keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                keccak256(bytes("AgentSessionDelegate")),
                keccak256(bytes("1")),
                block.chainid,
                address(this)
            )
        );
    }

    function _recover(TradeIntent calldata intent, bytes calldata sig)
        internal
        view
        returns (address)
    {
        bytes32 structHash = keccak256(
            abi.encode(
                TRADE_INTENT_TYPEHASH,
                intent.pool,
                intent.sell,
                intent.buy,
                intent.amountIn,
                intent.nonce,
                intent.deadline
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", domainSeparator(), structHash));

        if (sig.length != 65) return address(0);
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        if (v < 27) v += 27;
        // reject high-s (EIP-2 malleability) so a relayer can't mint a second valid sig per intent
        if (uint256(s) > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) {
            return address(0);
        }
        return ecrecover(digest, v, r, s);
    }
}
