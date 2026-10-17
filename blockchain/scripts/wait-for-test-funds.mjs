import { formatEther } from "ethers";

import { signer } from "./lib.mjs";

const wallet = signer();
const attempts = Number.parseInt(process.env.FUNDING_ATTEMPTS ?? "40", 10);
const intervalMs = Number.parseInt(process.env.FUNDING_INTERVAL_MS ?? "15000", 10);

for (let attempt = 1; attempt <= attempts; attempt += 1) {
  const balance = await wallet.provider.getBalance(wallet.address);
  console.log(`Funding check ${attempt}/${attempts}: ${formatEther(balance)} test INJ`);
  if (balance > 0n) process.exit(0);
  await new Promise((resolve) => setTimeout(resolve, intervalMs));
}
throw new Error("Testnet funds did not arrive before the funding wait expired.");
