# Contributing

Keep changes focused, explain the behavior they change, and include enough
validation for another contributor to reproduce the result.

## Local setup

Install Claude Code and Mistral Vibe as described in [README.md](README.md).
The gateway must run with the interpreter belonging to the Vibe installation,
with `starlette`, `uvicorn` and `httpx2` available. No account credentials are
needed for the automated tests.

For a `uv tool` installation, locate the environment with:

```bash
uv tool dir
```

Run the tests using the Python executable in that directory's
`mistral-vibe/bin/python` environment:

```bash
/path/to/mistral-vibe/bin/python -m unittest discover -s tests -v
```

The tests simulate Mistral using an in-process ASGI client and mock transport.
They do not make model requests or spend subscription usage.

## Project layout

| File | Responsibility |
| --- | --- |
| `cc-mistral` | Locate Vibe's interpreter and run the CLI |
| `ccmistral/__main__.py` | Commands, catalog loading and Claude Code launch |
| `ccmistral/auth.py` | Vibe browser credential provisioning and keyring storage |
| `ccmistral/catalog.py` | Native model picker entries and context configuration |
| `ccmistral/model_limits.py` | Published fallback limits and excluded models |
| `ccmistral/thinking.py` | Model-specific reasoning levels and request effort |
| `ccmistral/protocol.py` | Messages, tools and SSE translation |
| `ccmistral/server.py` | Authenticated local gateway and upstream requests |
| `ccmistral/picker.py` | Legacy terminal picker and saved-selection helpers |
| `tests/` | Protocol, gateway and launcher regression tests |

## Implementation guidelines

- Provision credentials through Vibe's browser sign-in service and store them
  only in the connector's OS keyring slot. Do not add credential scraping,
  plaintext storage, developer-key fallback or experimental OIDC fallback.
- Preserve loopback binding, local request authentication and upstream error
  redaction. Never log tokens, authorization headers or complete private prompts.
- Keep native `/model` and `/effort` controls independent.
- Prefer current catalog values to published fallback values. Document any
  limitation in mapping a provider limit to Claude Code's client settings.
- Reject unsupported request shapes or settings explicitly. Do not silently
  switch providers, accounts, effort levels or models.
- Add regression tests for protocol changes and bugs. Test tool round trips and
  streaming behavior when those paths change.
- Do not count a simulated test as proof of live login, model entitlement or
  billing allocation. Label live checks clearly and use your own account.
- Update the README when commands, dependencies or user-visible behavior change.

## Before submitting

1. Run the automated tests with Vibe's Python interpreter.
2. Check the diff for credentials, local runtime files and unrelated changes.
3. Explain the problem, resulting behavior, validation and known limitations in
   your pull request.

Use concise commit messages. Do not add `Co-authored-by` trailers, generated-by
footers or assistant attribution to commits or pull requests. This repository's
Claude Code settings disable automatic commit and PR attribution.

For bug reports, provide the command, relevant versions, expected and actual
behavior, and a redacted error or minimal request shape. Never include account
tokens, keyring data or private source code without authorization.
