// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface ICommerce {
    enum Status {
        NONE,
        OPEN,
        FUNDED,
        SUBMITTED,
        COMPLETED,
        REJECTED,
        EXPIRED
    }

    function jobInfo(uint256 jobId)
        external
        view
        returns (Status status, uint64 submittedAt, address provider, address client, uint256 budget);
    function release(uint256 jobId) external;
    function refund(uint256 jobId) external;
}

interface IPolicy {
    enum Verdict {
        PENDING,
        APPROVE,
        REJECT
    }

    function verdict(uint256 jobId, uint64 submittedAt) external view returns (Verdict);
}

/// @title EvaluatorRouter
/// @notice Permissionless settlement trigger. Reads a submitted job's state from the commerce
///         kernel and the dispute verdict from the policy, then drives the kernel to release the
///         escrow to the provider (APPROVE) or refund the client (REJECT). No privileged settler:
///         anyone may call `settle` once the verdict is decided.
contract EvaluatorRouter {
    ICommerce public immutable commerce;
    IPolicy public immutable policy;

    event Settled(uint256 indexed jobId, uint8 verdict);

    constructor(address _commerce, address _policy) {
        require(_commerce != address(0) && _policy != address(0), "zero dep");
        commerce = ICommerce(_commerce);
        policy = IPolicy(_policy);
    }

    function settle(uint256 jobId) external {
        (ICommerce.Status status, uint64 submittedAt,,,) = commerce.jobInfo(jobId);
        require(status == ICommerce.Status.SUBMITTED, "not submitted");
        IPolicy.Verdict v = policy.verdict(jobId, submittedAt);
        if (v == IPolicy.Verdict.APPROVE) {
            commerce.release(jobId);
        } else if (v == IPolicy.Verdict.REJECT) {
            commerce.refund(jobId);
        } else {
            revert("pending");
        }
        emit Settled(jobId, uint8(v));
    }
}
