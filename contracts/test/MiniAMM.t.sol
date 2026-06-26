// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {MiniERC20} from "../src/MiniERC20.sol";
import {MiniAMM} from "../src/MiniAMM.sol";

contract MiniAMMTest is Test {
    MiniERC20 usdc;
    MiniERC20 weth;
    MiniAMM amm;
    address trader = address(0xBEEF);

    function setUp() public {
        usdc = new MiniERC20("USD Coin", "USDC", 6);
        weth = new MiniERC20("Wrapped Ether", "WETH", 18);
        amm = new MiniAMM(address(usdc), address(weth));

        // seed ~2000 USDC per WETH: 2,000,000 USDC / 1000 WETH
        usdc.mint(address(this), 2_000_000e6);
        weth.mint(address(this), 1000 ether);
        usdc.approve(address(amm), type(uint256).max);
        weth.approve(address(amm), type(uint256).max);
        amm.addLiquidity(2_000_000e6, 1000 ether);
    }

    function test_liquidity_seeded() public view {
        assertEq(amm.reserve0(), 2_000_000e6);
        assertEq(amm.reserve1(), 1000 ether);
    }

    function test_swap_usdc_for_weth_moves_reserves_and_pays_trader() public {
        usdc.mint(trader, 2_000e6);
        vm.startPrank(trader);
        usdc.approve(address(amm), 2_000e6);
        uint256 expected = amm.quote(address(usdc), 2_000e6);
        uint256 out = amm.swapExactIn(address(usdc), 2_000e6, expected, trader);
        vm.stopPrank();

        assertEq(out, expected);
        assertEq(weth.balanceOf(trader), out);
        assertApproxEqRel(out, 1 ether, 0.01e18); // ~1 WETH for ~2000 USDC, within 1%
        assertEq(amm.reserve0(), 2_000_000e6 + 2_000e6);
        assertEq(amm.reserve1(), 1000 ether - out);
    }

    function test_swap_reverts_on_slippage() public {
        usdc.mint(trader, 2_000e6);
        vm.startPrank(trader);
        usdc.approve(address(amm), 2_000e6);
        uint256 expected = amm.quote(address(usdc), 2_000e6);
        vm.expectRevert("slippage");
        amm.swapExactIn(address(usdc), 2_000e6, expected + 1, trader); // demand more than possible
        vm.stopPrank();
    }

    function test_constant_product_holds_after_swap() public {
        uint256 kBefore = amm.reserve0() * amm.reserve1();
        usdc.mint(trader, 10_000e6);
        vm.startPrank(trader);
        usdc.approve(address(amm), 10_000e6);
        amm.swapExactIn(address(usdc), 10_000e6, 0, trader);
        vm.stopPrank();
        // k grows slightly due to the 0.30% fee; never shrinks
        assertGe(amm.reserve0() * amm.reserve1(), kBefore);
    }

    function test_bad_tokenIn_reverts() public {
        vm.expectRevert("bad tokenIn");
        amm.swapExactIn(address(0xdead), 1e6, 0, trader);
    }
}
