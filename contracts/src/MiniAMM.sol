// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

/// @title MiniAMM
/// @notice Minimal constant-product (x*y=k) swap pool over two tokens, 0.30% fee. The local venue an
///         agent uses to convert USDC -> WETH. Permissionless `swapExactIn` with an explicit `minOut`
///         (slippage guard) and `to` recipient. The agent-side guardrails (caps, allow-listed venue,
///         recipient pinned to the agent itself) live off-chain in SwapGuard — this contract is just
///         the venue. Not production: no LP tokens, no reentrancy lock needed (tokens are trusted
///         test ERC-20s with no callbacks).
contract MiniAMM {
    address public immutable token0; // USDC (6dp)
    address public immutable token1; // WETH (18dp)
    uint256 public reserve0;
    uint256 public reserve1;
    uint16 public constant FEE_BPS = 30; // 0.30%

    event Swap(address indexed to, address tokenIn, uint256 amountIn, address tokenOut, uint256 amountOut);
    event LiquidityAdded(uint256 amount0, uint256 amount1);

    constructor(address _token0, address _token1) {
        require(_token0 != address(0) && _token1 != address(0), "token zero");
        require(_token0 != _token1, "same token");
        token0 = _token0;
        token1 = _token1;
    }

    /// @notice Seed/extend reserves. Caller must approve both tokens to this pool first.
    function addLiquidity(uint256 amount0, uint256 amount1) external {
        require(IERC20(token0).transferFrom(msg.sender, address(this), amount0), "pull token0");
        require(IERC20(token1).transferFrom(msg.sender, address(this), amount1), "pull token1");
        reserve0 += amount0;
        reserve1 += amount1;
        emit LiquidityAdded(amount0, amount1);
    }

    /// @notice Constant-product output for `amountIn` after the 0.30% fee.
    function getAmountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut)
        public
        pure
        returns (uint256)
    {
        require(amountIn > 0, "amountIn zero");
        require(reserveIn > 0 && reserveOut > 0, "no liquidity");
        uint256 amountInWithFee = amountIn * (10000 - FEE_BPS);
        return (amountInWithFee * reserveOut) / (reserveIn * 10000 + amountInWithFee);
    }

    /// @notice Read-only quote for swapping `amountIn` of `tokenIn`.
    function quote(address tokenIn, uint256 amountIn) external view returns (uint256) {
        require(tokenIn == token0 || tokenIn == token1, "bad tokenIn");
        (uint256 rIn, uint256 rOut) =
            tokenIn == token0 ? (reserve0, reserve1) : (reserve1, reserve0);
        return getAmountOut(amountIn, rIn, rOut);
    }

    /// @notice Swap exactly `amountIn` of `tokenIn` for the other token; reverts if out < `minOut`.
    ///         Pulls `amountIn` from msg.sender (prior approval) and sends the output to `to`.
    function swapExactIn(address tokenIn, uint256 amountIn, uint256 minOut, address to)
        external
        returns (uint256 amountOut)
    {
        require(to != address(0), "to zero");
        require(tokenIn == token0 || tokenIn == token1, "bad tokenIn");
        bool zeroForOne = tokenIn == token0;
        (address tIn, address tOut, uint256 rIn, uint256 rOut) = zeroForOne
            ? (token0, token1, reserve0, reserve1)
            : (token1, token0, reserve1, reserve0);

        amountOut = getAmountOut(amountIn, rIn, rOut);
        require(amountOut >= minOut, "slippage");

        require(IERC20(tIn).transferFrom(msg.sender, address(this), amountIn), "pull in");
        require(IERC20(tOut).transfer(to, amountOut), "send out");

        if (zeroForOne) {
            reserve0 += amountIn;
            reserve1 -= amountOut;
        } else {
            reserve1 += amountIn;
            reserve0 -= amountOut;
        }
        emit Swap(to, tIn, amountIn, tOut, amountOut);
    }
}
