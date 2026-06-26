// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

interface IReputation {
    function agentIdOf(address owner) external view returns (uint256);
    function recordFeedback(uint256 agentId, int256 delta) external;
}

/// @title AgenticCommerce (ERC-8183 kernel)
/// @notice Escrowed job marketplace. A client creates + funds a job for a provider in a fixed
///         6-decimal settlement token; the provider submits a result hash; settlement is driven
///         by an authorized evaluator router that reads a dispute policy verdict and triggers the
///         payout (provider, minus protocol fee) or refund (client). Expiry refunds are a
///         permissionless escape hatch independent of the router.
///
///         The settlement asset is immutable for the life of the kernel.
contract AgenticCommerce {
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

    address public immutable paymentToken;
    /// @notice protocol fee in basis points, taken from the budget at completion
    uint16 public immutable feeBps;
    address public immutable feeRecipient;

    address public owner;
    address public router; // the only address allowed to release/refund a submitted job
    address public registry; // optional ERC-8004 registry for reputation feedback

    uint256 public jobCount;
    mapping(uint256 => Job) public jobs;

    event JobCreated(
        uint256 indexed jobId, address indexed client, address indexed provider, bytes32 descHash, uint64 expiresAt
    );
    event JobFunded(uint256 indexed jobId, uint256 budget);
    event JobSubmitted(uint256 indexed jobId, bytes32 resultHash, string uri);
    event JobSettled(uint256 indexed jobId, address indexed provider, uint256 payout, uint256 fee);
    event JobRejected(uint256 indexed jobId, uint256 refund);
    event JobRefunded(uint256 indexed jobId, uint256 refund);
    event RouterSet(address indexed router);
    event RegistrySet(address indexed registry);

    constructor(address _paymentToken, uint16 _feeBps, address _feeRecipient) {
        require(_paymentToken != address(0), "token zero");
        require(_feeBps <= 10_000, "fee too high");
        require(_feeRecipient != address(0) || _feeBps == 0, "fee recipient zero");
        paymentToken = _paymentToken;
        feeBps = _feeBps;
        feeRecipient = _feeRecipient;
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    modifier onlyRouter() {
        require(msg.sender == router, "not router");
        _;
    }

    /// @notice Wire the evaluator router. Settable once.
    function setRouter(address r) external onlyOwner {
        require(router == address(0), "router set");
        require(r != address(0), "router zero");
        router = r;
        emit RouterSet(r);
    }

    /// @notice Wire the reputation registry (optional). Settable once.
    function setRegistry(address reg) external onlyOwner {
        require(registry == address(0), "registry set");
        registry = reg;
        emit RegistrySet(reg);
    }

    function createJob(address provider, bytes32 descHash, uint64 expiresAt) external returns (uint256 jobId) {
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

    /// @notice Provider submits the result hash + off-chain URI before the deadline. Starts the
    ///         dispute window tracked by the policy.
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

    /// @notice Router-only: release escrow to the provider (minus protocol fee) and bump reputation.
    function release(uint256 jobId) external onlyRouter {
        Job storage j = jobs[jobId];
        require(j.status == Status.SUBMITTED, "not submitted");
        j.status = Status.COMPLETED;
        uint256 fee = (j.budget * feeBps) / 10_000;
        uint256 payout = j.budget - fee;
        require(IERC20(paymentToken).transfer(j.provider, payout), "payout failed");
        if (fee > 0) {
            require(IERC20(paymentToken).transfer(feeRecipient, fee), "fee failed");
        }
        _bumpReputation(j.provider, 1);
        emit JobSettled(jobId, j.provider, payout, fee);
    }

    /// @notice Router-only: refund escrow to the client (dispute upheld) and ding reputation.
    function refund(uint256 jobId) external onlyRouter {
        Job storage j = jobs[jobId];
        require(j.status == Status.SUBMITTED, "not submitted");
        j.status = Status.REJECTED;
        uint256 amount = j.budget;
        require(IERC20(paymentToken).transfer(j.client, amount), "refund failed");
        _bumpReputation(j.provider, -1);
        emit JobRejected(jobId, amount);
    }

    /// @notice Escape hatch: if the provider never submitted by the deadline, the client reclaims
    ///         escrow. Permissionless trigger, client-only beneficiary.
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

    // --- views consumed by the router/policy ---

    function statusOf(uint256 jobId) external view returns (Status) {
        return jobs[jobId].status;
    }

    /// @notice Compact tuple the router needs to evaluate a job.
    function jobInfo(uint256 jobId)
        external
        view
        returns (Status status, uint64 submittedAt, address provider, address client, uint256 budget)
    {
        Job storage j = jobs[jobId];
        return (j.status, j.submittedAt, j.provider, j.client, j.budget);
    }

    // --- internal ---

    function _bumpReputation(address agent, int256 delta) internal {
        if (registry == address(0)) return;
        uint256 agentId = IReputation(registry).agentIdOf(agent);
        if (agentId == 0) return; // provider has no registered identity; skip silently
        IReputation(registry).recordFeedback(agentId, delta);
    }
}
