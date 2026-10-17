import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { ContractFactory, formatEther, keccak256, toUtf8Bytes } from "ethers";

import {
  compileAuditAnchor,
  network,
  projectDirectory,
  readProof,
  signer,
  waitForAnchor,
  waitForCode,
} from "./lib.mjs";

const wallet = signer();
const actualNetwork = await wallet.provider.getNetwork();
if (actualNetwork.chainId !== BigInt(network.chainId)) {
  throw new Error(`Wrong chain: expected ${network.chainId}, received ${actualNetwork.chainId}.`);
}
const balance = await wallet.provider.getBalance(wallet.address);
if (balance === 0n) {
  throw new Error("The dedicated signer has no test INJ. Request faucet funds first.");
}

const proof = await readProof();
const artifact = await compileAuditAnchor();
console.log(`Signer: ${wallet.address}`);
console.log(`Starting balance: ${formatEther(balance)} test INJ`);
console.log(`Proof: ${proof.batch_id} (${proof.record_count} ${proof.mode} records)`);

const factory = new ContractFactory(artifact.abi, artifact.bytecode, wallet);
const contract = await factory.deploy();
const deployment = contract.deploymentTransaction();
console.log(`Deployment submitted: ${deployment.hash}`);
const contractAddress = await contract.getAddress();
await waitForCode(wallet.provider, contractAddress);
const deploymentObservedAtBlock = await wallet.provider.getBlockNumber();
console.log(`AuditAnchor deployed: ${contractAddress}`);

const batchIdHash = keccak256(toUtf8Bytes(proof.batch_id));
const mode = proof.mode === "demo" ? 0 : 1;
const transaction = await contract.anchor(
  proof.root_hash,
  batchIdHash,
  proof.record_count,
  mode,
  proof.previous_root
);
console.log(`Anchor submitted: ${transaction.hash}`);
const stored = await waitForAnchor(contract, proof.root_hash);
if (stored.batchIdHash !== batchIdHash || stored.recordCount !== BigInt(proof.record_count)) {
  throw new Error("The persisted contract state does not match the local proof.");
}
const anchorObservedAtBlock = await wallet.provider.getBlockNumber();

const receipt = {
  network: network.name,
  chain_id: network.chainId,
  contract_address: contractAddress,
  deployment_transaction: deployment.hash,
  deployment_observed_at_block: deploymentObservedAtBlock,
  anchor_transaction: transaction.hash,
  observed_at_block: anchorObservedAtBlock,
  explorer_url: `${network.explorerUrl}/tx/${transaction.hash}`,
  contract_url: `${network.explorerUrl}/address/${contractAddress}`,
  signer_address: wallet.address,
  proof,
};
const outputDirectory = path.join(projectDirectory, "data", "private");
await mkdir(outputDirectory, { recursive: true });
const outputPath = path.join(outputDirectory, "injective-testnet-anchor-receipt.json");
await writeFile(outputPath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");

console.log(`Anchor verified from contract state at observed block ${anchorObservedAtBlock}.`);
console.log(`Explorer: ${receipt.explorer_url}`);
console.log(`Public receipt saved to ${outputPath}.`);
