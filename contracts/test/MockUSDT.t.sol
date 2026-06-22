// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockUSDT} from "../src/MockUSDT.sol";

contract MockUSDTTest is Test {
    MockUSDT usdt;
    address alice = address(0xA11CE);
    address bob = address(0xB0B);

    function setUp() public {
        usdt = new MockUSDT();
    }

    function test_decimals_is_six() public view {
        assertEq(usdt.decimals(), 6);
    }

    function test_mint_increases_balance_and_supply() public {
        usdt.mint(alice, 1_000_000); // 1.0 USDT
        assertEq(usdt.balanceOf(alice), 1_000_000);
        assertEq(usdt.totalSupply(), 1_000_000);
    }

    function test_transfer() public {
        usdt.mint(alice, 5_000_000);
        vm.prank(alice);
        usdt.transfer(bob, 2_000_000);
        assertEq(usdt.balanceOf(alice), 3_000_000);
        assertEq(usdt.balanceOf(bob), 2_000_000);
    }

    function test_transferFrom_with_allowance() public {
        usdt.mint(alice, 5_000_000);
        vm.prank(alice);
        usdt.approve(address(this), 2_000_000);
        usdt.transferFrom(alice, bob, 2_000_000);
        assertEq(usdt.balanceOf(bob), 2_000_000);
        assertEq(usdt.allowance(alice, address(this)), 0);
    }

    function test_transfer_reverts_on_insufficient_balance() public {
        vm.prank(alice);
        vm.expectRevert("insufficient balance");
        usdt.transfer(bob, 1);
    }
}
