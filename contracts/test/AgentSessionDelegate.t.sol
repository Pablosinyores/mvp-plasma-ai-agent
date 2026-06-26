// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {AgentSessionDelegate} from "../src/AgentSessionDelegate.sol";
import {MiniERC20} from "../src/MiniERC20.sol";
import {MiniAMM} from "../src/MiniAMM.sol";

/// @notice Exercises the EIP-7702 "trade from the user's own address" rail. The user EOA delegates to
///         AgentSessionDelegate, installs a scoped session key, and a keeper relays session-signed
///         trades. Asserts the money flows at the USER address and every on-chain cap holds even when
///         the keeper / session key is assumed hostile.
contract AgentSessionDelegateTest is Test {
    AgentSessionDelegate impl;
    MiniERC20 usdc;
    MiniERC20 wxpl;
    MiniAMM pool;

    uint256 userPk = 0xA11CE; // the user EOA that delegates via 7702
    address user;
    uint256 sessionPk = 0x5E5510; // the scoped session key the agent holds
    address sessionKey;
    address keeper = address(0xCAFE);

    uint128 constant MAX_PER_TRADE = 100e6; // 100 USDC
    uint128 constant SESSION_CAP = 250e6; // 250 USDC

    function setUp() public {
        impl = new AgentSessionDelegate();
        usdc = new MiniERC20("USD Coin", "USDC", 6);
        wxpl = new MiniERC20("Wrapped XPL", "WXPL", 18);

        // USDC/WXPL pool @ ~0.10 USDC/WXPL, mirrors Deploy.s.sol seeding ratios
        pool = new MiniAMM(address(usdc), address(wxpl));
        usdc.mint(address(this), 100_000e6);
        wxpl.mint(address(this), 1_000_000 ether);
        usdc.approve(address(pool), 100_000e6);
        wxpl.approve(address(pool), 1_000_000 ether);
        pool.addLiquidity(100_000e6, 1_000_000 ether);

        userPk = 0xA11CE;
        user = vm.addr(userPk);
        sessionKey = vm.addr(sessionPk);

        usdc.mint(user, 1_000e6);

        // delegate the user EOA's code to the implementation (EIP-7702)
        vm.signAndAttachDelegation(address(impl), userPk);
    }

    // ---- helpers ---------------------------------------------------------------

    function _defaultPolicy() internal view returns (AgentSessionDelegate.Policy memory p) {
        p = AgentSessionDelegate.Policy({
            active: true,
            expiry: uint48(block.timestamp + 1 days),
            fundingToken: address(usdc),
            maxInPerTrade: MAX_PER_TRADE,
            sessionInCap: SESSION_CAP,
            spentIn: 0,
            maxSlippageBps: 100 // 1%
        });
    }

    function _install() internal {
        address[] memory buys = new address[](1);
        buys[0] = address(wxpl);
        address[] memory pools = new address[](1);
        pools[0] = address(pool);
        vm.prank(user);
        AgentSessionDelegate(payable(user)).installSession(sessionKey, _defaultPolicy(), buys, pools);
    }

    function _sign(uint256 pk, AgentSessionDelegate.TradeIntent memory intent)
        internal
        view
        returns (bytes memory)
    {
        bytes32 typeHash = keccak256(
            "TradeIntent(address pool,address sell,address buy,uint256 amountIn,uint256 nonce,uint48 deadline)"
        );
        bytes32 structHash = keccak256(
            abi.encode(
                typeHash, intent.pool, intent.sell, intent.buy, intent.amountIn, intent.nonce, intent.deadline
            )
        );
        bytes32 domSep = AgentSessionDelegate(payable(user)).domainSeparator();
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", domSep, structHash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    function _intent(uint256 amountIn, uint256 nonce)
        internal
        view
        returns (AgentSessionDelegate.TradeIntent memory)
    {
        return AgentSessionDelegate.TradeIntent({
            pool: address(pool),
            sell: address(usdc),
            buy: address(wxpl),
            amountIn: amountIn,
            nonce: nonce,
            deadline: uint48(block.timestamp + 1 hours)
        });
    }

    // ---- happy path ------------------------------------------------------------

    function test_happy_trade_executes_from_user_address() public {
        _install();
        uint256 usdcBefore = usdc.balanceOf(user);
        uint256 wxplBefore = wxpl.balanceOf(user);

        AgentSessionDelegate.TradeIntent memory intent = _intent(50e6, 0);
        bytes memory sig = _sign(sessionPk, intent);

        vm.prank(keeper);
        uint256 out = AgentSessionDelegate(payable(user)).executeTrade(intent, sig);

        assertEq(usdc.balanceOf(user), usdcBefore - 50e6, "user USDC debited");
        assertEq(wxpl.balanceOf(user), wxplBefore + out, "user WXPL credited");
        assertGt(out, 0, "got output");

        (,,,,, uint128 spentIn,) = AgentSessionDelegate(payable(user)).policies(sessionKey);
        assertEq(spentIn, 50e6, "spentIn tracks");
        assertEq(AgentSessionDelegate(payable(user)).sessionNonce(sessionKey), 1, "nonce advanced");
    }

    function test_session_cap_accumulates_across_trades() public {
        _install();
        for (uint256 i; i < 2; ++i) {
            AgentSessionDelegate.TradeIntent memory intent = _intent(100e6, i);
            vm.prank(keeper);
            AgentSessionDelegate(payable(user)).executeTrade(intent, _sign(sessionPk, intent));
        }
        (,,,,, uint128 spentIn,) = AgentSessionDelegate(payable(user)).policies(sessionKey);
        assertEq(spentIn, 200e6, "two trades accumulated");
    }

    // ---- revert cases ----------------------------------------------------------

    function test_revert_over_per_trade_cap() public {
        _install();
        AgentSessionDelegate.TradeIntent memory intent = _intent(101e6, 0);
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        vm.expectRevert("over per-trade cap");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_over_session_cap() public {
        _install();
        // two 100e6 trades ok (200), third 100e6 would hit 300 > 250 cap
        for (uint256 i; i < 2; ++i) {
            AgentSessionDelegate.TradeIntent memory ok = _intent(100e6, i);
            vm.prank(keeper);
            AgentSessionDelegate(payable(user)).executeTrade(ok, _sign(sessionPk, ok));
        }
        AgentSessionDelegate.TradeIntent memory bad = _intent(100e6, 2);
        bytes memory sig = _sign(sessionPk, bad);
        vm.prank(keeper);
        vm.expectRevert("over session cap");
        AgentSessionDelegate(payable(user)).executeTrade(bad, sig);
    }

    function test_revert_buy_token_not_allowed() public {
        _install();
        // buy = usdc (not allow-listed as a buy) -> also pool pair mismatch, but allow-list checked first
        AgentSessionDelegate.TradeIntent memory intent = AgentSessionDelegate.TradeIntent({
            pool: address(pool),
            sell: address(usdc),
            buy: address(0xDEAD),
            amountIn: 10e6,
            nonce: 0,
            deadline: uint48(block.timestamp + 1 hours)
        });
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        vm.expectRevert("buy not allowed");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_pool_not_allowed() public {
        _install();
        MiniAMM other = new MiniAMM(address(usdc), address(wxpl));
        AgentSessionDelegate.TradeIntent memory intent = AgentSessionDelegate.TradeIntent({
            pool: address(other),
            sell: address(usdc),
            buy: address(wxpl),
            amountIn: 10e6,
            nonce: 0,
            deadline: uint48(block.timestamp + 1 hours)
        });
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        vm.expectRevert("pool not allowed");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_expired_session() public {
        _install();
        vm.warp(block.timestamp + 2 days); // past expiry
        AgentSessionDelegate.TradeIntent memory intent = _intent(10e6, 0);
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        vm.expectRevert("expired");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_replay_reused_nonce() public {
        _install();
        AgentSessionDelegate.TradeIntent memory intent = _intent(50e6, 0);
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
        vm.prank(keeper);
        vm.expectRevert("bad nonce");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_uninstalled_key() public {
        _install();
        uint256 strangerPk = 0xBADBAD;
        AgentSessionDelegate.TradeIntent memory intent = _intent(10e6, 0);
        bytes memory sig = _sign(strangerPk, intent);
        vm.prank(keeper);
        vm.expectRevert("inactive");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_revoked_session() public {
        _install();
        vm.prank(user);
        AgentSessionDelegate(payable(user)).revokeSession(sessionKey);
        AgentSessionDelegate.TradeIntent memory intent = _intent(10e6, 0);
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        vm.expectRevert("inactive");
        AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
    }

    function test_revert_install_by_non_owner() public {
        address[] memory buys = new address[](1);
        buys[0] = address(wxpl);
        address[] memory pools = new address[](1);
        pools[0] = address(pool);
        vm.prank(keeper); // not the user EOA -> msg.sender != address(this)
        vm.expectRevert("only self");
        AgentSessionDelegate(payable(user)).installSession(sessionKey, _defaultPolicy(), buys, pools);
    }

    // ---- compromised-keeper guarantees ----------------------------------------

    /// @dev There is no recipient field in TradeIntent — output is hard-pinned to the user EOA. A
    ///      hostile keeper has no lever to redirect funds. We assert output landed at the user and
    ///      nowhere else.
    function test_keeper_cannot_redirect_output() public {
        _install();
        AgentSessionDelegate.TradeIntent memory intent = _intent(50e6, 0);
        bytes memory sig = _sign(sessionPk, intent);
        vm.prank(keeper);
        uint256 out = AgentSessionDelegate(payable(user)).executeTrade(intent, sig);
        assertEq(wxpl.balanceOf(keeper), 0, "keeper got nothing");
        assertEq(wxpl.balanceOf(user), out, "all output to user");
    }
}
