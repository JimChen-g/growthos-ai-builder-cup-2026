import { readFile } from "node:fs/promises";
import path from "node:path";

import { Contract, keccak256, toUtf8Bytes } from "ethers";

import { compileAuditAnchor, projectDirectory, provider } from "./lib.mjs";

const receiptPath = path.join(
  projectDirectory,
  "data",
  "private",
  "injective-testnet-anchor-receipt.json"
);
const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
const artifact = await compileAuditAnchor();
const contract = new Contract(receipt.contract_address, artifact.abi, provider());
const stored = await contract.anchors(receipt.proof.root_hash);
const expectedBatchIdHash = keccak256(toUtf8Bytes(receipt.proof.batch_id));

if (stored.batchIdHash !== expectedBatchIdHash) throw new Error("Batch ID hash mismatch.");
if (stored.previousRoot !== receipt.proof.previous_root) throw new Error("Previous root mismatch.");
if (stored.recordCount !== BigInt(receipt.proof.record_count)) {
  throw new Error("Record count mismatch.");
}
if (stored.anchoredAt === 0n) throw new Error("The proof root is not anchored.");

console.log("Injective Testnet proof verified against persistent contract state.");
console.log(`Root: ${receipt.proof.root_hash}`);
console.log(`Records: ${stored.recordCount}`);
console.log(`Explorer: ${receipt.explorer_url}`);
