// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test, Vm} from "forge-std/Test.sol";
import {MockUSDT} from "../src/MockUSDT.sol";
import {Eip1271Account} from "../src/Eip1271Account.sol";

/// @notice Proves the gasless single-token path the HLD flags as must-test: an EOA delegated via
///         EIP-7702 to an EIP-1271 smart account authorizes an EIP-3009 transfer with a SESSION key
///         (deliberately distinct from the EOA key), and MockUSDT accepts it through
///         isValidSignature — exercising the ecrecover-first / EIP-1271-fallback branch of
///         _verifyAuth. Uses foundry's 7702 cheatcodes (vm.signDelegation + vm.attachDelegation).
contract Eip7702Eip1271Test is Test {
    MockUSDT usdt;
    address bob = address(0xB0B);

    uint256 eoaPk = 0xE0A; // the user EOA that delegates its code (holds no USDT key authority by itself)
    uint256 sessionPk = 0x5E5510; // the session/owner key that actually signs the payment
    address eoa;
    address session;

    function setUp() public {
        usdt = new MockUSDT();
        eoa = vm.addr(eoaPk);
        session = vm.addr(sessionPk);
        vm.warp(1000);
    }

    function _signTransfer(
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

    /// EOA -> EIP-1271 account (7702), session key signs the EIP-3009 auth, transfer settles.
    function test_7702_delegated_eoa_pays_via_eip1271() public {
        // 1) the EIP-1271 account whose owner is the SESSION key (baked into its immutable bytecode)
        Eip1271Account impl = new Eip1271Account(session);

        // 2) EIP-7702: sign + attach the authorization delegating the EOA's code to that account
        Vm.SignedDelegation memory d = vm.signDelegation(address(impl), eoaPk);
        vm.attachDelegation(d);
        assertGt(eoa.code.length, 0, "EOA must carry 7702 delegated code");

        // 3) fund the now-smart EOA; the SESSION key (not the EOA key) authorizes the transfer
        usdt.mint(eoa, 5_000_000);
        bytes32 nonce = keccak256("7702-1271");
        (uint8 v, bytes32 r, bytes32 s) =
            _signTransfer(eoa, bob, 2_000_000, 0, block.timestamp + 300, nonce, sessionPk);

        // ecrecover -> session != from(eoa), so MockUSDT must fall back to isValidSignature on the EOA
        usdt.transferWithAuthorization(eoa, bob, 2_000_000, 0, block.timestamp + 300, nonce, v, r, s);

        assertEq(usdt.balanceOf(bob), 2_000_000, "payee funded via 1271 path");
        assertEq(usdt.balanceOf(eoa), 3_000_000);
        assertTrue(usdt.authorizationState(eoa, nonce));
    }

    /// A key that is neither the EOA nor its configured session owner is still rejected.
    function test_7702_rejects_unauthorized_key() public {
        Eip1271Account impl = new Eip1271Account(session);
        Vm.SignedDelegation memory d = vm.signDelegation(address(impl), eoaPk);
        vm.attachDelegation(d);
        usdt.mint(eoa, 5_000_000);

        uint256 attackerPk = 0xBADBADBAD;
        bytes32 nonce = keccak256("bad");
        (uint8 v, bytes32 r, bytes32 s) =
            _signTransfer(eoa, bob, 1_000_000, 0, block.timestamp + 300, nonce, attackerPk);
        vm.expectRevert("invalid signature");
        usdt.transferWithAuthorization(eoa, bob, 1_000_000, 0, block.timestamp + 300, nonce, v, r, s);
    }
}
