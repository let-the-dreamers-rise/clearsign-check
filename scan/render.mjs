// Renders transactions with the reference ERC-7730 library against a local
// registry checkout, so any registry commit can be replayed.
// Usage: node render.mjs <registry> <evidence.json> <out.json> [hash,hash,...]
// Needs @ethereum-sourcify/clear-signing (npm) resolvable from this directory.
import fs from "node:fs";
import path from "node:path";
import { format } from "@ethereum-sourcify/clear-signing";

const [, , REG, EV, OUT, ONLY] = process.argv;
const ev = JSON.parse(fs.readFileSync(EV, "utf8"));
const read = (p) => JSON.parse(fs.readFileSync(path.join(REG, p), "utf8"));
let calldataIndex = {};
try { calldataIndex = read("index.calldata.json"); } catch {}
let eip712Index = {};
try { eip712Index = read("index.eip712.json"); } catch {}
const resolver = {
  type: "custom",
  resolver: { index: { calldataIndex, eip712Index }, fetchDescriptor: async (p) => read(p) },
};
// Native currency per chain, as a wallet would know it.
const NATIVE = { 1: "ETH", 10: "ETH", 56: "BNB", 100: "xDAI", 137: "POL", 146: "S", 250: "FTM", 324: "ETH", 5000: "MNT",
  8453: "ETH", 42161: "ETH", 42220: "CELO", 43114: "AVAX", 59144: "ETH", 81457: "ETH", 534352: "ETH" };
const NATIVE_ADDRS = new Set(["0x0000000000000000000000000000000000000000", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"]);
const native = (chainId) => ({ name: NATIVE[chainId] ?? "ETH", symbol: NATIVE[chainId] ?? "ETH", decimals: 18 });
const provider = {
  resolveChainInfo: async (chainId) => ({ name: String(chainId), nativeCurrency: native(chainId) }),
  resolveToken: async (chainId, address) => {
    if (NATIVE_ADDRS.has(address.toLowerCase())) return native(chainId);
    const t = ev.tokens[`${chainId}:${address.toLowerCase()}`];
    return t && t.symbol ? { name: t.symbol, symbol: t.symbol, decimals: t.decimals ?? 18 } : null;
  },
};
const only = ONLY ? new Set(ONLY.split(",").map((s) => s.toLowerCase())) : null;
const out = {};
for (const [hash, r] of Object.entries(ev.receipts)) {
  if (only && !only.has(hash)) continue;
  if (!r.input || r.notFound || r.error) continue;
  try {
    const m = await format(
      { chainId: r.chain, to: r.to, data: r.input, value: BigInt(r.value || "0x0"), from: r.from },
      { descriptorResolverOptions: resolver, externalDataProvider: provider },
    );
    out[hash] = m;
  } catch (e) {
    out[hash] = { error: String(e).slice(0, 300) };
  }
}
fs.writeFileSync(OUT, JSON.stringify(out, (k, v) => (typeof v === "bigint" ? v.toString() : v)));
console.error(`rendered ${Object.keys(out).length}`);
