"""Export a Demo or Live Agent Action Ledger proof for Injective anchoring."""

import argparse
import asyncio
from pathlib import Path

from backend.models.connectors import DataMode
from backend.services.audit_anchor import build_audit_batch_proof
from backend.services.audit_log import AuditLogService
from backend.services.founder_experience import build_demo_founder_experience
from backend.services.phase2_runtime import build_demo_runtime


async def export_proof(mode: DataMode) -> str:
    if mode is DataMode.DEMO:
        runtime = build_demo_runtime()
        experience = build_demo_founder_experience(runtime)
        await experience.experience()
        ledger = await runtime.audit_log.list(limit=100)
    else:
        ledger = await AuditLogService(mode=DataMode.LIVE).list(limit=100)

    proof = build_audit_batch_proof(ledger.entries, mode=mode)
    return proof.model_dump_json(indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=[mode.value for mode in DataMode], default="demo")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        rendered = asyncio.run(export_proof(DataMode(args.mode)))
    except ValueError as error:
        parser.error(str(error))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Audit proof saved to {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
