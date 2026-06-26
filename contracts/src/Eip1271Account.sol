// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title Eip1271Account
/// @notice Minimal EIP-1271 smart account used as the EIP-7702 delegation target for gas sponsorship.
///         An EOA delegates its code to this implementation with a type-4 SetCode tx; thereafter a
///         relayer / ERC-4337 paymaster can submit the user's EIP-3009 payment and the token's
///         isValidSignature() check validates it — so the user spends a single token (USDT) and
///         never has to hold native gas (the gasless single-token UX of issue #8).
///
///         A signature over `hash` is accepted when its ECDSA signer is either:
///           - `owner` — a session / owner key fixed at deploy time. It is an immutable, so it is
///             baked into this implementation's runtime bytecode and resolves correctly even when the
///             code executes in a delegating EOA's 7702 context; or
///           - address(this) — the account's own key. In a 7702 context address(this) is the
///             delegating EOA, so the EOA can always authorize on its own behalf.
contract Eip1271Account {
    // bytes4(keccak256("isValidSignature(bytes32,bytes)"))
    bytes4 internal constant MAGIC = 0x1626ba7e;
    bytes4 internal constant INVALID = 0xffffffff;

    address public immutable owner;

    constructor(address owner_) {
        owner = owner_;
    }

    /// @notice EIP-1271 validation. Returns MAGIC for a 65-byte ECDSA signature by `owner` or by
    ///         this account's own key (address(this)); INVALID otherwise.
    function isValidSignature(bytes32 hash, bytes calldata signature)
        external
        view
        returns (bytes4)
    {
        if (signature.length != 65) {
            return INVALID;
        }
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        address signer = ecrecover(hash, v, r, s);
        if (signer != address(0) && (signer == owner || signer == address(this))) {
            return MAGIC;
        }
        return INVALID;
    }
}
