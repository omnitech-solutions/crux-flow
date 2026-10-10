# The public contract, on worked examples

Each operation is shown with what goes in, what comes out, what it changes and how it fails.
Where a name here differs from the code, the code is wrong or this document is stale; neither is
allowed to stand. The examples use invented data.

Status: **proposed**. The decisions behind each section are ADR-0001 to ADR-0014.

## 0. Constructing the engine

```ts
import { createAiEngine } from "@omnitech/ai-engine";

const engine = createAiEngine({
  profiles,              // data: id -> provider, model, capabilities, limits, default parameters
  providers,             // adapters, built by the host from its own credentials
  runtimes,              // agent runtime adapters (worker tier only)
  authorize,             // (execution, profile) => allow | refuse with a reason
  jobs,                  // optional: a job store on the host's database
  trace,                 // optional: where interaction records go
  code,                  // optional: a code runner and its execution profiles
  log,                   // optional: level, sink, form and context of the engine's own logger
});
```

- Constructing reads no environment variable, opens no connection and starts no timer.
- There is one entry point (ADR-0032). A provider is named, never imported by path:

```ts
import { agentRuntime, createAiEngine, modelProvider } from "@omnitech/ai-engine";

const providers = {
  anthropic: modelProvider({ kind: "anthropic", apiKey }),
  claude: agentRuntime({ runtime: "claude-code", environment }),
};
```

  The runtime's SDK is loaded on its first run, so a host that never runs one does not need it.
- Every operation takes an **execution**: who is asking and how to stop.

