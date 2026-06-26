// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {MockUSDT} from "../src/MockUSDT.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";
import {Commerce} from "../src/Commerce.sol";
import {MiniERC20} from "../src/MiniERC20.sol";
import {MiniAMM} from "../src/MiniAMM.sol";
import {WXPL} from "../src/WXPL.sol";
import {AgentSessionDelegate} from "../src/AgentSessionDelegate.sol";

/// @notice Deploys the M1 contracts to the local chain and writes a deployments manifest
///         (deployments/local.json) that the Python SDK reads for addresses.
contract Deploy is Script {
    function run() external {
        uint256 pk = vm.envUint("RELAYER_PK");
        address me = vm.addr(pk);

        // The WXPL venue wraps native ETH to seed the pools (1M + 2M used by the two WXPL pools), so the
        // deployer must hold at least this much native balance. Tunable for constrained chains, but the
        // pool seeds below assume the 3M default. Preflight LOUDLY: a silent OutOfFunds here was almost
        // always "you're pointed at the wrong chain" (e.g. a Docker proxy shadowing localhost:8545 with
        // a default-balance node) — fail with an actionable message instead of a cryptic revert.
        uint256 wxplSeed = vm.envOr("WXPL_SEED_WEI", uint256(3_000_000 ether));
        console2.log("Deploy: chainId", block.chainid);
        console2.log("Deploy: deployer", me);
        console2.log("Deploy: balance(wei)", me.balance);
        console2.log("Deploy: WXPL seed(wei)", wxplSeed);
        require(
            me.balance >= wxplSeed + 1 ether,
            "deployer underfunded for the WXPL venue: start a high-balance node "
            "(anvil --balance 200000000 --hardfork prague) or lower WXPL_SEED_WEI, "
            "and confirm RPC points at THAT node (a Docker proxy may shadow :8545)"
        );

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

        // mint/wrap the deployer's seed balances (USDC/WETH are mintable; WXPL wraps native)
        usdc.mint(me, 2_100_000e6);
        weth.mint(me, 1_100 ether);
        wxpl.deposit{value: wxplSeed}(); // amount preflighted above (default 3M; tunable via WXPL_SEED_WEI)

        // pools (constant-product). prices chosen for sane demo math:
        //   USDC/WETH = 2000 USDC/WETH ; USDC/WXPL = 0.10 USDC/WXPL ; WETH/WXPL = 20000 WXPL/WETH
        MiniAMM poolUW = new MiniAMM(address(usdc), address(weth));
        MiniAMM poolUX = new MiniAMM(address(usdc), address(wxpl));
        MiniAMM poolWX = new MiniAMM(address(weth), address(wxpl));

        usdc.approve(address(poolUW), 2_000_000e6);
        weth.approve(address(poolUW), 1_000 ether);
        poolUW.addLiquidity(2_000_000e6, 1_000 ether);

        // Split the WXPL seed across its two pools (1:2, as the defaults) and derive the paired-token
        // amounts from it so PRICES stay fixed when WXPL_SEED_WEI is lowered for a constrained chain.
        // Scoped in a block so the temporaries free the stack (avoids stack-too-deep).
        {
            uint256 wxUX = wxplSeed / 3;             // -> 1,000,000 WXPL at the 3M default
            uint256 wxWX = wxplSeed - wxUX;          // -> 2,000,000 WXPL (remainder; no leftover wrap)
            uint256 usdcUX = (100_000e6 * wxUX) / 1_000_000 ether;
            uint256 wethWX = (100 ether * wxWX) / 2_000_000 ether;

            usdc.approve(address(poolUX), usdcUX);
            wxpl.approve(address(poolUX), wxUX);
            poolUX.addLiquidity(usdcUX, wxUX);

            weth.approve(address(poolWX), wethWX);
            wxpl.approve(address(poolWX), wxWX);
            poolWX.addLiquidity(wethWX, wxWX);
        }

        // --- EIP-7702 "trade from the user's own address" rail ---
        // The single delegation target a user EOA delegates its code to (no constructor args; runs in
        // the user's context). Custodies nothing; enforces all session money-bounds on-chain.
        AgentSessionDelegate sessionDelegate = new AgentSessionDelegate();

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
        vm.serializeAddress(obj, "AgentSessionDelegate", address(sessionDelegate));
        // back-compat: existing swap helpers/tests read "MiniAMM" as the USDC/WETH pool
        string memory out = vm.serializeAddress(obj, "MiniAMM", address(poolUW));
        vm.writeJson(out, "./deployments/local.json");

        console2.log("USDC:", address(usdc));
        console2.log("WETH:", address(weth));
        console2.log("WXPL:", address(wxpl));
        console2.log("Pool USDC/WETH:", address(poolUW));
        console2.log("Pool USDC/WXPL:", address(poolUX));
        console2.log("Pool WETH/WXPL:", address(poolWX));
        console2.log("AgentSessionDelegate:", address(sessionDelegate));
    }
}
