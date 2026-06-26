// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";

contract IdentityReputationTest is Test {
    IdentityRegistry identity;
    address evaluator = address(0xE7A1);
    address agentOwner = address(0xA9E);
    uint256 walletPk = 0xB0B;
    address wallet;

    function setUp() public {
        identity = new IdentityRegistry();
        identity.setEvaluator(evaluator);
        wallet = vm.addr(walletPk);
        vm.prank(agentOwner);
        identity.register("s3://card");
    }

    function test_recordFeedback_onlyEvaluator() public {
        uint256 id = identity.agentIdOf(agentOwner);
        vm.prank(address(0xBAD));
        vm.expectRevert("not evaluator");
        identity.recordFeedback(id, 1);
    }

    function test_recordFeedback_positiveAndNegative() public {
        uint256 id = identity.agentIdOf(agentOwner);
        vm.prank(evaluator);
        identity.recordFeedback(id, 5);
        vm.prank(evaluator);
        identity.recordFeedback(id, -8);
        assertEq(identity.reputationOf(id), int256(-3), "reputation can go negative");
    }

    function test_setEvaluator_onlyOwner() public {
        vm.prank(address(0xBAD));
        vm.expectRevert("not owner");
        identity.setEvaluator(address(0x1));
    }

    function test_setAgentWallet_withConsentSig() public {
        uint256 id = identity.agentIdOf(agentOwner);
        bytes32 inner = keccak256(abi.encode(id, wallet, block.chainid, address(identity)));
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", inner));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(walletPk, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        vm.prank(agentOwner);
        identity.setAgentWallet(id, wallet, sig);
        assertEq(identity.agentWallet(id), wallet);
    }

    function test_setAgentWallet_badConsent_reverts() public {
        uint256 id = identity.agentIdOf(agentOwner);
        bytes32 inner = keccak256(abi.encode(id, wallet, block.chainid, address(identity)));
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", inner));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(uint256(0xDEAD), digest); // wrong signer
        bytes memory sig = abi.encodePacked(r, s, v);

        vm.prank(agentOwner);
        vm.expectRevert("bad wallet consent");
        identity.setAgentWallet(id, wallet, sig);
    }

    function test_setAgentWallet_onlyAgentOwner() public {
        uint256 id = identity.agentIdOf(agentOwner);
        bytes memory sig = new bytes(65);
        vm.prank(address(0xBAD));
        vm.expectRevert("not owner");
        identity.setAgentWallet(id, wallet, sig);
    }

    function test_globalId_parts() public view {
        uint256 id = identity.agentIdOf(agentOwner);
        (string memory ns, uint256 chainId, address reg, uint256 tokenId) = identity.globalId(id);
        assertEq(ns, "eip155");
        assertEq(chainId, block.chainid);
        assertEq(reg, address(identity));
        assertEq(tokenId, id);
    }
}
