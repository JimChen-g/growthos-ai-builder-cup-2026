import { chmod, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import { bech32 } from "bech32";
import { Wallet } from "ethers";

import { projectDirectory } from "./lib.mjs";

const envPath = path.join(projectDirectory, ".env.injective-testnet");
let wallet;

try {
  const existing = await readFile(envPath, "utf8");
  const match = existing.match(/^INJECTIVE_TESTNET_PRIVATE_KEY=(0x[0-9a-fA-F]{64})$/m);
  if (!match) throw new Error("Existing test-wallet file is malformed.");
  wallet = new Wallet(match[1]);
} catch (error) {
  if (error.code !== "ENOENT") throw error;
  wallet = Wallet.createRandom();
  const injAddress = bech32.encode(
    "inj",
    bech32.toWords(Buffer.from(wallet.address.slice(2), "hex"))
  );
  const contents = [
    "# Dedicated Injective EVM Testnet signer. Never commit or expose this file.",
    `INJECTIVE_TESTNET_PRIVATE_KEY=${wallet.privateKey}`,
    `INJECTIVE_TESTNET_EVM_ADDRESS=${wallet.address}`,
    `INJECTIVE_TESTNET_INJ_ADDRESS=${injAddress}`,
    "",
  ].join("\n");
  await writeFile(envPath, contents, { encoding: "utf8", mode: 0o600 });
  await chmod(envPath, 0o600);
}

const injAddress = bech32.encode(
  "inj",
  bech32.toWords(Buffer.from(wallet.address.slice(2), "hex"))
);
console.log(`EVM address: ${wallet.address}`);
console.log(`Injective address: ${injAddress}`);
console.log("Dedicated testnet signer is ready in the ignored local environment file.");
