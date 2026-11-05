# Claude Code Mistral Connector

Run Claude Code with Mistral-hosted coding models through a local gateway. The
`claude-mistral` command signs in with Mistral Vibe's browser authentication and
translates between Claude Code's Messages API and Mistral chat completions.

```text
Claude Code → local gateway (127.0.0.1:auto-port) → Mistral
```

The connector uses Vibe's normal browser sign-in to provision a plan credential,
then stores it in the operating system keyring. Internally this credential is an
API key issued by Vibe's sign-in service; you do not create or paste a developer
API key. Existing developer keys and experimental OIDC sessions are not used.
Subscription access, usage limits and billing allocation are determined by Mistral;
a successful model request alone does not verify which allowance is charged.

## Features

- Select Mistral-hosted models in Claude Code's native `/model` menu.
- Change thinking independently with `/effort` during a session.
- Stream text and tool-call arguments, and translate tool results back to Mistral.
- Send images and documents, including images returned by Read and MCP tools.
- Translate JSON schema output and custom tools while preserving names and inputs.
- Support system instructions, including Claude Code's inline system messages.
- Read context and output limits from the live coding catalog, with published
  defaults for GLM 5.3, GLM 5.3 Flash and Mistral Large 4.
- Keep provider settings scoped to the launched Claude Code process.
- Authenticate local requests with an ephemeral secret and bind to loopback.

**Permission mode:** `claude-mistral` launches Claude Code with
`--permission-mode bypassPermissions` by default, so its tools can run without
permission prompts. To choose another mode for a launch, pass it after `--`.

## Requirements

- Linux or macOS with a browser and a working operating system keyring.
- Claude Code CLI on `PATH`; custom picker rows require version 2.1.242 or newer.
- Mistral Vibe CLI on `PATH`, with its browser sign-in modules available.
- `starlette`, `uvicorn` and `httpx2` in Vibe's Python environment.
- A Mistral account with access to the selected model.

Developed against Mistral Vibe 3.1.0 and Claude Code 2.1.318. Vibe's private browser sign-in
interfaces can change between releases. The launcher expects a Python-script Vibe
installation with a shebang pointing to its interpreter.

## Install

Clone this repository and enter its directory. If you use `uv`, install Vibe and
the gateway dependencies into the same tool environment:

```bash
uv tool install 'mistral-vibe==3.1.0' --with starlette --with uvicorn
```

If Vibe is already installed, make sure its Python environment contains the
requirements listed above. The launcher automatically uses that interpreter.

