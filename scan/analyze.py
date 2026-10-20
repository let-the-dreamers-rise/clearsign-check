"""Compare what a wallet shows (ERC-7730 display model from render.mjs) with
what each transaction actually did onchain (receipts in evidence.json).

Usage: python3 -I analyze.py <registry> <evidence.json> <rendered.json> <out-dir>
Writes findings.json and findings.md to out-dir.

Checks (each is deterministic, no model in the loop):
  NATIVE_VALUE_NOT_SHOWN        tx sends native currency, no field shows a native amount
  TOKEN_OUTFLOW_NOT_SHOWN       a token leaves the signer, no field names that token
  OUTFLOW_EXCEEDS_SHOWN_AMOUNT  more of a token leaves the signer than any field shows
  INFLOW_TOKEN_NOT_SHOWN        signer receives a token, while the screen names other tokens and not this one
  APPROVAL_SPENDER_NOT_SHOWN    signer approves a spender the screen never shows
  APPROVAL_FOR_ALL_NOT_SHOWN    signer grants an NFT operator the screen never shows
  NFT_OUTFLOW_NOT_SHOWN         an NFT leaves the signer, collection not shown
  SHOWN_RECIPIENT_UNTOUCHED     a field labelled as recipient shows an address the tx never touches
"""
import json, os, re, sys, glob, collections
from decimal import Decimal, getcontext

getcontext().prec = 80
REG, EV, RENDERED, OUT = sys.argv[1:5]
ev = json.load(open(EV))
rendered = json.load(open(RENDERED))
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
APPROVAL_ALL = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
WETH_WITHDRAWAL = "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65"
NATIVE = {137: "POL", 56: "BNB", 43114: "AVAX", 100: "XDAI", 146: "S"}
NATIVE_WORDS = {"eth", "pol", "matic", "bnb", "avax", "xdai", "s"}
AMOUNT = re.compile(r"^\s*(?:[<>~≈]\s*)?([0-9][0-9,]*(?:\.[0-9]+)?)\s+(.+?)\s*$")
UNBOUNDED = re.compile(r"^\s*(all|unlimited|max(?:imum)?|entire|full)\b", re.I)
ADDR = re.compile(r"0x[0-9a-fA-F]{40}")
RECIPIENT_LABEL = re.compile(r"recipient|receiver|beneficiary|\bto\b|send to|on behalf|for (?:debt )?holder|for account", re.I)
MAXU = 2**256 - 1


def taddr(t):
    return "0x" + t[-40:].lower()


def flat(fields, acc):
    for f in fields or []:
        if "fields" in f:
            flat(f["fields"], acc)
        else:
            acc.append(f)
            ec = (f.get("embeddedCalldata") or {}).get("display") or {}
            flat(ec.get("fields"), acc)
    return acc


def human(raw, decimals):
    return None if decimals is None else Decimal(raw) / (Decimal(10) ** decimals)


def screen(model):
    """Everything a signer can read on the screen."""
    fields = flat(model.get("fields"), [])
    text = " ".join(str(f.get("value", "")) for f in fields)
    intent = model.get("intent")
    intent = " ".join(f"{k} {v}" for k, v in intent.items()) if isinstance(intent, dict) else (intent or "")
    text += " " + intent + " " + (model.get("interpolatedIntent") or "")
    s = {"fields": fields, "text": text, "addrs": {a.lower() for a in ADDR.findall(text)}, "tokens": collections.defaultdict(list),
         "symbols": set(), "native": []}
    for f in fields:
        if f.get("rawAddress"):
            s["addrs"].add(f["rawAddress"].lower())
        v = str(f.get("value", ""))
        m = AMOUNT.match(v)
        bound = "unbounded" if UNBOUNDED.match(v) else (Decimal(m.group(1).replace(",", "")) if m else None)
        sym = (m.group(2) if m else (v.split()[-1] if v.split() else "")).lower()
        if f.get("tokenAddress"):
            s["tokens"][f["tokenAddress"].lower()].append(bound)
            s["symbols"].add(sym)
        elif f.get("format") == "amount" or sym in NATIVE_WORDS:
            s["native"].append(bound)
        elif m or bound == "unbounded":
            s["symbols"].add(sym)
    for w in re.findall(r"[A-Za-z][A-Za-z0-9.\-]{1,14}", text):
        s["symbols"].add(w.lower())
    return s


