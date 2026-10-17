import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { config } from "dotenv";
import { JsonRpcProvider, Wallet } from "ethers";
import solc from "solc";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
export const blockchainDirectory = path.resolve(scriptDirectory, "..");
export const projectDirectory = path.resolve(blockchainDirectory, "..");

config({ path: path.join(projectDirectory, ".env.injective-testnet"), quiet: true });

export const network = Object.freeze({
  chainId: 1439,
  name: "injective-evm-testnet",
  rpcUrl: "https://k8s.testnet.json-rpc.injective.network/",
  explorerUrl: "https://testnet-injective.cloud.blockscout.com",
});

export function provider() {
  return new JsonRpcProvider(network.rpcUrl, network.chainId, { staticNetwork: true });
}

export function signer() {
  const privateKey = process.env.INJECTIVE_TESTNET_PRIVATE_KEY;
  if (!privateKey) {
    throw new Error(
      "Missing .env.injective-testnet. Run npm run wallet:create before requesting funds."
    );
  }
  return new Wallet(privateKey, provider());
}

export async function compileAuditAnchor() {
  const contractPath = path.join(blockchainDirectory, "contracts", "AuditAnchor.sol");
  const source = await readFile(contractPath, "utf8");
  const input = {
    language: "Solidity",
    sources: { "AuditAnchor.sol": { content: source } },
    settings: {
      optimizer: { enabled: true, runs: 200 },
      outputSelection: { "*": { "*": ["abi", "evm.bytecode.object"] } },
    },
  };
  const output = JSON.parse(solc.compile(JSON.stringify(input)));
  const errors = (output.errors ?? []).filter((item) => item.severity === "error");
  if (errors.length) {
    throw new Error(errors.map((item) => item.formattedMessage).join("\n"));
  }
  const artifact = output.contracts["AuditAnchor.sol"].AuditAnchor;
  return { abi: artifact.abi, bytecode: `0x${artifact.evm.bytecode.object}` };
}

export async function readProof() {
  const proofPath = process.env.PROOF_PATH
    ? path.resolve(process.env.PROOF_PATH)
    : path.join(projectDirectory, "data", "private", "demo-audit-proof.json");
  const proof = JSON.parse(await readFile(proofPath, "utf8"));
  if (!/^0x[0-9a-f]{64}$/.test(proof.root_hash)) {
    throw new Error("The proof root_hash is not a lowercase 32-byte value.");
  }
  if (!Number.isInteger(proof.record_count) || proof.record_count < 1) {
    throw new Error("The proof must contain at least one record.");
  }
  if (!['demo', 'live'].includes(proof.mode)) {
    throw new Error("The proof mode must be demo or live.");
  }
  return proof;
}

export async function waitForAnchor(contract, rootHash, options = {}) {
  const attempts = options.attempts ?? 40;
  const intervalMs = options.intervalMs ?? 3000;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const stored = await contract.anchors(rootHash);
    if (stored.anchoredAt !== 0n) return stored;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("The anchor transaction was not visible in contract state before timeout.");
}

export async function waitForCode(rpcProvider, contractAddress, options = {}) {
  const attempts = options.attempts ?? 40;
  const intervalMs = options.intervalMs ?? 3000;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const code = await rpcProvider.getCode(contractAddress);
    if (code !== "0x") return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error("The deployed contract code was not visible before timeout.");
}
