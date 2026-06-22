// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {Commerce} from "../src/Commerce.sol";
import {MockUSDT} from "../src/MockUSDT.sol";

contract CommerceTest is Test {
    MockUSDT usdt;
    Commerce commerce;
    address client = address(0xC11E27);
    address provider = address(0x9809D);
    uint64 constant WINDOW = 10; // seconds
    uint256 constant BUDGET = 5_000_000; // 5 USDT

    function setUp() public {
        usdt = new MockUSDT();
        commerce = new Commerce(address(usdt), WINDOW);
        usdt.mint(client, 100_000_000);
        vm.prank(client);
        usdt.approve(address(commerce), type(uint256).max);
    }

    function _funded() internal returns (uint256 jobId) {
        vm.startPrank(client);
        jobId = commerce.createJob(provider, keccak256("desc"), uint64(block.timestamp + 1 hours));
        commerce.fund(jobId, BUDGET);
        vm.stopPrank();
    }

    function test_create_and_fund_pulls_escrow() public {
        uint256 jobId = _funded();
        assertEq(uint8(commerce.statusOf(jobId)), uint8(Commerce.Status.FUNDED));
        assertEq(usdt.balanceOf(address(commerce)), BUDGET);
        assertEq(usdt.balanceOf(client), 100_000_000 - BUDGET);
    }

    function test_full_happy_path_settles_to_provider() public {
        uint256 jobId = _funded();
        vm.prank(provider);
        commerce.submit(jobId, keccak256("result"), "s3://agent-cards/r");
        assertEq(uint8(commerce.statusOf(jobId)), uint8(Commerce.Status.SUBMITTED));

        // cannot settle inside the window
        vm.expectRevert("in dispute window");
        commerce.settle(jobId);

        // after the window, anyone can settle
        vm.warp(block.timestamp + WINDOW + 1);
        commerce.settle(jobId); // permissionless (this test contract calls it)
        assertEq(uint8(commerce.statusOf(jobId)), uint8(Commerce.Status.COMPLETED));
        assertEq(usdt.balanceOf(provider), BUDGET);
        assertEq(usdt.balanceOf(address(commerce)), 0);
    }

    function test_reject_within_window_refunds_client() public {
        uint256 jobId = _funded();
        vm.prank(provider);
        commerce.submit(jobId, keccak256("result"), "uri");
        vm.prank(client);
        commerce.reject(jobId);
        assertEq(uint8(commerce.statusOf(jobId)), uint8(Commerce.Status.REJECTED));
        assertEq(usdt.balanceOf(client), 100_000_000);
        assertEq(usdt.balanceOf(provider), 0);
    }

    function test_claimRefund_after_expiry_when_not_delivered() public {
        vm.startPrank(client);
        uint256 jobId = commerce.createJob(provider, keccak256("d"), uint64(block.timestamp + 100));
        commerce.fund(jobId, BUDGET);
        vm.stopPrank();

        vm.warp(block.timestamp + 101);
        vm.prank(client);
        commerce.claimRefund(jobId);
        assertEq(uint8(commerce.statusOf(jobId)), uint8(Commerce.Status.EXPIRED));
        assertEq(usdt.balanceOf(client), 100_000_000);
    }

    function test_double_settle_reverts() public {
        uint256 jobId = _funded();
        vm.prank(provider);
        commerce.submit(jobId, keccak256("r"), "uri");
        vm.warp(block.timestamp + WINDOW + 1);
        commerce.settle(jobId);
        vm.expectRevert("not submitted");
        commerce.settle(jobId);
    }

    function test_submit_after_expiry_reverts() public {
        vm.startPrank(client);
        uint256 jobId = commerce.createJob(provider, keccak256("d"), uint64(block.timestamp + 10));
        commerce.fund(jobId, BUDGET);
        vm.stopPrank();
        vm.warp(block.timestamp + 11);
        vm.prank(provider);
        vm.expectRevert("expired");
        commerce.submit(jobId, keccak256("r"), "uri");
    }

    function test_only_provider_can_submit() public {
        uint256 jobId = _funded();
        vm.prank(client);
        vm.expectRevert("not provider");
        commerce.submit(jobId, keccak256("r"), "uri");
    }
}
