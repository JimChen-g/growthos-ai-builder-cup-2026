import { compileAuditAnchor } from "./lib.mjs";

const artifact = await compileAuditAnchor();
if (!artifact.bytecode.startsWith("0x") || artifact.bytecode.length < 100) {
  throw new Error("Compiled contract bytecode is unexpectedly empty.");
}
console.log(`AuditAnchor compiled successfully (${artifact.bytecode.length / 2 - 1} bytes).`);
