// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {MockUSDT} from "../src/MockUSDT.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";
import {Commerce} from "../src/Commerce.sol";
import {MiniERC20} from "../src/MiniERC20.sol";
import {MiniAMM} from "../src/MiniAMM.sol";
import {WXPL} from "../src/WXPL.sol";

/// @notice Deploys the M1 contracts to the local chain and writes a deployments manifest
///         (deployments/local.json) that the Python SDK reads for addresses.
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("RELAYER_PK");
        vm.startBroadcast(pk);

        // small dispute window so local demos settle in seconds
        uint64 disputeWindow = uint64(vm.envOr("DISPUTE_WINDOW", uint256(5)));

        MockUSDT usdt = new MockUSDT();
        IdentityRegistry identity = new IdentityRegistry();
        Commerce commerce = new Commerce(address(usdt), disputeWindow);

        // --- local multi-pair swap venue for the guarded agentic trader ---
        // tokens: USDC (6dp), WETH (18dp), WXPL (18dp, wrapped native — the XPL stand-in)
        MiniERC20 usdc = new MiniERC20("USD Coin", "USDC", 6);
        MiniERC20 weth = new MiniERC20("Wrapped Ether", "WETH", 18);
        WXPL wxpl = new WXPL();
        address me = vm.addr(pk);

        // mint/wrap the deployer's seed balances (USDC/WETH are mintable; WXPL wraps native)
        usdc.mint(me, 2_100_000e6);
        weth.mint(me, 1_100 ether);
        wxpl.deposit{value: 3_000_000 ether}(); // needs a high --balance anvil

        // pools (constant-product). prices chosen for sane demo math:
        //   USDC/WETH = 2000 USDC/WETH ; USDC/WXPL = 0.10 USDC/WXPL ; WETH/WXPL = 20000 WXPL/WETH
        MiniAMM poolUW = new MiniAMM(address(usdc), address(weth));
        MiniAMM poolUX = new MiniAMM(address(usdc), address(wxpl));
        MiniAMM poolWX = new MiniAMM(address(weth), address(wxpl));

        usdc.approve(address(poolUW), 2_000_000e6);
        weth.approve(address(poolUW), 1_000 ether);
        poolUW.addLiquidity(2_000_000e6, 1_000 ether);

        usdc.approve(address(poolUX), 100_000e6);
        wxpl.approve(address(poolUX), 1_000_000 ether);
        poolUX.addLiquidity(100_000e6, 1_000_000 ether);

        weth.approve(address(poolWX), 100 ether);
        wxpl.approve(address(poolWX), 2_000_000 ether);
        poolWX.addLiquidity(100 ether, 2_000_000 ether);

        vm.stopBroadcast();

        // Write a manifest the SDK + CLI consume. Keys match sdk/plasma_mvp/adapter.py.
        string memory obj = "deployments";
        vm.serializeUint(obj, "chainId", block.chainid);
        vm.serializeUint(obj, "disputeWindow", disputeWindow);
        vm.serializeAddress(obj, "MockUSDT", address(usdt));
        vm.serializeAddress(obj, "IdentityRegistry", address(identity));
        vm.serializeAddress(obj, "Commerce", address(commerce));
        vm.serializeAddress(obj, "USDC", address(usdc));
        vm.serializeAddress(obj, "WETH", address(weth));
        vm.serializeAddress(obj, "WXPL", address(wxpl));
        // pool registry — keys are "TOKENA/TOKENB" (sorted by the venue loader)
        vm.serializeAddress(obj, "Pool_USDC_WETH", address(poolUW));
        vm.serializeAddress(obj, "Pool_USDC_WXPL", address(poolUX));
        vm.serializeAddress(obj, "Pool_WETH_WXPL", address(poolWX));
        // back-compat: existing swap helpers/tests read "MiniAMM" as the USDC/WETH pool
        string memory out = vm.serializeAddress(obj, "MiniAMM", address(poolUW));
        vm.writeJson(out, "./deployments/local.json");

        console2.log("USDC:", address(usdc));
        console2.log("WETH:", address(weth));
        console2.log("WXPL:", address(wxpl));
        console2.log("Pool USDC/WETH:", address(poolUW));
        console2.log("Pool USDC/WXPL:", address(poolUX));
        console2.log("Pool WETH/WXPL:", address(poolWX));
    }
}
