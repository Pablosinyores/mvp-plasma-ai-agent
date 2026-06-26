// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title IdentityRegistry (ERC-8004)
/// @notice Self-contained ERC-721 identity registry for AI agents with on-chain reputation.
///         An agent calls `register(cardURI)` from its own address; the contract mints a
///         soulbound identity NFT (agentId) owned by the caller and stores the off-chain
///         Agent Card URI. A separate operational `agentWallet` may be bound to an identity
///         via a consent signature from that wallet. Reputation is an aggregate score that
///         only an authorized evaluator (e.g. the commerce settlement path) may mutate.
///
///         The canonical global identifier of an agent is `eip155:{chainId}:{registry}` with
///         `agentId == tokenId`; `globalId()` exposes its parts.
contract IdentityRegistry {
    string public constant name = "Agent Identity";
    string public constant symbol = "AGENT";

    address public owner;
    /// @notice the only address allowed to mutate reputation (set by owner; e.g. the commerce kernel)
    address public evaluator;

    uint256 private _nextId = 1;

    mapping(uint256 => address) public ownerOf; // agentId => owner
    mapping(address => uint256) public balanceOf; // owner => count
    mapping(uint256 => string) private _cardURI; // agentId => Agent Card URI
    mapping(address => uint256) public agentIdOf; // owner => agentId (one identity per address in MVP)
    mapping(uint256 => address) public agentWallet; // agentId => bound operational wallet
    mapping(uint256 => int256) public reputationOf; // agentId => aggregate reputation score

    event Registered(uint256 indexed agentId, address indexed owner, string cardURI);
    event Transfer(address indexed from, address indexed to, uint256 indexed agentId);
    event CardUpdated(uint256 indexed agentId, string cardURI);
    event AgentWalletSet(uint256 indexed agentId, address indexed wallet);
    event ReputationUpdated(uint256 indexed agentId, int256 delta, int256 total);
    event EvaluatorSet(address indexed evaluator);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    /// @notice Authorize the single address allowed to mutate reputation.
    function setEvaluator(address e) external onlyOwner {
        evaluator = e;
        emit EvaluatorSet(e);
    }

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

    /// @notice Bind an operational wallet to an identity. Caller must own the identity and the
    ///         target wallet must consent by signing the binding digest (EIP-191 personal-sign).
    ///         This proves the wallet's controller authorized the binding before discovery/reputation
    ///         flow through it.
    function setAgentWallet(uint256 agentId, address wallet, bytes calldata sig) external {
        require(ownerOf[agentId] == msg.sender, "not owner");
        require(wallet != address(0), "wallet zero");
        bytes32 inner = keccak256(abi.encode(agentId, wallet, block.chainid, address(this)));
        bytes32 digest = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", inner));
        require(_recover(digest, sig) == wallet, "bad wallet consent");
        agentWallet[agentId] = wallet;
        emit AgentWalletSet(agentId, wallet);
    }

    /// @notice Mutate an identity's reputation. Authorized evaluator only. `delta` may be negative.
    function recordFeedback(uint256 agentId, int256 delta) external {
        require(msg.sender == evaluator, "not evaluator");
        require(ownerOf[agentId] != address(0), "no such agent");
        int256 total = reputationOf[agentId] + delta;
        reputationOf[agentId] = total;
        emit ReputationUpdated(agentId, delta, total);
    }

    // --- views ---

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

    /// @notice The parts of the canonical global id `eip155:{chainId}:{registry}` for `agentId`.
    function globalId(uint256 agentId)
        external
        view
        returns (string memory namespace, uint256 chainId, address registry, uint256 tokenId)
    {
        require(ownerOf[agentId] != address(0), "no such agent");
        return ("eip155", block.chainid, address(this), agentId);
    }

    /// @notice Total identities minted so far.
    function totalAgents() external view returns (uint256) {
        return _nextId - 1;
    }

    /// @notice ERC-165. Advertises ERC-165 itself and the ERC-721 *Metadata* extension
    ///         (name/symbol/tokenURI). The full ERC-721 transfer/approval interface
    ///         (0x80ac58cd) is intentionally NOT advertised: agent identities are soulbound.
    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == 0x01ffc9a7 // ERC-165
            || interfaceId == 0x5b5e139f; // ERC-721 Metadata (name, symbol, tokenURI)
    }

    // --- internal ---

    function _recover(bytes32 digest, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "bad sig len");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        require(uint256(s) <= 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0, "bad s");
        require(v == 27 || v == 28, "bad v");
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0), "bad sig");
        return signer;
    }
}
