// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title OptimisticPolicy
/// @notice Dispute-resolution policy for the commerce kernel. Optimistic: silence past the dispute
///         window is approval. A quorum of authorized voters can reject a submitted job inside the
///         window. The router queries `verdict()` (passing the job's submit timestamp) and acts on it.
contract OptimisticPolicy {
    enum Verdict {
        PENDING,
        APPROVE,
        REJECT
    }

    address public owner;
    uint64 public disputeWindow; // seconds of silence after submit before approval
    uint256 public quorum; // reject votes required to flip a job to REJECT

    mapping(address => bool) public isVoter;
    mapping(uint256 => uint256) public rejectCount; // jobId => distinct reject votes
    mapping(uint256 => mapping(address => bool)) public voted; // jobId => voter => voted?

    event VoterSet(address indexed voter, bool allowed);
    event QuorumSet(uint256 quorum);
    event DisputeWindowSet(uint64 window);
    event RejectVote(uint256 indexed jobId, address indexed voter, uint256 total);

    constructor(uint64 _disputeWindow, uint256 _quorum) {
        require(_quorum >= 1, "quorum < 1");
        owner = msg.sender;
        disputeWindow = _disputeWindow;
        quorum = _quorum;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    function setVoter(address voter, bool allowed) external onlyOwner {
        isVoter[voter] = allowed;
        emit VoterSet(voter, allowed);
    }

    function setQuorum(uint256 q) external onlyOwner {
        require(q >= 1, "quorum < 1");
        quorum = q;
        emit QuorumSet(q);
    }

    function setDisputeWindow(uint64 w) external onlyOwner {
        disputeWindow = w;
        emit DisputeWindowSet(w);
    }

    /// @notice An authorized voter rejects a submitted job. One vote per voter per job.
    function voteReject(uint256 jobId) external {
        require(isVoter[msg.sender], "not voter");
        require(!voted[jobId][msg.sender], "already voted");
        voted[jobId][msg.sender] = true;
        uint256 total = ++rejectCount[jobId];
        emit RejectVote(jobId, msg.sender, total);
    }

    /// @notice Resolve the verdict for a job given the timestamp it was submitted at.
    ///         REJECT once quorum reject votes are in; APPROVE once the window of silence elapses;
    ///         otherwise PENDING.
    function verdict(uint256 jobId, uint64 submittedAt) external view returns (Verdict) {
        if (submittedAt == 0) return Verdict.PENDING;
        if (rejectCount[jobId] >= quorum) return Verdict.REJECT;
        if (block.timestamp >= uint256(submittedAt) + disputeWindow) return Verdict.APPROVE;
        return Verdict.PENDING;
    }
}