findings, stats = [], collections.Counter()
tests_by_hash = {}
for f in glob.glob(os.path.join(REG, "registry/*/testsv2/*.tests.json")):
    for c in json.load(open(f)).get("tests", []):
        if c.get("txHash"):
            tests_by_hash[c["txHash"].lower()] = os.path.relpath(f, REG)

for h, r in ev["receipts"].items():
    m = rendered.get(h)
    if r.get("notFound") or r.get("logs") is None:
        stats["no_receipt"] += 1
        continue
    if r.get("status") != "0x1":
        stats["reverted"] += 1
        continue
    if not m or m.get("error") or not m.get("fields") and not m.get("intent"):
        stats["not_rendered"] += 1
        continue
    if m.get("rawCalldataFallback"):
        stats["no_descriptor"] += 1
        continue
    stats["checked"] += 1
    chain, signer, to = r["chain"], r["from"].lower(), (r["to"] or "").lower()
    s = screen(m)
    tok = lambda a: ev["tokens"].get(f"{chain}:{a.lower()}", {})
    def named(a):
        t = tok(a)
        return a.lower() in s["tokens"] or a.lower() in s["addrs"] or ((t.get("symbol") or "").lower() in s["symbols"] and t.get("symbol"))
    base = {"tx": h, "chain": chain, "to": to, "signer": signer, "source": tests_by_hash.get(h, "sampled"),
            "intent": m.get("intent"), "screen": [(f.get("label"), f.get("value")) for f in s["fields"]]}
    add = lambda kind, detail: findings.append({**base, "kind": kind, "detail": detail})

    val = int(r.get("value") or "0x0", 16)
    if val > 0 and not s["native"]:
        add("NATIVE_VALUE_NOT_SHOWN", f"sends {human(val, 18)} {NATIVE.get(chain, 'ETH')}, no field shows a native amount")
    elif val > 0 and "unbounded" not in s["native"]:
        shown = max((b for b in s["native"] if b is not None), default=None)
        if shown is not None and shown * Decimal("1.005") < human(val, 18):
            add("OUTFLOW_EXCEEDS_SHOWN_AMOUNT", f"sends {human(val, 18)} native, largest native amount shown is {shown}")

    out, inflow = collections.defaultdict(int), collections.defaultdict(int)
    for l in r["logs"]:
        tp = l["topics"]
        data = int(l["data"], 16) if l.get("data") not in (None, "0x") else 0
        if tp[0] == TRANSFER and len(tp) == 3:
            if taddr(tp[1]) == signer:
                out[l["address"].lower()] += data
            if taddr(tp[2]) == signer:
                inflow[l["address"].lower()] += data
        elif tp[0] == TRANSFER and len(tp) == 4 and taddr(tp[1]) == signer and not named(l["address"]):
            add("NFT_OUTFLOW_NOT_SHOWN", f"NFT {l['address']} #{int(tp[3], 16)} leaves the signer, collection not shown")
        elif tp[0] == APPROVAL and len(tp) == 3 and taddr(tp[1]) == signer and data > 0:
            sp = taddr(tp[2])
            if sp not in s["addrs"] and sp != to:
                add("APPROVAL_SPENDER_NOT_SHOWN", f"signer approves {sp} for {'unlimited' if data >= MAXU // 2 else human(data, tok(l['address']).get('decimals'))} {tok(l['address']).get('symbol')}, spender not shown")
        elif tp[0] == APPROVAL_ALL and taddr(tp[1]) == signer and data == 1:
            op = taddr(tp[2])
            if op not in s["addrs"] and op != to:
                add("APPROVAL_FOR_ALL_NOT_SHOWN", f"signer lets {op} move every {tok(l['address']).get('symbol')} NFT, operator not shown")

    for a, raw in out.items():
        if raw == 0:
            continue
        t, amt = tok(a), human(raw, tok(a).get("decimals"))
        if not named(a):
            add("TOKEN_OUTFLOW_NOT_SHOWN", f"{amt} {t.get('symbol') or a} leaves the signer, the screen never names this token")
            continue
        bounds = s["tokens"].get(a, [])
        nums = [b for b in bounds if isinstance(b, Decimal)]
        if nums and "unbounded" not in bounds and amt is not None and max(nums) * Decimal("1.005") < amt:
            add("OUTFLOW_EXCEEDS_SHOWN_AMOUNT", f"{amt} {t.get('symbol')} leaves the signer, largest amount shown is {max(nums)}")

    if s["tokens"] or s["native"]:
        for a, raw in inflow.items():
            if raw and not named(a) and a not in out:
                t = tok(a)
                add("INFLOW_TOKEN_NOT_SHOWN", f"signer receives {human(raw, t.get('decimals'))} {t.get('symbol') or a}, the screen names other assets but not this one")

    touched = {signer, to}
    for l in r["logs"]:
        touched.add(l["address"].lower())
        touched.update(taddr(t) for t in l["topics"][1:])
    for f in s["fields"]:
        if RECIPIENT_LABEL.search(f.get("label") or ""):
            for a in ADDR.findall((f.get("rawAddress") or "") + " " + str(f.get("value", ""))):
                a = a.lower()
                if a not in touched and int(a, 16) > 0xFFFF:
                    add("SHOWN_RECIPIENT_UNTOUCHED", f"'{f.get('label')}' shows {a}, which no transfer or approval in this tx involves")

