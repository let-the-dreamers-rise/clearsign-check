// Collects onchain evidence for the ERC-7730 registry: receipts of every test
// transaction, token metadata, and proxy slots of every deployment.
// Usage: node fetch.mjs <path-to-clear-signing-erc7730-registry> <out.json>
// Node 18+, no dependencies.
import fs from "node:fs";
import path from "node:path";

const [, , REG, OUT = "evidence.json"] = process.argv;
const RPC = {
  1: ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com", "https://rpc.ankr.com/eth"],
  10: ["https://optimism-rpc.publicnode.com", "https://mainnet.optimism.io"],
  56: ["https://bsc-rpc.publicnode.com"],
  100: ["https://gnosis-rpc.publicnode.com"],
  137: ["https://polygon-bor-rpc.publicnode.com"],
  146: ["https://sonic-rpc.publicnode.com"],
  8453: ["https://base-rpc.publicnode.com", "https://mainnet.base.org"],
  42161: ["https://arbitrum-one-rpc.publicnode.com", "https://arb1.arbitrum.io/rpc"],
  43114: ["https://avalanche-c-chain-rpc.publicnode.com"],
  59144: ["https://linea-rpc.publicnode.com"],
  534352: ["https://scroll-rpc.publicnode.com"],
  130: ["https://unichain-rpc.publicnode.com"],
  1868: ["https://soneium-rpc.publicnode.com"],
  480: ["https://worldchain-mainnet.g.alchemy.com/public"],
};
const KEEP = new Set([
  "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef", // Transfer
  "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925", // Approval
  "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31", // ApprovalForAll
  "0xda9fa7c1b00402c17d0161b249b1ab8bbec047c5a52207b9c112deffd817036b", // Permit2 Approval
  "0xc6a377bfc4eb120024a8ac08eef205be16b817020812c73223e81d1bdb9708ec", // Permit2 Permit
  "0xe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c", // WETH Deposit
  "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65", // WETH Withdrawal
]);
const SLOTS = {
  eip1967_impl: "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc",
  eip1967_admin: "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103",
  eip1967_beacon: "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50",
  eip1822_proxiable: "0xc5f16f0fcc639fa48a6947836d9850f504798523bf8c9a3a87d5876cf622bcf7",
  oz_legacy_impl: "0x7050c9e0f4ca769c69bd3a8ef740bc37934f8e2c036e5a723fd8ee048ed3f8c3",
};

let calls = 0;
async function rpc(chain, method, params) {
  const urls = RPC[chain];
  if (!urls) throw new Error(`no rpc for chain ${chain}`);
  let last;
  for (let attempt = 0; attempt < 3; attempt++) {
    for (const url of urls) {
      try {
        calls++;
        const r = await fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
          signal: AbortSignal.timeout(20000),
        });
        const j = await r.json();
        if (j.error) throw new Error(JSON.stringify(j.error));
        return j.result;
      } catch (e) {
        last = e;
      }
    }
    await new Promise((s) => setTimeout(s, 1000 * (attempt + 1)));
  }
  throw last;
}

function walk(dir, pred, acc = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, pred, acc);
    else if (pred(p)) acc.push(p);
  }
  return acc;
}

async function pool(items, n, fn) {
  const out = [];
  let i = 0;
  await Promise.all(
    Array.from({ length: n }, async () => {
      while (i < items.length) {
        const k = i++;
        try { out[k] = await fn(items[k]); } catch (e) { out[k] = { error: String(e).slice(0, 200) }; }
      }
    }),
  );
  return out;
}

const descriptorFiles = walk(path.join(REG, "registry"), (p) => /[\\/](calldata|eip712)-[^\\/]*\.json$/.test(p) && !/[\\/](tests|testsv2|sigs)[\\/]/.test(p))
  .concat(walk(path.join(REG, "ercs"), (p) => /calldata-.*\.json$/.test(p)));
const deployments = new Map(); // chain:addr -> descriptor files
for (const f of descriptorFiles) {
  let d;
  try { d = JSON.parse(fs.readFileSync(f, "utf8")); } catch { continue; }
  const deps = d?.context?.contract?.deployments || d?.context?.eip712?.deployments || [];
  for (const x of deps) {
    if (!x.address || !x.chainId) continue;
    const k = `${x.chainId}:${x.address.toLowerCase()}`;
    if (!deployments.has(k)) deployments.set(k, []);
    deployments.get(k).push(path.relative(REG, f));
  }
}

const testFiles = walk(path.join(REG, "registry"), (p) => /[\\/]testsv2[\\/].*\.tests\.json$/.test(p));
const txs = [];
for (const f of testFiles) {
  const t = JSON.parse(fs.readFileSync(f, "utf8"));
  const desc = path.resolve(path.dirname(f), t.descriptor || "");
  let chains = [];
  try {
    const d = JSON.parse(fs.readFileSync(desc, "utf8"));
    chains = [...new Set((d?.context?.contract?.deployments || []).map((x) => x.chainId))];
  } catch {}
  for (const c of t.tests || []) if (c.txHash) txs.push({ file: path.relative(REG, f), hash: c.txHash, chains: chains.length ? chains : [1] });
}
console.error(`descriptors ${descriptorFiles.length}, deployments ${deployments.size}, test txs ${txs.length}`);

