// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {WXPL} from "../src/WXPL.sol";

contract WXPLTest is Test {
    WXPL wxpl;
    address user = address(0xBEEF);

    function setUp() public {
        wxpl = new WXPL();
        vm.deal(user, 100 ether);
    }

    function test_deposit_mints_1to1() public {
        vm.prank(user);
        wxpl.deposit{value: 10 ether}();
        assertEq(wxpl.balanceOf(user), 10 ether);
        assertEq(wxpl.totalSupply(), 10 ether);
        assertEq(address(wxpl).balance, 10 ether);
    }

    function test_receive_wraps() public {
        vm.prank(user);
        (bool ok,) = address(wxpl).call{value: 5 ether}("");
        assertTrue(ok);
        assertEq(wxpl.balanceOf(user), 5 ether);
    }

    function test_withdraw_returns_native() public {
        vm.startPrank(user);
        wxpl.deposit{value: 10 ether}();
        uint256 nativeBefore = user.balance;
        wxpl.withdraw(4 ether);
        vm.stopPrank();
        assertEq(wxpl.balanceOf(user), 6 ether);
        assertEq(user.balance, nativeBefore + 4 ether);
    }

    function test_withdraw_over_balance_reverts() public {
        vm.prank(user);
        wxpl.deposit{value: 1 ether}();
        vm.prank(user);
        vm.expectRevert("insufficient balance");
        wxpl.withdraw(2 ether);
    }
}
