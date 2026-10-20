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
  AMOUNT_IN_BASE_UNITS          a field shows a moving amount as a raw base-unit integer with no unit
  AMOUNT_WITHOUT_UNIT           a field shows a moving amount with no token or currency
  AMOUNT_WRONG_UNIT             a field shows a moving amount labelled with a different asset
  UNLIMITED_SHOWN_AS_NUMBER     a field shows a sentinel or maximum value as a plain 40+ digit number
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
AMOUNT = re.compile(r"^\s*(?:[<>~≈]\s*)?([0-9][0-9,]*(?:\.[0-9]+)?)\s*([^\d\s,.%].*?)\s*$")
# warnings that mean this harness lacked data a wallet would have, not a descriptor defect
HARNESS_GAPS = {"UNKNOWN_CHAIN", "UNKNOWN_TOKEN"}
AMOUNT_LABEL = re.compile(r"amount|value|qty|quantity|sum|total|deposit|withdraw|send|receive|pay|stake|redeem|buy|sell|supply|repay|borrow|input|output|\bin\b|\bout\b", re.I)
NOT_AMOUNT_LABEL = re.compile(r"\bid\b|nonce|index|deadline|expir|time|date|chain|fee tier|slippage|bps|percent|ratio|rate|count", re.I)
BARE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
UNBOUNDED = re.compile(r"^\s*(all|unlimited|max(?:imum)?|entire|full)\b", re.I)
ADDR = re.compile(r"0x(?:0{24})?([0-9a-fA-F]{40})(?![0-9a-fA-F])")
RECIPIENT_LABEL = re.compile(r"recipient|receiver|beneficiary|\bto\b|send to|on behalf|for (?:debt )?holder|for account", re.I)
MAXU = 2**256 - 1
ZERO = "0x" + "0" * 40
NATIVE_ADDRS = {ZERO, "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"}


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
    s = {"fields": fields, "text": text, "addrs": {"0x" + a.lower() for a in ADDR.findall(text)}, "tokens": collections.defaultdict(list),
         "symbols": set(), "native": []}
    for f in fields:
        if f.get("rawAddress"):
            s["addrs"].add(f["rawAddress"].lower())
        v = str(f.get("value", ""))
        m = AMOUNT.match(v)
        bound = "unbounded" if UNBOUNDED.match(v) else (Decimal(m.group(1).replace(",", "")) if m else None)
        sym = (m.group(2) if m else (v.split()[-1] if v.split() else "")).lower()
        if f.get("tokenAddress") and f["tokenAddress"].lower() in NATIVE_ADDRS:
            s["native"].append(bound)
        elif f.get("tokenAddress"):
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
    if m and m.get("rawCalldataFallback"):
        stats["no_descriptor_for_function"] += 1
        continue
    if not m or m.get("error") or not m.get("fields") and not m.get("intent"):
        stats["not_rendered"] += 1
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
    burned, minted_only = set(), collections.defaultdict(lambda: True)
    for l in r["logs"]:
        tp = l["topics"]
        if tp[0] == TRANSFER and len(tp) == 3:
            if taddr(tp[1]) == signer and taddr(tp[2]) != ZERO:
                burned.discard(l["address"].lower()); out.setdefault("_real_" + l["address"].lower(), 1)
            elif taddr(tp[1]) == signer:
                burned.add(l["address"].lower())
            if taddr(tp[2]) == signer and taddr(tp[1]) != ZERO:
                minted_only[l["address"].lower()] = False
    for l in r["logs"]:
        tp = l["topics"]
        data = int(l["data"], 16) if l.get("data") not in (None, "0x") else 0
        if tp[0] == TRANSFER and len(tp) == 3:
            if taddr(tp[1]) == signer:
                out[l["address"].lower()] += data
            if taddr(tp[2]) == signer:
                inflow[l["address"].lower()] += data
        elif tp[0] == TRANSFER and len(tp) == 4 and taddr(tp[1]) == signer and taddr(tp[2]) == ZERO:
            stats["nft_burns_ignored"] += 1
        elif tp[0] == TRANSFER and len(tp) == 4 and taddr(tp[1]) == signer and not named(l["address"]):
            add("NFT_OUTFLOW_NOT_SHOWN", f"NFT {l['address']} #{int(tp[3], 16)} leaves the signer, collection not shown")
        elif tp[0] == APPROVAL and len(tp) == 3 and taddr(tp[1]) == signer and data > 0:
            sp = taddr(tp[2])
            pulled = any(x["topics"][0] == TRANSFER and len(x["topics"]) == 3 and x["address"].lower() == l["address"].lower()
                         and taddr(x["topics"][1]) == signer for x in r["logs"])
            if sp not in s["addrs"] and sp != to and not pulled:
                add("APPROVAL_SPENDER_NOT_SHOWN", f"signer approves {sp} for {'unlimited' if data >= MAXU // 2 else human(data, tok(l['address']).get('decimals'))} {tok(l['address']).get('symbol')}, spender not shown")
        elif tp[0] == APPROVAL_ALL and taddr(tp[1]) == signer and data == 1:
            op = taddr(tp[2])
            if op not in s["addrs"] and op != to:
                add("APPROVAL_FOR_ALL_NOT_SHOWN", f"signer lets {op} move every {tok(l['address']).get('symbol')} NFT, operator not shown")

    for a, raw in out.items():
        if raw == 0 or a.startswith("_real_"):
            continue
        if a in burned and ("_real_" + a) not in out:
            stats["receipt_token_burns_ignored"] += 1
            continue
        t, amt = tok(a), human(raw, tok(a).get("decimals"))
        shown_amounts = [b for v in s["tokens"].values() for b in v if isinstance(b, Decimal)] + [b for b in s["native"] if isinstance(b, Decimal)]
        shown_syms = {x for x in s["symbols"] if len(x) >= 3} | ({NATIVE.get(chain, "ETH").lower()} if s["native"] else set())
        unbounded = any(b == "unbounded" for v in list(s["tokens"].values()) + [s["native"]] for b in v)
        if not named(a) and (amt in shown_amounts or unbounded) and any(x in (t.get("symbol") or "").lower() for x in shown_syms):
            stats["wrapped_equivalent_ignored"] += 1  # e.g. aWETH pulled to withdraw the same amount of ETH
            continue
        if not named(a):
            raw_shown = [f.get("label") for f in s["fields"] if str(f.get("value", "")).replace(",", "").strip() in (str(raw), str(amt))]
            how = f"; the screen shows it only as the bare number under '{raw_shown[0]}'" if raw_shown else ""
            add("TOKEN_OUTFLOW_NOT_SHOWN", f"{amt} {t.get('symbol') or a} leaves the signer, the screen never names this token{how}")
            continue
        bounds = s["tokens"].get(a, [])
        nums = [b for b in bounds if isinstance(b, Decimal)]
        if nums and "unbounded" not in bounds and amt is not None and max(nums) * Decimal("1.005") < amt:
            add("OUTFLOW_EXCEEDS_SHOWN_AMOUNT", f"{amt} {t.get('symbol')} leaves the signer, largest amount shown is {max(nums)}")

    if s["tokens"] or s["native"]:
        for a, raw in inflow.items():
            if raw and not named(a) and a not in out and not minted_only[a]:
                t = tok(a)
                add("INFLOW_TOKEN_NOT_SHOWN", f"signer receives {human(raw, t.get('decimals'))} {t.get('symbol') or a}, the screen names other assets but not this one")

    # amounts on screen that match a real movement but carry no unit, the wrong unit, or raw base units
    moves = [(a, raw) for a, raw in list(out.items()) + list(inflow.items())
             if raw and not a.startswith("_real_") and not (a in burned and ("_real_" + a) not in out) and not (a in inflow and minted_only[a] and a not in out)]
    if val:
        moves.append(("native", val))
    for f in s["fields"]:
        if (f.get("warning") or {}).get("code") in HARNESS_GAPS:
            stats["fields_skipped_missing_metadata"] += 1
            continue
        v = str(f.get("value", "")).replace(",", "").strip()
        m = AMOUNT.match(str(f.get("value", "")))
        if BARE.match(v) and AMOUNT_LABEL.search(f.get("label") or "") and not NOT_AMOUNT_LABEL.search(f.get("label") or ""):
            n = Decimal(v)
            if n >= Decimal(10) ** 40:
                add("UNLIMITED_SHOWN_AS_NUMBER", f"'{f.get('label')}' shows {v}, a sentinel or maximum shown as a plain number")
                continue
            for a, raw in moves:
                t = {"symbol": NATIVE.get(chain, "ETH"), "decimals": 18} if a == "native" else tok(a)
                d = t.get("decimals")
                if d and n == raw and raw >= 10 ** min(d, 4):
                    add("AMOUNT_IN_BASE_UNITS", f"'{f.get('label')}' shows {v}, the raw base-unit count of {human(raw, d)} {t.get('symbol') or a}, with no unit")
                    break
                if d is not None and n == human(raw, d) and n > 0 and not f.get("tokenAddress"):
                    add("AMOUNT_WITHOUT_UNIT", f"'{f.get('label')}' shows {v} with no unit; it is {v} {t.get('symbol') or a}")
                    break
        elif m and not f.get("tokenAddress"):
            n, sym = Decimal(m.group(1).replace(",", "")), m.group(2).strip().lower()
            same = []
            for a, raw in moves:
                t = {"symbol": NATIVE.get(chain, "ETH"), "decimals": 18} if a == "native" else tok(a)
                if t.get("decimals") is not None and n > 0 and n == human(raw, t["decimals"]) and t.get("symbol"):
                    same.append(t["symbol"])
            known = NATIVE_WORDS | {(tok(b).get("symbol") or "").lower() for b, _ in moves if b != "native"}
            if same and sym and sym in known and sym not in {x.lower() for x in same} and n >= Decimal("0.000001"):
                add("AMOUNT_WRONG_UNIT", f"'{f.get('label')}' shows {f.get('value')}, but the amount that moves is {n} {same[0]}")

    touched = {signer, to}
    for l in r["logs"]:
        touched.add(l["address"].lower())
        touched.update(taddr(t) for t in l["topics"][1:])
    for f in s["fields"]:
        if RECIPIENT_LABEL.search(f.get("label") or ""):
            for a in ADDR.findall((f.get("rawAddress") or "") + " " + str(f.get("value", ""))):
                a = "0x" + a.lower()
                padded = re.search(r"0x0{24}[0-9a-fA-F]{16}\b", str(f.get("value", "")))
                if a not in touched and int(a, 16) > 0xFFFF and not padded:
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

