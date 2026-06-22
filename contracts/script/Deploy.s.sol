// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {MockUSDT} from "../src/MockUSDT.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";
import {Commerce} from "../src/Commerce.sol";

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

        vm.stopBroadcast();

        // Write a manifest the SDK + CLI consume. Keys match sdk/plasma_mvp/adapter.py.
        string memory obj = "deployments";
        vm.serializeUint(obj, "chainId", block.chainid);
        vm.serializeUint(obj, "disputeWindow", disputeWindow);
        vm.serializeAddress(obj, "MockUSDT", address(usdt));
        vm.serializeAddress(obj, "IdentityRegistry", address(identity));
        string memory out = vm.serializeAddress(obj, "Commerce", address(commerce));
        vm.writeJson(out, "./deployments/local.json");

        console2.log("MockUSDT:", address(usdt));
        console2.log("IdentityRegistry:", address(identity));
        console2.log("Commerce:", address(commerce));
    }
}