# upgradeable deployments and declared preconditions
prox = ev.get("proxies", {})
upgradeable = {k: v for k, v in prox.items() if any(x in v for x in ("eip1967_impl", "eip1967_beacon", "eip1822_proxiable", "oz_legacy_impl"))}
declared = set()
for f in glob.glob(os.path.join(REG, "registry/*/*.json")):
    try:
        c = (json.load(open(f)).get("context") or {}).get("contract") or {}
    except Exception:
        continue
    if "proxy" in c or "stateRefs" in c:
        declared.update(f"{x.get('chainId')}:{x.get('address', '').lower()}" for x in c.get("deployments", []))
attested = {re.sub(r"\.eip155-.*$", "", os.path.relpath(f, REG)).replace("/sigs/", "/") + ".json" for f in glob.glob(os.path.join(REG, "registry/*/sigs/*.json"))}

summary = {
    "transactions": len(ev["receipts"]), **stats, "findings": len(findings),
    "by_kind": dict(collections.Counter(f["kind"] for f in findings)),
    "contracts_with_findings": len({(f["chain"], f["to"]) for f in findings}),
    "deployments_checked": len(prox), "upgradeable": len(upgradeable),
    "upgradeable_with_declared_precondition": sum(k in declared for k in upgradeable),
    "upgradeable_with_attested_descriptor": sum(any(d in attested for d in v["descriptors"]) for v in upgradeable.values()),
}
os.makedirs(OUT, exist_ok=True)
json.dump({"summary": summary, "findings": findings, "upgradeable": upgradeable}, open(os.path.join(OUT, "findings.json"), "w"), indent=1, default=str)
with open(os.path.join(OUT, "findings.md"), "w") as fh:
    fh.write("# Descriptor vs onchain behaviour\n\n```\n" + json.dumps(summary, indent=1) + "\n```\n\n")
    for f in sorted(findings, key=lambda f: (f["kind"], f["to"])):
        fh.write(f"- **{f['kind']}** chain {f['chain']} contract {f['to']} tx {f['tx']} ({f['source']}): {f['detail']}\n")
print(json.dumps(summary, indent=1))
