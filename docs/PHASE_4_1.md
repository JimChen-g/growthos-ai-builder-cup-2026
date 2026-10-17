# Phase 4.1 — Injective Testnet Proof Lab

Phase 4.1 is an isolated technical experiment built after Phase 4 acceptance. It proves that a
GrowthOS Agent Action Ledger snapshot can be independently timestamped and integrity-checked on
Injective EVM Testnet without publishing the underlying business records.

It does not replace the Phase 4 ledger, make agent decisions truthful, create a token, implement
RWA, or claim production/legal finality. Demo and Live remain separate ledgers and cannot be mixed
inside one proof.

## Proof pipeline

```text
Agent Action Ledger entries
        ↓ stable timestamp + entry-id ordering
Canonical JSON for each immutable entry
        ↓ SHA-256 with an entry domain separator
Merkle tree
        ↓ SHA-256 with a node domain separator
32-byte batch root
        ↓ owner-authorized EVM transaction
AuditAnchor persistent contract state on Injective EVM Testnet
```

The canonical entry includes the Phase 4 fields and workflow/approval links. JSON keys are sorted,
UTF-8 is preserved, insignificant whitespace is removed, and timestamps are normalized to UTC with
microseconds. A change to any covered field changes the leaf and batch root.

Only these batch-level values are sent on-chain:

- `root` (`bytes32`);
- hashed batch identifier (`bytes32`);
- `recordCount` (`uint64`);
- `mode` (`0` Demo, `1` Live);
- `previousRoot` (`bytes32`, reserved for later chaining).

Agent identity, action, reason, status, timestamps, workflow IDs, approval IDs, and individual leaf
hashes are not sent to the public chain. The contract stores the anchor in state and also emits an
event; verification reads persistent state rather than relying only on historical event retention.

## API change

| Method | Route | External effect |
|---|---|---|
| `GET` | `/api/v1/audit-proof?mode=demo` | None; builds a local proof |
| `GET` | `/api/v1/audit-proof/status?mode=demo\|live` | None; compares only the selected workspace with its own receipt |

An empty Live ledger returns `409` instead of falling back to Demo data. This endpoint never signs
or broadcasts a transaction. The status endpoint returns `verified`, `not_anchored`, `empty`, or
`mismatch`; it never falls back to the Demo receipt for a Live request.

## Dashboard behavior

Phase 4.1 adds one compact integrity state beside the existing Agent Action Ledger heading. It does
not add another mode switch or repeat technical banners across the product.

- Demo shows **Growth record verified**, the Injective Testnet block, and a public proof link
  while the records covered by the receipt still hash to the anchored root. The receipt covers
  the first `record_count` records in canonical order; records added later (for example after
  the founder approves an action during the demo) are reported as pending the next proof and do
  not break verification.
- If any anchored record changes or disappears the state is **Integrity check failed**
  (`mismatch`).
- The status compares the ledger with the committed receipt; the explorer link is how a viewer
  confirms the same root on-chain.
- Live with no activity shows **No Live proof yet**.
- Live with activity and no separate Live receipt shows **Live history not anchored**. Demo proof
  data is never reused.

The three seeded Demo ledger records use stable sample IDs and timestamps, so the sample proof can
be reproduced after an API restart. Live IDs and timestamps remain runtime-generated.

## Contract safety

`blockchain/contracts/AuditAnchor.sol` has one immutable deployment owner. Only that address can
anchor roots, zero roots and empty batches are rejected, and a root cannot be written twice. Raw
records and wallet secrets are never contract inputs.

The dedicated testnet private key lives only in `.env.injective-testnet`, which is covered by the
repository's `.env.*` ignore rule and created with local file mode `0600`. It must never be reused
on mainnet or placed in frontend code.

## Runbook

```bash
# 1. Backend proof and contract checks
.venv/bin/python -m pytest tests/test_phase41_audit_anchor.py -q
.venv/bin/python -m demo.run_phase41 \
  --mode demo \
  --output data/private/demo-audit-proof.json
npm --prefix blockchain install
npm --prefix blockchain run contract:check

# 2. One-time dedicated testnet signer
npm --prefix blockchain run wallet:create

# 3. Fund via the current official hCaptcha-protected faucet
# https://testnet.faucet.injective.network/
npm --prefix blockchain run faucet:wait

# 4. Deploy, anchor once, and independently re-read contract state
npm --prefix blockchain run anchor:deploy
npm --prefix blockchain run anchor:verify
```

The public deployment and anchor transaction identifiers are written to the Git-ignored file
`data/private/injective-testnet-anchor-receipt.json`. No private key is included in that receipt.
The minimal non-secret Demo receipt used by the dashboard is committed as
`data/demo_injective_anchor_receipt.json`. A future Live receipt has a different private path and
cannot overwrite or satisfy the Demo status.

## Acceptance checklist

- [x] Canonical ledger hashing and deterministic Merkle root
- [x] Empty and mixed-mode batches rejected
- [x] Demo proof read does not initialize or mutate Live history
- [x] Minimal owner-authorized contract compiles with Solidity 0.8.30
- [x] Dedicated ignored testnet signer created
- [x] Official Faucet hCaptcha completed and 1 test INJ received
- [x] Contract deployment confirmed on Injective EVM Testnet (chain ID 1439)
- [x] Demo proof root anchored and verified from persistent contract state
- [x] Public Blockscout transaction link recorded
- [x] Demo proof remains identical across fresh runtime instances
- [x] Dashboard shows a compact proof state and public explorer link
- [x] Demo and Live status APIs use separate receipt paths with no fallback

## Completed Testnet evidence — August 16, 2026

- Signer: `0x18544f0796615611cfdAc1Feac8A29B063B86006`
- Contract: `0xbB2fC40BAAeF4f495A581B43b33271fc5dAB18Bc`
- Deployment transaction: `0x84ea6a6eff1a3fae3eb58dc2a0b1d3a2668b1b941a7369c7a89ffa33923445f7`
- Deployment block: `136980855`
- Demo batch: `growthos-demo-832e0a51089812bd`
- Root: `0x832e0a51089812bd28750b9907edcad9b4fb71d2ca6f30e5fc7a11d3382e75c2`
- Records: `3`
- Anchor transaction: `0x9c79a034467c5fe572dd99cdfca13e801df1be58951c9d9929a81ad89c797249`
- Anchor block: `136981934`
- State observed at block: `136981938`
- Blockscout: <https://testnet-injective.cloud.blockscout.com/tx/0x9c79a034467c5fe572dd99cdfca13e801df1be58951c9d9929a81ad89c797249>

The Faucet UI initially reported that it could not find a funding transaction after its 30-second
wait. This was not a rejected request: the asynchronous queue subsequently credited 1 test INJ.
The EVM RPC similarly advanced account state before its transaction-receipt lookup caught up, so
the final verification reads the root from persistent contract storage and records the block at
which that state was observed.
