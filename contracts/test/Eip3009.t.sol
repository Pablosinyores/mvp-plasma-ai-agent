// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MockUSDT} from "../src/MockUSDT.sol";

/// @notice Exercises the EIP-3009 transferWithAuthorization rail that x402 spend settles over.
contract Eip3009Test is Test {
    MockUSDT usdt;
    uint256 alicePk = 0xA11CE;
    address alice;
    address bob = address(0xB0B);

    function setUp() public {
        usdt = new MockUSDT();
        alice = vm.addr(alicePk);
        usdt.mint(alice, 10_000_000); // 10 USDT
        vm.warp(1000); // a non-zero clock so validAfter math is sane
    }

    function _sign(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint256 pk
    ) internal view returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(
            abi.encode(
                usdt.TRANSFER_WITH_AUTHORIZATION_TYPEHASH(),
                from,
                to,
                value,
                validAfter,
                validBefore,
                nonce
            )
        );
        bytes32 digest =
            keccak256(abi.encodePacked("\x19\x01", usdt.DOMAIN_SEPARATOR(), structHash));
        (v, r, s) = vm.sign(pk, digest);
    }

    function test_transferWithAuthorization_moves_funds() public {
        bytes32 nonce = keccak256("n1");
        (uint8 v, bytes32 r, bytes32 s) =
            _sign(alice, bob, 2_000_000, 0, block.timestamp + 300, nonce, alicePk);
        usdt.transferWithAuthorization(alice, bob, 2_000_000, 0, block.timestamp + 300, nonce, v, r, s);
        assertEq(usdt.balanceOf(bob), 2_000_000);
        assertEq(usdt.balanceOf(alice), 8_000_000);
        assertTrue(usdt.authorizationState(alice, nonce));
    }

    function test_replay_same_nonce_reverts() public {
        bytes32 nonce = keccak256("n2");
        (uint8 v, bytes32 r, bytes32 s) =
            _sign(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, alicePk);
        usdt.transferWithAuthorization(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, v, r, s);
        vm.expectRevert("auth already used");
        usdt.transferWithAuthorization(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, v, r, s);
    }

    function test_expired_authorization_reverts() public {
        bytes32 nonce = keccak256("n3");
        uint256 validBefore = block.timestamp + 100;
        (uint8 v, bytes32 r, bytes32 s) =
            _sign(alice, bob, 1_000_000, 0, validBefore, nonce, alicePk);
        vm.warp(block.timestamp + 200); // past validBefore
        vm.expectRevert("auth expired");
        usdt.transferWithAuthorization(alice, bob, 1_000_000, 0, validBefore, nonce, v, r, s);
    }

    function test_wrong_signer_reverts() public {
        bytes32 nonce = keccak256("n4");
        uint256 attackerPk = 0xBADBADBAD;
        (uint8 v, bytes32 r, bytes32 s) =
            _sign(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, attackerPk);
        vm.expectRevert("invalid signature");
        usdt.transferWithAuthorization(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, v, r, s);
    }

    // --- receiveWithAuthorization ------------------------------------------------

    function _signReceive(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint256 pk
    ) internal view returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(
            abi.encode(
                usdt.RECEIVE_WITH_AUTHORIZATION_TYPEHASH(),
                from, to, value, validAfter, validBefore, nonce
            )
        );
        bytes32 digest =
            keccak256(abi.encodePacked("\x19\x01", usdt.DOMAIN_SEPARATOR(), structHash));
        (v, r, s) = vm.sign(pk, digest);
    }

    function test_receiveWithAuthorization_payee_pulls_funds() public {
        bytes32 nonce = keccak256("r1");
        (uint8 v, bytes32 r, bytes32 s) =
            _signReceive(alice, bob, 2_000_000, 0, block.timestamp + 300, nonce, alicePk);
        vm.prank(bob); // payee submits
        usdt.receiveWithAuthorization(alice, bob, 2_000_000, 0, block.timestamp + 300, nonce, v, r, s);
        assertEq(usdt.balanceOf(bob), 2_000_000);
        assertTrue(usdt.authorizationState(alice, nonce));
    }

    function test_receiveWithAuthorization_non_payee_reverts() public {
        bytes32 nonce = keccak256("r2");
        (uint8 v, bytes32 r, bytes32 s) =
            _signReceive(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, alicePk);
        vm.expectRevert("caller must be payee"); // anyone-but-bob (the test contract) submits
        usdt.receiveWithAuthorization(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, v, r, s);
    }

    // --- cancelAuthorization -----------------------------------------------------

    function _signCancel(address authorizer, bytes32 nonce, uint256 pk)
        internal
        view
        returns (uint8 v, bytes32 r, bytes32 s)
    {
        bytes32 structHash =
            keccak256(abi.encode(usdt.CANCEL_AUTHORIZATION_TYPEHASH(), authorizer, nonce));
        bytes32 digest =
            keccak256(abi.encodePacked("\x19\x01", usdt.DOMAIN_SEPARATOR(), structHash));
        (v, r, s) = vm.sign(pk, digest);
    }

    function test_cancelAuthorization_blocks_later_use() public {
        bytes32 nonce = keccak256("c1");
        (uint8 cv, bytes32 cr, bytes32 cs) = _signCancel(alice, nonce, alicePk);
        usdt.cancelAuthorization(alice, nonce, cv, cr, cs);
        assertTrue(usdt.authorizationState(alice, nonce));

        // a valid transfer auth on the same nonce now reverts as already-used
        (uint8 v, bytes32 r, bytes32 s) =
            _sign(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, alicePk);
        vm.expectRevert("auth already used");
        usdt.transferWithAuthorization(alice, bob, 1_000_000, 0, block.timestamp + 300, nonce, v, r, s);
    }

    function test_cancelAuthorization_wrong_signer_reverts() public {
        bytes32 nonce = keccak256("c2");
        (uint8 v, bytes32 r, bytes32 s) = _signCancel(alice, nonce, 0xBADBADBAD);
        vm.expectRevert("invalid signature");
        usdt.cancelAuthorization(alice, nonce, v, r, s);
    }
}
