# Versioning

`@omnitech/ai-engine` follows semantic versioning. One version covers the whole package: every
entry point, the contracts, the providers and (from phase 6) the stored tables. `bionic/research/references/contract.md`
is the specification a version describes.

## What a consumer depends on

| Surface | Where it is defined |
|---|---|
| Entry points | `exports` in `packages/ai-engine/package.json` |
| Exported names | What the entry point's `index.ts` and `browser.ts` export |
| Shapes | Inputs, outputs, stream parts and terminals, `Failure` and its codes |
| Configuration keys | `EngineConfig`, `Profile`, each provider's options |
| Stored tables | The engine's schema and migrations (phase 6) |

A change is judged by what it does to a consumer that compiles and runs against the previous
version.

## Breaking (major)

- Removing or renaming an entry point or an exported name.
- Removing or renaming a field, changing its type, or making an optional input required.
- Removing a value a consumer may send, or adding a value a consumer must handle: a new stream
  part type, a new terminal, a new failure code, a new usage status.
- Changing what a failure code means, or whether it is `retryable`.
- Changing behaviour the contract states: one terminal part, one repair turn, what usage is the
  sum of, what `authorize` is asked.
- Removing or renaming a configuration key, or changing a default so that the same configuration
  behaves differently.
- A migration that drops or rewrites a column or table, or needs the host to act before upgrading.
- Making the browser-safe half of the entry point (`browser.ts`) reach a Node-only module or
  a provider SDK, or raising the required Node version.

## Additive (minor)

- A new entry point, exported name or operation.
- A new optional input field or configuration key whose absence keeps today's behaviour.
- A new field on an output.
- A new provider, runtime or profile capability.