Create the command from the repository root:

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/cc-mistral" ~/.local/bin/claude-mistral
```

Ensure `~/.local/bin` is on your `PATH`. You can also run `./cc-mistral` directly
from the repository. Keep the checkout in place: the command links to its launcher.

## Quick start

```bash
claude-mistral login
claude-mistral
```

Inside Claude Code:

```text
/model
/effort high
/context
```

`/model` lists each enabled model once. `/effort` controls its reasoning level.
The router rejects unsupported effort values with the supported levels rather
than silently substituting a different setting.

| Model | Thinking levels |
| --- | --- |
| GLM 5.3 and GLM 5.3 Flash | low, high, max |
| Mistral Large 4 | low, medium, high |

Other models use reasoning settings advertised by the live catalog. The
`deepseek-v4-1-flash-1017` model is explicitly excluded from the connector.

## Commands

| Command | Purpose |
| --- | --- |
| `claude-mistral login` | Sign in through the browser |
| `claude-mistral login --no-browser` | Print the login URL for manual opening |
| `claude-mistral status` | Check for a browser-provisioned Vibe credential, offline |
| `claude-mistral models` | List enabled model IDs |
| `claude-mistral` | Launch Claude Code with the native model picker configured |
| `claude-mistral run --model MODEL_ID` | Choose the initial model explicitly |
| `claude-mistral run --model MODEL_ID --thinking high` | Set an initial effort level |

Use an exact ID from `models`. Effort remains changeable with `/effort`.
Each launch automatically reserves its own free loopback port, so multiple
terminals and worker processes can run concurrently. `run --port 3457` pins a
specific port instead. Pass Claude Code arguments after
`--`, for example:

```bash
claude-mistral run --model MODEL_ID -- --resume
claude-mistral run --model MODEL_ID -- --permission-mode plan
```

### Concurrent workers and an orchestrator

Run `claude-mistral` in each terminal; no manual port configuration is needed.
Each launch owns its gateway process, local authentication secret and upstream
session. Exiting one launch shuts down its gateway without stopping other workers.
An explicitly requested port that is already occupied fails with a clear error.

An orchestrator can launch independent noninteractive workers with ordinary
subprocesses. For example, these two workers run concurrently:

```bash
claude-mistral run -- -p "Review the API code; do not edit files" --output-format json > api-review.json &
claude-mistral run -- -p "Review the tests; do not edit files" --output-format json > test-review.json &
wait
```

The orchestrator should collect each process's stdout and exit status. Launcher
and gateway messages go to stderr, leaving stdout for Claude Code's output.
Claude Code arguments such as `--agent`, `--agents`, `--model` and `--worktree`
can be passed after `--`; use connector `--model` before `--` to select a Mistral
model. Independent workers can be started from a Claude Code parent session:
the launcher clears the inherited `CLAUDECODE` nesting marker only for its child.

This supports external orchestration; it does not automatically assign tasks or
collect worker results. Claude Code's own in-session subagents can share the
parent gateway. Keep the launcher running for the lifetime of its workers;
detached Claude processes such as `--background` outlive the launcher and are
not supported by this gateway lifecycle. Concurrent workers share account usage
limits. For workers that edit files, use separate worktrees or divide file
ownership to avoid overlapping edits.

The launcher chooses an earlier saved connector model when available, otherwise
the catalog recommendation. It chooses its starting model independently of the
default saved by Claude Code's `/model` command. In the picker, pressing `s`
switches for this session only; Enter lets Claude Code save its own default.

## How it works

1. Browser login uses Vibe's `BrowserSignInService` and PKCE. The service starts
   `/api/vibe/sign-in` at the Mistral console, polls for browser authorization,
   then exchanges the result for a Vibe credential. The connector stores that
   credential in its own OS keyring slot, `ai.claude-mistral.vibe-plan`. It does
   not overwrite Vibe's existing credentials or persist a key in `.env`.
2. The connector fetches `/v1/model-catalog?surface=vibe-code`, falling back to
   `/v1/models` only on HTTP 404 or 405. Enabled entries populate the native picker.
3. The launcher starts an authenticated gateway on an automatically assigned
   `127.0.0.1` port and passes
   its URL, an ephemeral local secret and model settings to Claude Code.
4. For each `/v1/messages` request, the gateway resolves the selected model and
   effort, translates messages and tools, and calls Mistral chat completions.
   The browser-provisioned credential supplies its workspace scope. A rejected
   credential requires a fresh `login`; there is no fallback to OIDC or a
   developer key.
5. Responses are translated back to Anthropic Messages JSON or SSE events.
   The gateway terminates when the launched Claude Code process exits.

Credentials are not written into the repository. The local gateway secret is not
a Mistral API key. Upstream error bodies are redacted, and HTTP 402/429 responses
stop the request without trying another provider or account.

Completion requests carry `metadata.call_source = "vibe_code"`, the Vibe SDK
User-Agent with an additional connector identifier, and stable session affinity.
These fields describe request attribution, not a server-confirmed billing result.
The actual endpoint remains `/v1/chat/completions`, as in Vibe itself, so a request
being called a completion does not by itself identify its allowance.

### Migrating from experimental OIDC login

Earlier connector versions used experimental OIDC authentication. Adding Vibe
metadata to those requests did not resolve the reported API-allowance usage.
The connector now requires Vibe's normal browser-provisioned credential:

```bash
claude-mistral login
claude-mistral
```

Existing OIDC sessions are left untouched but are no longer used by this connector.
Mistral's [plan-key documentation](https://help.mistral.ai/en/articles/698531-why-am-i-hitting-api-rate-limits-and-how-do-i-increase-them)
distinguishes Vibe plan budgets from general API credentials. The new flow follows
Vibe's own browser credential provisioning; billing still needs live verification.

Note both the API and Vibe Code usage counters, stop other activity, sign in again,
send a small test, and compare the counters after the dashboard updates. Rounded
counters may not visibly change for a tiny request. The automated tests verify the
credential flow and request contents, not server-side billing allocation.

## Context and output limits

Live catalog limits take precedence. Missing GLM 5.3, GLM 5.3 Flash and Mistral
Large 4 context values use their published 1M window. Missing GLM output limits
use 128,000 tokens. Requests may specify a smaller output; requests above a known
native output limit are rejected.

Exact 1,000,000-token windows use Claude Code's `[1m]` suffix, which is stripped
before forwarding. Other numeric windows, including 1,048,576, use the exact
`CLAUDE_CODE_MAX_CONTEXT_TOKENS` override. The launcher also sets
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` when the output limit is known. The tested Claude
Code version accepts output overrides up to 128,000 tokens.

