// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";

contract IdentityRegistryTest is Test {
    IdentityRegistry reg;
    address agent = address(0xA9E47);
    address other = address(0x0EE7);

    function setUp() public {
        reg = new IdentityRegistry();
    }

    function test_register_mints_to_caller_and_stores_card() public {
        string memory uri = "s3://agent-cards/abc123";
        vm.prank(agent);
        uint256 id = reg.register(uri);

        assertEq(id, 1);
        assertEq(reg.ownerOf(id), agent);
        assertEq(reg.balanceOf(agent), 1);
        assertEq(reg.agentIdOf(agent), id);
        assertEq(reg.cardURI(id), uri);
        assertEq(reg.tokenURI(id), uri);
        assertEq(reg.totalAgents(), 1);
    }

    function test_ids_increment() public {
        vm.prank(agent);
        uint256 a = reg.register("s3://agent-cards/a");
        vm.prank(other);
        uint256 b = reg.register("s3://agent-cards/b");
        assertEq(a, 1);
        assertEq(b, 2);
    }

    function test_double_register_reverts() public {
        vm.startPrank(agent);
        reg.register("s3://agent-cards/a");
        vm.expectRevert("already registered");
        reg.register("s3://agent-cards/a2");
        vm.stopPrank();
    }

    function test_setCardURI_only_owner() public {
        vm.prank(agent);
        uint256 id = reg.register("s3://agent-cards/a");

        vm.prank(other);
        vm.expectRevert("not owner");
        reg.setCardURI(id, "s3://agent-cards/hacked");

        vm.prank(agent);
        reg.setCardURI(id, "s3://agent-cards/updated");
        assertEq(reg.cardURI(id), "s3://agent-cards/updated");
    }

    function test_cardURI_reverts_for_unknown() public {
        vm.expectRevert("no such agent");
        reg.cardURI(999);
    }

    function test_supportsInterface() public view {
        assertTrue(reg.supportsInterface(0x01ffc9a7)); // ERC-165
        assertTrue(reg.supportsInterface(0x5b5e139f)); // ERC-721 Metadata
        assertFalse(reg.supportsInterface(0x80ac58cd)); // full ERC-721 — soulbound, not advertised
        assertFalse(reg.supportsInterface(0xffffffff));
    }
}
