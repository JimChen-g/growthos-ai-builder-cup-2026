import { bech32 } from "bech32";

import { signer } from "./lib.mjs";

const wallet = signer();
const captchaToken = process.env.FAUCET_CAPTCHA_TOKEN;
if (!captchaToken) {
  throw new Error(
    "The current official faucet requires hCaptcha. Open " +
      "https://testnet.faucet.injective.network/, enter the printed wallet address, " +
      "complete the challenge, and request tokens."
  );
}
const injAddress = bech32.encode(
  "inj",
  bech32.toWords(Buffer.from(wallet.address.slice(2), "hex"))
);
const response = await fetch(
  "https://d1ikjbl0xk5pmx.cloudfront.net/v2/faucet",
  {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ address: injAddress, token: captchaToken }),
  }
);
const body = await response.text();
if (!response.ok) {
  throw new Error(`Faucet request failed (${response.status}): ${body.slice(0, 300)}`);
}
console.log(`Faucet request queued for ${injAddress}.`);
console.log(body.slice(0, 300));