# which descriptor rendered each finding, and whether an auditor attested it
desc_of = collections.defaultdict(set)
for f in glob.glob(os.path.join(REG, "registry/*/calldata-*.json")):
    try:
        c = (json.load(open(f)).get("context") or {}).get("contract") or {}
    except Exception:
        continue
    for x in c.get("deployments", []):
        desc_of[(x.get("chainId"), x.get("address", "").lower())].add(os.path.relpath(f, REG))
REVIEW = {"INFLOW_TOKEN_NOT_SHOWN", "SHOWN_RECIPIENT_UNTOUCHED"}
for f in findings:
    f["descriptors"] = sorted(desc_of.get((f["chain"], f["to"]), ()))
    f["attested"] = any(d in attested for d in f["descriptors"])
    f["severity"] = "review" if f["kind"] in REVIEW else "defect"
defects = [f for f in findings if f["severity"] == "defect"]

summary = {
    "transactions": len(ev["receipts"]), **stats, "findings": len(findings),
    "defects": len(defects), "review": len(findings) - len(defects),
    "contracts_with_defects": len({(f["chain"], f["to"]) for f in defects}),
    "descriptors_with_defects": len({d for f in defects for d in f["descriptors"]}),
    "attested_descriptors_with_defects": len({d for f in defects for d in f["descriptors"] if d in attested}),
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
    for sev, title in (("defect", "Defects: the screen misstates what the transaction does"), ("review", "For review: often legitimate (bridges, confidential transfers, wrappers)")):
        fh.write(f"## {title}\n\n")
        for f in sorted((f for f in findings if f["severity"] == sev), key=lambda f: (f["kind"], f["to"])):
            fh.write(f"- **{f['kind']}** chain {f['chain']} contract {f['to']} ({', '.join(f['descriptors']) or 'no descriptor'}{', attested' if f['attested'] else ''}) tx {f['tx']}: {f['detail']}\n")
        fh.write("\n")
print(json.dumps(summary, indent=1))
