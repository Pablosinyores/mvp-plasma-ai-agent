// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title IdentityRegistry (ERC-8004-lite)
/// @notice A minimal, self-contained ERC-721 identity registry for AI agents.
///         An agent calls `register(cardURI)` from its own address; the contract mints a
///         non-transferable-by-default identity NFT (agentId) owned by the caller and stores the
///         off-chain Agent Card URI. `cardURI(agentId)` resolves it back.
///
///         Self-contained (no external deps) so the local MVP compiles and tests with zero install.
contract IdentityRegistry {
    string public constant name = "Agent Identity";
    string public constant symbol = "AGENT";

    uint256 private _nextId = 1;

    mapping(uint256 => address) public ownerOf;       // agentId => owner
    mapping(address => uint256) public balanceOf;     // owner => count
    mapping(uint256 => string) private _cardURI;      // agentId => Agent Card URI
    mapping(address => uint256) public agentIdOf;     // owner => agentId (one identity per address in MVP)

    event Registered(uint256 indexed agentId, address indexed owner, string cardURI);
    event Transfer(address indexed from, address indexed to, uint256 indexed agentId);
    event CardUpdated(uint256 indexed agentId, string cardURI);

    /// @notice Mint a fresh identity to the caller and record its Agent Card URI.
    /// @param card pointer to the off-chain Agent Card (e.g. s3://agent-cards/<hash>)
    /// @return agentId the freshly minted identity id
    function register(string calldata card) external returns (uint256 agentId) {
        require(agentIdOf[msg.sender] == 0, "already registered");
        agentId = _nextId++;
        ownerOf[agentId] = msg.sender;
        balanceOf[msg.sender] += 1;
        agentIdOf[msg.sender] = agentId;
        _cardURI[agentId] = card;
        emit Transfer(address(0), msg.sender, agentId);
        emit Registered(agentId, msg.sender, card);
    }

    /// @notice Update the Agent Card URI for an identity you own.
    function setCardURI(uint256 agentId, string calldata card) external {
        require(ownerOf[agentId] == msg.sender, "not owner");
        _cardURI[agentId] = card;
        emit CardUpdated(agentId, card);
    }

    /// @notice Resolve an identity to its Agent Card URI.
    function cardURI(uint256 agentId) external view returns (string memory) {
        require(ownerOf[agentId] != address(0), "no such agent");
        return _cardURI[agentId];
    }

    /// @notice ERC-721-style metadata URI alias.
    function tokenURI(uint256 agentId) external view returns (string memory) {
        require(ownerOf[agentId] != address(0), "no such agent");
        return _cardURI[agentId];
    }

    /// @notice Total identities minted so far.
    function totalAgents() external view returns (uint256) {
        return _nextId - 1;
    }

    /// @notice ERC-165. Advertises ERC-165 itself and the ERC-721 *Metadata* extension
    ///         (name/symbol/tokenURI), which this registry implements. The full ERC-721
    ///         transfer/approval interface (0x80ac58cd) is intentionally NOT advertised:
    ///         agent identities are soulbound (non-transferable) in the MVP.
    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == 0x01ffc9a7 // ERC-165
            || interfaceId == 0x5b5e139f; // ERC-721 Metadata (name, symbol, tokenURI)
    }
}
