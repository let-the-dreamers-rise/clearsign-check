# clearsign-check

Checks that ERC-7730 clear-signing descriptors tell the truth.

Wallets that support clear signing (Ledger, Trezor and others) render a transaction with a descriptor from the [Ethereum Foundation registry](https://github.com/ethereum/clear-signing-erc7730-registry). If the descriptor is wrong, the wallet shows a confident, readable screen that does not match what the transaction does. clearsign-check finds those screens deterministically: it replays real transactions, renders each one with the reference library ([@ethereum-sourcify/clear-signing](https://www.npmjs.com/package/@ethereum-sourcify/clear-signing)), and compares the screen with the onchain effects in the receipt. No language model is in the loop.

## What it flags

| Check | Meaning |
|---|---|
| `NATIVE_VALUE_NOT_SHOWN` | The transaction sends ETH (or the chain's native coin), and no field shows a native amount |
| `TOKEN_OUTFLOW_NOT_SHOWN` | A token leaves the signer, and the screen never names that token |
| `OUTFLOW_EXCEEDS_SHOWN_AMOUNT` | More of a token leaves the signer than any amount on the screen |
| `APPROVAL_SPENDER_NOT_SHOWN` | The signer approves a spender the screen never shows |
| `APPROVAL_FOR_ALL_NOT_SHOWN` | The signer lets an operator move every NFT in a collection, and the screen never shows the operator |
| `NFT_OUTFLOW_NOT_SHOWN` | An NFT leaves the signer, and the screen never shows its collection |
| `AMOUNT_IN_BASE_UNITS` | A field shows an amount that moves as a raw base-unit integer (150000000000 for 150,000 USDC) |
| `AMOUNT_WITHOUT_UNIT` | A field shows an amount that moves with no token or currency next to it |
| `AMOUNT_WRONG_UNIT` | A field shows an amount that moves labelled with a different asset |
| `UNLIMITED_SHOWN_AS_NUMBER` | A field shows a maximum or sentinel value as a 40+ digit number |
| `SHOWN_RECIPIENT_UNTOUCHED` | For review: a field labelled as a recipient shows an address the transaction never touches |
| `INFLOW_TOKEN_NOT_SHOWN` | For review: the signer receives a token the screen never names |

It also lists every registry deployment that is an upgradeable proxy (EIP-1967, EIP-1822, beacon), along with whether its descriptor declares the `proxy` or `stateRefs` precondition that the registry's [auditor guide](https://github.com/ethereum/clear-signing-erc7730-registry/blob/master/auditors/README.md) asks for.

Burns of receipt tokens, a wrapped token pulled for the same amount of its underlying, and fields whose token metadata could not be resolved are skipped, not flagged. The latest hand-checked results are in [reports/](reports/).

## Audit replay

`scan/replay.sh` renders the same real transactions with the registry before and after each `fix(...)` commit. A finding that appears before a fix and is gone after it means the checker flagged the defect the fix removed, with no human input. The registry's September 2026 "Cyfrin Audit" fixes are the ground truth.

## Run it

The [Registry scan](.github/workflows/scan.yml) workflow runs it weekly against public RPC nodes and Blockscout, then commits the results to `results/`. To run it locally (Node 22, Python 3.12):

```sh
npm ci --ignore-scripts
git clone https://github.com/ethereum/clear-signing-erc7730-registry registry
node scan/fetch.mjs registry evidence.json
node scan/render.mjs registry evidence.json rendered.json
python3 -I scan/analyze.py registry evidence.json rendered.json results/current
bash scan/replay.sh registry evidence.json . results/replay
```

## License

MIT
