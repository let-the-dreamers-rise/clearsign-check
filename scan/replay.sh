#!/usr/bin/env bash
# Audit replay: for each "Cyfrin Audit" fix commit in the registry, render the
# same real transactions with the registry before and after the fix, and report
# which findings the fix removed. A finding present before and gone after means
# the checker flagged the defect the auditor fixed, with no human input.
# Usage: replay.sh <full registry clone> <evidence.json> <render dir with node_modules> <out dir>
set -euo pipefail
REG=$(realpath "$1"); EV=$(realpath "$2"); RENDER=$(realpath "$3"); OUT=$(realpath -m "$4")
HERE=$(dirname "$(realpath "$0")")
mkdir -p "$OUT"
cp "$HERE/render.mjs" "$RENDER/render.mjs"
git -C "$REG" log --format="%H %s" | grep -E "^[0-9a-f]+ fix\(" | while read -r sha subject; do
  # transactions to the contracts whose descriptors this fix touched
  only=$(git -C "$REG" show --name-only --format= "$sha" | { grep -E '^registry/[^/]+/(calldata|common)-[^/]*\.json$' || true; } | while read -r f; do
           git -C "$REG" show "$sha:$f" 2>/dev/null || git -C "$REG" show "$sha^:$f" 2>/dev/null; echo; done |
         python3 -I -c '
import json, sys
raw = sys.stdin.read()
deps = set()
dec = json.JSONDecoder(); i = 0
while i < len(raw):
    while i < len(raw) and raw[i].isspace(): i += 1
    if i >= len(raw): break
    try: d, i = dec.raw_decode(raw, i)
    except Exception: break
    for x in ((d.get("context") or {}).get("contract") or {}).get("deployments", []):
        deps.add((x.get("chainId"), x.get("address", "").lower()))
ev = json.load(open(sys.argv[1]))
print(",".join(h for h, r in ev["receipts"].items() if (r.get("chain"), (r.get("to") or "").lower()) in deps))
' "$EV")
  [ -z "$only" ] && continue
  for side in before after; do
    rev=$([ "$side" = before ] && echo "$sha^" || echo "$sha")
    wt="$OUT/wt-$side"
    rm -rf "$wt"; git -C "$REG" worktree prune
    git -C "$REG" worktree add -q --detach "$wt" "$rev"
    (cd "$RENDER" && node render.mjs "$wt" "$EV" "$OUT/$side.json" "$only" 2>/dev/null)
    python3 -I "$HERE/analyze.py" "$wt" "$EV" "$OUT/$side.json" "$OUT/$side" > /dev/null
    git -C "$REG" worktree remove --force "$wt"
  done
  python3 -I - "$OUT" "$sha" "$subject" <<'EOF'
import json, sys
out, sha, subject = sys.argv[1:4]
key = lambda f: (f["tx"], f["kind"], f["detail"])
b = {key(f): f for f in json.load(open(f"{out}/before/findings.json"))["findings"]}
a = {key(f) for f in json.load(open(f"{out}/after/findings.json"))["findings"]}
gone = [b[k] for k in b if k not in a]
with open(f"{out}/replay.md", "a") as fh:
    fh.write(f"\n## {sha[:7]} {subject}\n\n")
    fh.write(f"findings before {len(b)}, after {len(a)}, removed by the fix {len(gone)}\n\n")
    for f in gone:
        fh.write(f"- {f['kind']} chain {f['chain']} {f['to']} tx {f['tx']}: {f['detail']}\n")
EOF
done
echo "wrote $OUT/replay.md"
