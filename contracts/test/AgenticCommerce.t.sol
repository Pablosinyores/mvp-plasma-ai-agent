// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockUSDT} from "../src/MockUSDT.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";
import {AgenticCommerce} from "../src/AgenticCommerce.sol";
import {OptimisticPolicy} from "../src/OptimisticPolicy.sol";
import {EvaluatorRouter} from "../src/EvaluatorRouter.sol";

contract AgenticCommerceTest is Test {
    MockUSDT usdt;
    IdentityRegistry identity;
    AgenticCommerce commerce;
    OptimisticPolicy policy;
    EvaluatorRouter router;

    address feeRecipient = address(0xFEE);
    address client = address(0xC1);
    address provider = address(0x9D);
    address voter = address(0x70);
    address stranger = address(0xBAD);

    uint16 constant FEE_BPS = 250; // 2.5%
    uint64 constant WINDOW = 60;

    function setUp() public {
        usdt = new MockUSDT();
        identity = new IdentityRegistry();
        commerce = new AgenticCommerce(address(usdt), FEE_BPS, feeRecipient);
        policy = new OptimisticPolicy(WINDOW, 1);
        router = new EvaluatorRouter(address(commerce), address(policy));

        commerce.setRouter(address(router));
        commerce.setRegistry(address(identity));
        identity.setEvaluator(address(commerce));
        policy.setVoter(voter, true);

        usdt.mint(client, 1_000e6);

        // provider registers an identity so reputation can move
        vm.prank(provider);
        identity.register("s3://card/provider");
    }

    function _fundedJob(uint256 budget) internal returns (uint256 jobId) {
        vm.prank(client);
        jobId = commerce.createJob(provider, keccak256("desc"), uint64(block.timestamp + 1 days));
        vm.prank(client);
        usdt.approve(address(commerce), budget);
        vm.prank(client);
        commerce.fund(jobId, budget);
    }

    function _submittedJob(uint256 budget) internal returns (uint256 jobId) {
        jobId = _fundedJob(budget);
        vm.prank(provider);
        commerce.submit(jobId, keccak256("result"), "ipfs://res");
    }

    // --- happy path: OPEN -> COMPLETED, provider paid minus fee ---
    function test_happyPath_paysProviderMinusFee() public {
        uint256 budget = 100e6;
        uint256 jobId = _submittedJob(budget);

        vm.warp(block.timestamp + WINDOW); // window elapses -> APPROVE
        router.settle(jobId);

        uint256 fee = (budget * FEE_BPS) / 10_000;
        assertEq(usdt.balanceOf(provider), budget - fee, "provider payout");
        assertEq(usdt.balanceOf(feeRecipient), fee, "fee");
        assertEq(uint8(commerce.statusOf(jobId)), uint8(AgenticCommerce.Status.COMPLETED));
        uint256 agentId = identity.agentIdOf(provider);
        assertEq(identity.reputationOf(agentId), int256(1), "reputation +1");
    }

    // --- dispute: quorum reject -> REJECTED, client refunded ---
    function test_dispute_quorumReject_refundsClient() public {
        uint256 budget = 100e6;
        uint256 jobId = _submittedJob(budget);

        vm.prank(voter);
        policy.voteReject(jobId);

        router.settle(jobId); // verdict REJECT regardless of window
        assertEq(usdt.balanceOf(client), 1_000e6, "client made whole");
        assertEq(uint8(commerce.statusOf(jobId)), uint8(AgenticCommerce.Status.REJECTED));
        uint256 agentId = identity.agentIdOf(provider);
        assertEq(identity.reputationOf(agentId), int256(-1), "reputation -1");
    }

    // --- expiry: claimRefund -> EXPIRED ---
    function test_expiry_claimRefund() public {
        uint256 budget = 50e6;
        vm.prank(client);
        uint256 jobId = commerce.createJob(provider, keccak256("desc"), uint64(block.timestamp + 100));
        vm.prank(client);
        usdt.approve(address(commerce), budget);
        vm.prank(client);
        commerce.fund(jobId, budget);

        vm.warp(block.timestamp + 101);
        vm.prank(client);
        commerce.claimRefund(jobId);
        assertEq(usdt.balanceOf(client), 1_000e6, "refunded");
        assertEq(uint8(commerce.statusOf(jobId)), uint8(AgenticCommerce.Status.EXPIRED));
    }

    // --- router access control: release/refund only by router ---
    function test_release_onlyRouter() public {
        uint256 jobId = _submittedJob(100e6);
        vm.warp(block.timestamp + WINDOW);
        vm.expectRevert("not router");
        commerce.release(jobId);
        vm.prank(stranger);
        vm.expectRevert("not router");
        commerce.refund(jobId);
    }

    // --- negative paths ---
    function test_createJob_pastExpiry_reverts() public {
        vm.prank(client);
        vm.expectRevert("expiry in past");
        commerce.createJob(provider, keccak256("d"), uint64(block.timestamp));
    }

    function test_fund_zeroBudget_reverts() public {
        vm.prank(client);
        uint256 jobId = commerce.createJob(provider, keccak256("d"), uint64(block.timestamp + 1 days));
        vm.prank(client);
        vm.expectRevert("zero budget");
        commerce.fund(jobId, 0);
    }

    function test_fund_wrongCaller_reverts() public {
        vm.prank(client);
        uint256 jobId = commerce.createJob(provider, keccak256("d"), uint64(block.timestamp + 1 days));
        vm.prank(stranger);
        vm.expectRevert("not client");
        commerce.fund(jobId, 10e6);
    }

    function test_submit_wrongCaller_reverts() public {
        uint256 jobId = _fundedJob(100e6);
        vm.prank(stranger);
        vm.expectRevert("not provider");
        commerce.submit(jobId, keccak256("r"), "uri");
    }

    function test_doubleSubmit_reverts() public {
        uint256 jobId = _submittedJob(100e6);
        vm.prank(provider);
        vm.expectRevert("not funded");
        commerce.submit(jobId, keccak256("r2"), "uri2");
    }

    function test_settle_beforeWindow_pending_reverts() public {
        uint256 jobId = _submittedJob(100e6);
        // no votes, window not elapsed -> PENDING
        vm.expectRevert("pending");
        router.settle(jobId);
    }

    function test_settle_notSubmitted_reverts() public {
        uint256 jobId = _fundedJob(100e6);
        vm.expectRevert("not submitted");
        router.settle(jobId);
    }

    function test_doubleSettle_reverts() public {
        uint256 jobId = _submittedJob(100e6);
        vm.warp(block.timestamp + WINDOW);
        router.settle(jobId);
        vm.expectRevert("not submitted");
        router.settle(jobId);
    }

    function test_voteReject_onlyVoter() public {
        uint256 jobId = _submittedJob(100e6);
        vm.prank(stranger);
        vm.expectRevert("not voter");
        policy.voteReject(jobId);
    }

    function test_voteReject_noDoubleVote() public {
        uint256 jobId = _submittedJob(100e6);
        vm.prank(voter);
        policy.voteReject(jobId);
        vm.prank(voter);
        vm.expectRevert("already voted");
        policy.voteReject(jobId);
    }

    function test_setRouter_onlyOnce() public {
        vm.expectRevert("router set");
        commerce.setRouter(address(0x1234));
    }
}