Environment overrides are process-wide. If you switch to a model with a different
non-1M window or output ceiling, restart with `--model MODEL_ID` to update the
client's overrides. The gateway applies output limits per request. Automatic
compaction remains enabled, and `/context` shows the effective client window.

## Images, documents and tool compatibility

Image URL and base64 blocks are translated to Mistral's image content chunks,
including images inside tool results. JPEG, PNG, GIF and WebP are accepted by
the router; the selected model determines which formats it can process. Text
and PDF document blocks are translated to text or document URL chunks. Media
support and document processing depend on the selected model and entitlement.
If the catalog explicitly marks a model as lacking vision, image requests return
an error asking you to choose a vision-capable model with `/model`. The router
does not switch models automatically.

Claude Code executes its local tools. The gateway translates their definitions,
calls and results to the chat-completions format used by Vibe; it does not run
a separate Vibe agent or rename Claude tools to Vibe tools. Tool names, input
schemas, strict settings, parallel-call settings and error results are preserved.
Long Claude tool-call IDs are consistently mapped to Mistral-compatible IDs in
history so results stay paired with their calls.

The launcher sets `ENABLE_TOOL_SEARCH=false` for its Claude Code process to load
MCP tool definitions upfront. Mistral does not provide Anthropic's hosted tool
search service. Tool references from resumed custom-tool searches are represented
as available-tool text, with custom definitions forwarded eagerly. Loading all
MCP tools can use more context. See [Claude Code tool-search configuration](https://code.claude.com/docs/en/mcp#configure-tool-search).

JSON schema formats in `output_config.format` (or legacy `output_format`) map to
Mistral's `response_format`. Native `/model`, `/effort`, and Claude Code permission
modes continue to work independently. See Mistral's [vision](https://docs.mistral.ai/inference/conversations/vision)
and [structured-output](https://docs.mistral.ai/inference/conversations/structured-output/custom) documentation.

Restart `claude-mistral` after updating to use the new routing and launcher settings.

## Limitations and troubleshooting

- Live chat has been exercised, but automated tests use simulated upstreams.
  Subscription allowance deductions are not independently verified.
- Anthropic-hosted tools, audio/video blocks and Anthropic file-ID media sources
  are unsupported. PDF processing requires upstream document support; the gateway
  does not implement local OCR. Requests remain limited to 16 MiB including base64.
- Thinking blocks are omitted from history, and structured upstream thinking is
  excluded from answer text. Prompt-cache directives are not translated.
- Exact token counting, remaining-allowance tracking and some Claude-specific
  capabilities are not implemented. A model's self-description is not proof of
  which upstream answered.
- A stored session reported by `status` does not prove it is still valid online.
  Retry `login` if Mistral rejects or cannot refresh it.
- Login requires network access and a usable keyring. There is no plaintext
  credential-storage or developer-key fallback.
- Ports are allocated automatically. If an explicit `--port` is occupied, omit
  it or choose another number.
- If an updated Vibe release removes the private browser sign-in interfaces, use the tested
  version or update the adapter.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout, test commands and
contribution guidelines. Commits and pull requests should not add automatic
co-author or assistant attribution trailers.

## References

- [Claude Code Router](https://github.com/musistudio/claude-code-router): gateway architecture reference.
- [Mistral Vibe](https://github.com/mistralai/mistral-vibe): authentication implementation.
- [Claude Code model configuration](https://code.claude.com/docs/en/model-config): native picker, effort and context settings.
