# Registry scan, 20 October 2026

Registry commit [`99e0f02`](https://github.com/ethereum/clear-signing-erc7730-registry/commit/99e0f02988b9d7f8e565e1cecbd43b89247c1a42). Evidence: `results/evidence.json.gz`. Reproduce with the commands in the README.

## Summary

- 1,682 real transactions collected (405 from the registry's own test files, the rest sampled from Blockscout for every registry deployment on Ethereum, Optimism, Base and Arbitrum).
- 942 of them hit a function that has a descriptor and were compared with their receipts. 694 hit a function with no descriptor (the wallet falls back to raw calldata), which is outside this check.
- **7 descriptors, covering 15 deployed contracts, rendered a screen that misstates what 45 transactions did.** None of the 7 is attested in `sigs/`. A search of the 66 open registry PRs on 20 October found no open fix for any of them.
- 441 of 1,017 registry deployments are upgradeable proxies (EIP-1967, EIP-1822 or beacon). 0 of them declare the `proxy` or `stateRefs` precondition the auditor guide asks for. 44 of them are covered by an attested descriptor.

Every finding below was confirmed by hand against the descriptor and the transaction.

## Defects

| Descriptor | Contracts | Txs | What the screen shows | What the transaction did | Example |
|---|---|---|---|---|---|
| `kiln/calldata-kiln-batch-deposit-v2.json` | 1 | 1 | "Stake 32ETH per validators" and a blob of validator keys; no amount | Sent **2,016 ETH** (63 validators). The descriptor was changed in the Cyfrin audit pass on 25 Sept (#3017); the total is still not shown | [tx](https://etherscan.io/tx/0x9133c45b46aa9fc0fe8a068be384f52b39088189256d5e26240e4c8e48b685eb) |
| `midas/calldata-RedemptionVault.json` | 6 (mTBILL, mHYPER, mEDGE, mHyperBTC, mMEV, mROX) | 19 | "mToken Amount In: 4649.69 " (the `unit` format with a blank base) | 4,649.69 mTBILL left the signer; the screen never says which token | [tx](https://etherscan.io/tx/0x10b62bdac96f794700c6911c33916864d99a982f1106c026c92b48b6b99931cf) |
| `zama/calldata-ConfidentialWrapper.json` | 4 (USDC, USDT, ZAMA, tGBP wrappers) | 12 | "Amount (underlying units): 150000000000" | **150,000 USDC** left the signer; the screen shows base units and never names USDC | [tx](https://etherscan.io/tx/0x035d850c730392e72fc7c83173d9e470096899c7efb3b799638b7c0704fb92dc) |
| `1inch/calldata-AggregationRouterV5.json` | 1 | 5 | "Amount to Send: 56580000000000000", "Minimum to Receive: 785359752206855742423040" | 0.05658 WETH left the signer; both amounts are raw integers and neither token is named (the V3 and V4 router descriptors are attested, V5 is not) | [tx](https://etherscan.io/tx/0x037602ae494c23cde3ff72f8a367ee4836be675b5d36d0291f9d21353aa177e2) |
| `opencover/calldata-Quote.json` (Optimism) | 1 | 5 | "Premium amount: 5651230", "Cover amount: 1446696280" | Premium of 5.65123 USDC paid; cover of 1,446.69 USDC. Both shown in base units | [tx](https://optimistic.etherscan.io/tx/0xc56900e0f9d3023032e808eed5ae86e9bbb40b813743612702931c6df9490220) |
| `okx/calldata-OkxDexRouterV1.0.7-multi-commission.json` (Optimism) | 1 | 2 | "Amount to Send: 0.009516617932157369 ETH" | 0.009598202654722511 ETH sent; the 0.86% commission is added on top and not shown | [tx](https://optimistic.etherscan.io/tx/0x276a79115f660935a6fc047720e15b042998003555775111fcb891bfbea3ba38) |
| `glue/calldata-GlueHook.json` (Optimism) | 1 | 1 | Pool parameters and "Liquidity units: 9937383358860903" | 1.596 OP left the signer; the only OP amount on the screen is "Min main: 0 OP" | [tx](https://optimistic.etherscan.io/tx/0x835bac72f9bdc6444ad337d2560c18c43015f32d9f1414ff35c405c265851066) |

Two more were flagged and are not counted, because the screen names the asset elsewhere or makes no claim: `midas/calldata-MinterVault.json` shows "Amount: 16983.000045" with no unit, but the paid token is shown in the field above it, and `morpho/calldata-MorphoBundlerV3.json` shows each bundled action as raw hex, so the screen does not claim a meaning (an NFT, #35 of 0xd4a3ac77ffe2220f2727c26234b00e5223bad943, left the signer in [this tx](https://etherscan.io/tx/0x26b0b0aa2a82a9e55496ce19bac032dfd32716ea5ac125200832046db4c73d9a)).

## Status (24 October)

- Fixed, pull requests submitted: Kiln (show `@.value` as "Total ETH sent"), OKX (show `@.value` as "Total sent with fees" when not zero).
- Not fixable inside ERC-7730 as written, reported to the registry: 1inch V5 `uniswapV3Swap` (tokens come from the pool, not calldata), Midas RedemptionVault (the mToken is the vault's own constant, across 78 deployments in one file), Zama wrappers (underlying decimals differ across 18 deployments in one file), OpenCover (assets are enum IDs with different decimals), Glue (amounts follow from liquidity and price). The auditor guide's rule is to omit a function whose meaning the descriptor cannot express, or to split the file per deployment.

Suggested fixes, in registry terms: use `tokenAmount` with a `tokenPath` (or a per-deployment `token` constant) instead of `unit` or `raw` for amounts (Midas, Zama, 1inch V5, OpenCover, Glue); show `@.value` with the `amount` format (Kiln, OKX); and decode the bundled call instead of rendering it raw (Morpho Bundler).

## Audit replay

`scan/replay.sh` renders the same transactions with the registry before and after each `fix(...)` commit (21 fixes, 16 of which had real transactions in the evidence).

- `fix(flyingtulip): Cyfrin Audit name the paid token, render amounts` (#3026): the checker flagged all 9 affected screens before the fix (amounts shown as raw base units, such as "FT amount: 20000000000000000000000") and none after it. It caught the defect with no human input.
- The other fixes changed things the sampled transactions did not exercise (unlimited allowances, a zero receiver, native outputs) or things outside these checks (labels, decoded withdrawal credentials, enum names, EIP-712 domains). Targeted sampling for each audited function is on the roadmap.
- The Kiln batch-deposit descriptor is in both lists: its audit fix (#3017) decoded the withdrawal credentials, and the checker still flags the missing ETH amount after it.

## For review, not counted above

166 findings are listed in `results/current/findings.md` under "For review". They are usually legitimate: bridges whose recipient is on another chain, Zama confidential transfers (which emit no ERC-20 Transfer), Safe creation with a zero payment, and wrappers whose minted token the screen describes in words. They stay in the output because a wrong recipient is the most costly mistake a screen can make, and a person should look at each new one.

## Limits

- Transactions are sampled (the registry's tests plus up to 5 recent calls per function per contract), so a clean result means "no defect in the sampled calls", not "no defect".
- Token symbols and decimals come from the token contracts. A field whose token the scan could not resolve is skipped, not flagged.
- EIP-712 messages have no receipts and are not checked yet.