const receipts = {};
await pool(txs, 6, async (t) => {
  for (const chain of [...t.chains].sort((a, b) => (a === 1 ? -1 : b === 1 ? 1 : 0))) {
    if (!RPC[chain]) continue;
    const tx = await rpc(chain, "eth_getTransactionByHash", [t.hash]).catch(() => null);
    if (!tx) continue;
    const rc = await rpc(chain, "eth_getTransactionReceipt", [t.hash]);
    receipts[t.hash.toLowerCase()] = {
      chain, file: t.file, from: tx.from, to: tx.to, value: tx.value, input: tx.input, status: rc.status, block: rc.blockNumber,
      logs: rc.logs.filter((l) => KEEP.has(l.topics[0])).map((l) => ({ address: l.address, topics: l.topics, data: l.data })),
    };
    return;
  }
  receipts[t.hash.toLowerCase()] = { notFound: true, file: t.file, chains: t.chains };
});
console.error(`receipts ${Object.keys(receipts).length}, rpc calls ${calls}`);

// token metadata for every token seen in logs
const tokenKeys = new Set();
for (const r of Object.values(receipts)) for (const l of r.logs || []) tokenKeys.add(`${r.chain}:${l.address.toLowerCase()}`);
const dec = (hex) => (hex && hex !== "0x" ? parseInt(hex.slice(0, 66), 16) : null);
function str(hex) {
  if (!hex || hex === "0x") return null;
  const b = Buffer.from(hex.slice(2), "hex");
  if (b.length === 32) return b.toString("utf8").replace(/\0+$/, "");
  const len = parseInt(b.subarray(32, 64).toString("hex"), 16);
  return b.subarray(64, 64 + len).toString("utf8");
}
const tokens = {};
await pool([...tokenKeys], 6, async (k) => {
  const [chain, addr] = k.split(":");
  const sym = await rpc(+chain, "eth_call", [{ to: addr, data: "0x95d89b41" }, "latest"]).catch(() => null);
  const d = await rpc(+chain, "eth_call", [{ to: addr, data: "0x313ce567" }, "latest"]).catch(() => null);
  tokens[k] = { symbol: str(sym), decimals: dec(d) };
});

// proxy slots and code presence for every deployment on a chain we can reach
const proxies = {};
await pool([...deployments.keys()].filter((k) => RPC[+k.split(":")[0]]), 6, async (k) => {
  const [chain, addr] = k.split(":");
  const out = { descriptors: deployments.get(k) };
  const code = await rpc(+chain, "eth_getCode", [addr, "latest"]);
  out.codeSize = (code.length - 2) / 2;
  out.eip1167 = /^0x363d3d373d3d3d363d73/.test(code);
  for (const [name, slot] of Object.entries(SLOTS)) {
    const v = await rpc(+chain, "eth_getStorageAt", [addr, slot, "latest"]);
    if (v && BigInt(v) !== 0n) out[name] = "0x" + v.slice(-40);
  }
  proxies[k] = out;
});

// recent real transactions to every deployment on chains with a public Blockscout,
// up to 5 successful calls per function the descriptor formats
const SCOUT = { 1: "https://eth.blockscout.com", 10: "https://optimism.blockscout.com", 8453: "https://base.blockscout.com", 42161: "https://arbitrum.blockscout.com" };
const sampled = {};
const scoutKeys = [...deployments.keys()].filter((k) => SCOUT[+k.split(":")[0]]);
await pool(scoutKeys, 3, async (k) => {
  const [chain, addr] = k.split(":");
  const r = await fetch(`${SCOUT[chain]}/api/v2/addresses/${addr}/transactions?filter=to`, { signal: AbortSignal.timeout(30000) });
  if (!r.ok) { sampled[k] = { error: r.status }; return; }
  const j = await r.json();
  const per = {};
  const picked = [];
  for (const t of j.items || []) {
    if (t.status !== "ok" || !t.raw_input || t.raw_input.length < 10) continue;
    const sel = t.raw_input.slice(0, 10);
    per[sel] = (per[sel] || 0) + 1;
    if (per[sel] <= 5) picked.push(t.hash);
  }
  sampled[k] = picked;
  for (const h of picked) {
    if (receipts[h.toLowerCase()]) continue;
    const tx = await rpc(+chain, "eth_getTransactionByHash", [h]).catch(() => null);
    const rc = tx && (await rpc(+chain, "eth_getTransactionReceipt", [h]).catch(() => null));
    if (!rc) continue;
    receipts[h.toLowerCase()] = {
      chain: +chain, sampledFrom: k, from: tx.from, to: tx.to, value: tx.value, input: tx.input, status: rc.status, block: rc.blockNumber,
      logs: rc.logs.filter((l) => KEEP.has(l.topics[0])).map((l) => ({ address: l.address, topics: l.topics, data: l.data })),
    };
    for (const l of receipts[h.toLowerCase()].logs) {
      const tk = `${chain}:${l.address.toLowerCase()}`;
      if (tokens[tk]) continue;
      const sym = await rpc(+chain, "eth_call", [{ to: l.address, data: "0x95d89b41" }, "latest"]).catch(() => null);
      const d = await rpc(+chain, "eth_call", [{ to: l.address, data: "0x313ce567" }, "latest"]).catch(() => null);
      tokens[tk] = { symbol: str(sym), decimals: dec(d) };
    }
  }
});
console.error(`sampled real transactions for ${Object.keys(sampled).length} deployments`);

fs.writeFileSync(OUT, JSON.stringify({ generatedAt: new Date().toISOString(), receipts, tokens, proxies, sampled }, null, 0));
console.error(`wrote ${OUT}: receipts ${Object.keys(receipts).length}, tokens ${Object.keys(tokens).length}, deployments ${Object.keys(proxies).length}, rpc calls ${calls}`);
