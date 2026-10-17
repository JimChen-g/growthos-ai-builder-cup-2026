import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { Contract, formatEther, keccak256, toUtf8Bytes } from "ethers";

import {
  compileAuditAnchor,
  network,
  projectDirectory,
  readProof,
  signer,
  waitForAnchor,
} from "./lib.mjs";

const contractAddress = process.env.AUDIT_ANCHOR_CONTRACT_ADDRESS;
const deploymentTransaction = process.env.AUDIT_ANCHOR_DEPLOYMENT_TX;
if (!contractAddress || !/^0x[0-9a-fA-F]{40}$/.test(contractAddress)) {
  throw new Error("Set AUDIT_ANCHOR_CONTRACT_ADDRESS to the deployed testnet contract.");
}
if (!deploymentTransaction || !/^0x[0-9a-fA-F]{64}$/.test(deploymentTransaction)) {
  throw new Error("Set AUDIT_ANCHOR_DEPLOYMENT_TX to the deployment transaction hash.");
}

const wallet = signer();
const proof = await readProof();
const artifact = await compileAuditAnchor();
const code = await wallet.provider.getCode(contractAddress);
if (code === "0x") throw new Error("No contract code exists at the supplied address.");

const contract = new Contract(contractAddress, artifact.abi, wallet);
const owner = await contract.owner();
if (owner.toLowerCase() !== wallet.address.toLowerCase()) {
  throw new Error("The dedicated testnet signer does not own this AuditAnchor contract.");
}
const existing = await contract.anchors(proof.root_hash);
if (existing.anchoredAt !== 0n) throw new Error("This Demo root is already anchored.");

const batchIdHash = keccak256(toUtf8Bytes(proof.batch_id));
const mode = proof.mode === "demo" ? 0 : 1;
const balance = await wallet.provider.getBalance(wallet.address);
console.log(`Contract: ${contractAddress}`);
console.log(`Signer balance: ${formatEther(balance)} test INJ`);
const transaction = await contract.anchor(
  proof.root_hash,
  batchIdHash,
  proof.record_count,
  mode,
  proof.previous_root
);
console.log(`Anchor submitted: ${transaction.hash}`);

// Injective's public RPC can persist state before its transaction lookup index catches up.
// Confirm the source of truth directly from contract storage instead of waiting on a receipt.
const stored = await waitForAnchor(contract, proof.root_hash);
if (stored.batchIdHash !== batchIdHash || stored.recordCount !== BigInt(proof.record_count)) {
  throw new Error("The persisted contract state does not match the local proof.");
}
const observedAtBlock = await wallet.provider.getBlockNumber();

const receipt = {
  network: network.name,
  chain_id: network.chainId,
  contract_address: contractAddress,
  deployment_transaction: deploymentTransaction,
  anchor_transaction: transaction.hash,
  observed_at_block: observedAtBlock,
  explorer_url: `${network.explorerUrl}/tx/${transaction.hash}`,
  contract_url: `${network.explorerUrl}/address/${contractAddress}`,
  signer_address: wallet.address,
  proof,
};
const outputDirectory = path.join(projectDirectory, "data", "private");
await mkdir(outputDirectory, { recursive: true });
const outputPath = path.join(outputDirectory, "injective-testnet-anchor-receipt.json");
await writeFile(outputPath, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");

console.log(`Anchor verified from contract state at observed block ${observedAtBlock}.`);
console.log(`Explorer: ${receipt.explorer_url}`);
console.log(`Public receipt saved to ${outputPath}.`);
