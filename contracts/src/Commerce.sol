// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title Commerce (ERC-8183-lite escrow)
/// @notice Minimal escrowed job marketplace with optimistic, permissionless settlement.
///         A client creates + funds a job for a provider; the provider submits a result; after a
///         dispute window of silence the job settles permissionlessly and the escrow is released to
///         the provider. The client can reject within the window, or reclaim funds after expiry if
///         the provider never delivered.
///
///         Self-contained (only an IERC20 interface) so the local MVP compiles with no external deps.
contract Commerce {
    enum Status {
        NONE,
        OPEN,
        FUNDED,
        SUBMITTED,
        COMPLETED,
        REJECTED,
        EXPIRED
    }

    struct Job {
        address client;
        address provider;
        uint256 budget;
        bytes32 descHash;
        bytes32 resultHash;
        string uri;
        uint64 expiresAt; // deadline for the provider to submit
        uint64 submittedAt; // when the result was submitted (starts the dispute window)
        Status status;
    }

    /// @notice settlement asset, fixed for the life of the contract (matches design §17)
    address public immutable paymentToken;
    /// @notice seconds of silence after submit before anyone can settle
    uint64 public immutable disputeWindow;

    uint256 public jobCount;
    mapping(uint256 => Job) public jobs;

    event JobCreated(uint256 indexed jobId, address indexed client, address indexed provider, bytes32 descHash, uint64 expiresAt);
    event JobFunded(uint256 indexed jobId, uint256 budget);
    event JobSubmitted(uint256 indexed jobId, bytes32 resultHash, string uri);
    event JobSettled(uint256 indexed jobId, address indexed provider, uint256 amount);
    event JobRejected(uint256 indexed jobId, uint256 refund);
    event JobRefunded(uint256 indexed jobId, uint256 refund);

    constructor(address _paymentToken, uint64 _disputeWindow) {
        require(_paymentToken != address(0), "token zero");
        paymentToken = _paymentToken;
        disputeWindow = _disputeWindow;
    }

    function createJob(address provider, bytes32 descHash, uint64 expiresAt)
        external
        returns (uint256 jobId)
    {
        require(provider != address(0), "provider zero");
        require(expiresAt > block.timestamp, "expiry in past");
        jobId = ++jobCount;
        Job storage j = jobs[jobId];
        j.client = msg.sender;
        j.provider = provider;
        j.descHash = descHash;
        j.expiresAt = expiresAt;
        j.status = Status.OPEN;
        emit JobCreated(jobId, msg.sender, provider, descHash, expiresAt);
    }

    /// @notice Client escrows `amount` of the payment token for the job. Requires prior approval.
    function fund(uint256 jobId, uint256 amount) external {
        Job storage j = jobs[jobId];
        require(j.status == Status.OPEN, "not open");
        require(msg.sender == j.client, "not client");
        require(amount > 0, "zero budget");
        j.budget = amount;
        j.status = Status.FUNDED;
        require(IERC20(paymentToken).transferFrom(msg.sender, address(this), amount), "escrow failed");
        emit JobFunded(jobId, amount);
    }

    /// @notice Provider submits the result hash + off-chain URI before the deadline.
    function submit(uint256 jobId, bytes32 resultHash, string calldata uri) external {
        Job storage j = jobs[jobId];
        require(j.status == Status.FUNDED, "not funded");
        require(msg.sender == j.provider, "not provider");
        require(block.timestamp <= j.expiresAt, "expired");
        j.resultHash = resultHash;
        j.uri = uri;
        j.submittedAt = uint64(block.timestamp);
        j.status = Status.SUBMITTED;
        emit JobSubmitted(jobId, resultHash, uri);
    }

    /// @notice Permissionless: after the dispute window of silence, release escrow to the provider.
    function settle(uint256 jobId) external {
        Job storage j = jobs[jobId];
        require(j.status == Status.SUBMITTED, "not submitted");
        require(block.timestamp >= uint256(j.submittedAt) + disputeWindow, "in dispute window");
        j.status = Status.COMPLETED;
        uint256 amount = j.budget;
        require(IERC20(paymentToken).transfer(j.provider, amount), "payout failed");
        emit JobSettled(jobId, j.provider, amount);
    }

    /// @notice Client rejects submitted work within the dispute window; escrow is refunded.
    function reject(uint256 jobId) external {
        Job storage j = jobs[jobId];
        require(j.status == Status.SUBMITTED, "not submitted");
        require(msg.sender == j.client, "not client");
        require(block.timestamp < uint256(j.submittedAt) + disputeWindow, "window passed");
        j.status = Status.REJECTED;
        uint256 amount = j.budget;
        require(IERC20(paymentToken).transfer(j.client, amount), "refund failed");
        emit JobRejected(jobId, amount);
    }

    /// @notice Non-pausable escape hatch: if the provider never submitted by the deadline, the
    ///         client reclaims the escrow. Cannot be blocked by anyone.
    function claimRefund(uint256 jobId) external {
        Job storage j = jobs[jobId];
        require(j.status == Status.FUNDED, "not refundable");
        require(msg.sender == j.client, "not client");
        require(block.timestamp > j.expiresAt, "not expired");
        j.status = Status.EXPIRED;
        uint256 amount = j.budget;
        require(IERC20(paymentToken).transfer(j.client, amount), "refund failed");
        emit JobRefunded(jobId, amount);
    }

    // --- views ---
    function statusOf(uint256 jobId) external view returns (Status) {
        return jobs[jobId].status;
    }

    function settleableAt(uint256 jobId) external view returns (uint256) {
        return uint256(jobs[jobId].submittedAt) + disputeWindow;
    }
}
