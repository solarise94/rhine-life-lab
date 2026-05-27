# Non-Interactive CLI Executor Compatibility Plan

## Purpose

Blueprint needs a compatibility layer for multiple coding-agent CLIs while keeping the existing run contract stable.

The compatibility layer should make `pi`, `opencode`, `codex`, and Claude Code (`cc` / `claude_code`) look like the same kind of executor to the backend:

1. Backend creates a task packet, prompt, adapter contract, run directory, and result directory.
2. A wrapper starts a non-interactive CLI command.
3. The CLI reads the prompt and writes files under the current run namespace.
4. The wrapper captures stdout/events, validates or repairs the manifest, and leaves evidence for backend validation/review.
5. The backend remains the only component that mutates Blueprint project truth.

This plan focuses on CLI compatibility. It does not change the Manager sidecar architecture.

## Non-Interactive Baseline

All real executor adapters should use non-interactive CLI mode.

Current `pi` behavior is the baseline:

- `PiWorkerAdapter` launches through `AgentCliWorkerAdapter`.
- The configured `BLUEPRINT_PI_COMMAND` receives `{executor_prompt_path}`.
- The bundled `scripts/blueprint_pi_launch.sh` runs `pi -p "@${prompt_path}"` with `--no-session`, `--no-skills`, and `--no-context-files`.
- `agent_cli_executor.py` captures output, writes `agent_trace.json`, validates `manifest.candidate.json`, and promotes it to `manifest.json`.

The same wrapper path should be used for `opencode`, `codex`, and `claude_code`.

## Runtime Boundary

The existing `bwrap` design stays in place.

Blueprint uses a soft sandbox:

- Host root is mounted read-only.
- Current run directories are writable.
- Project control paths such as `.git/` and `graph/` are masked.
- `HOME`, `XDG_*`, caches, and tool state default to run-local paths.
- Host networking remains available when task policy allows real agent execution.

Native CLI login directories may be visible through the read-only host root in `cli_native` mode. The wrapper must not write to those directories. If a native login token needs refresh and the directory is read-only, the run should fail with a clear setup error telling the operator to refresh the CLI login on the host.

## Authentication Modes

Each executor profile has an explicit authentication mode.

```text
auth_mode:
  cli_native
  project_api
```

### `cli_native`

Use the CLI's existing host-side login state or native provider configuration.

Rules:

- Do not inject project API keys.
- Do not set provider auth environment variables such as `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CODEX_API_KEY`, or provider-specific equivalents.
- Do not generate auth-bearing provider config files.
- Let the CLI resolve its own login or provider configuration from the read-only host environment.
- Record in `agent_trace.json` that the run used `cli_native`, without copying token material.

This mode is intended for native subscription accounts or preconfigured local CLI accounts.

### `project_api`

Use Blueprint project-managed API configuration for the run.

Rules:

- Inject only the API settings selected by the executor profile.
- Generate run-scoped provider config where the CLI requires config files.
- Redact keys in command logs, traces, and timelines.
- Avoid reading native auth directories when possible, so native subscription accounts and project API credentials are not mixed.
- Record provider, model, base URL, and auth mode in `provider_config_plan.json`.

## Provider Support Matrix

Initial support should be intentionally uneven. Some combinations are useful now; others should be blocked until the protocol mapping is clear.

| Worker | `cli_native` | `project_api` | Initial project API protocol |
| --- | --- | --- | --- |
| `pi` | Supported | Supported | Pi/DeepSeek current config |
| `opencode` | Supported | Supported | OpenCode provider config, OpenAI-compatible or provider-native |
| `claude_code` / `cc` | Supported | Not supported initially | Native Claude Code login only |
| `codex` | Supported | Not supported initially | Deferred OpenAI-compatible layer |

### Pi

`pi` supports both project API injection and native Pi login:

- `scripts/blueprint_pi_launch.sh` injects DeepSeek API key, model, and base URL.
- Run-local Pi state is configured through `PI_CODING_AGENT_DIR` and `PI_CODING_AGENT_SESSION_DIR`.
- `cli_native` does not inject a project API key and points Pi at the host-side `~/.pi/agent` auth directory when available.

Project API remains the recommended default for repeatable deployment. Native mode is for hosts that already logged into Pi through OAuth or a local auth file.

### OpenCode

OpenCode is the easiest target after `pi`.

`cli_native`:

- Run `opencode run` or equivalent non-interactive mode.
- Do not set API-key env.
- Let OpenCode read its host-side auth/provider configuration through the read-only host root.

`project_api`:

- Generate run-scoped `opencode.json` or use `OPENCODE_CONFIG_CONTENT`.
- Inject selected provider/base URL/model/API key.
- Support OpenAI-compatible and provider-native config first.

### Claude Code / CC

Claude Code is native-only in the first implementation.

`cli_native`:

- Run `claude -p` or equivalent non-interactive print mode.
- Do not set `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or base URL overrides.
- Use the existing host-side Claude Code login state.

`project_api`:

- Block initially.
- Do not inject `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, base URL, or generated provider config.
- Revisit only if there is a documented, stable non-interactive Claude Code API-key path that does not break native subscription login.

### Codex

Codex is native-only in the first implementation.

`cli_native`:

- Run `codex exec` or equivalent non-interactive command.
- Prefer JSON/JSONL output mode when available.
- Do not inject project API env or generated provider config.
- Let the host Codex CLI use its existing ChatGPT/Codex login or local configuration.

`project_api`:

- Block initially.
- The future implementation needs a dedicated OpenAI-compatible renderer for Codex config, provider, model, base URL, and credential injection.
- Do not route Codex through the Claude/Anthropic compatibility path.

## Profile Model

Introduce executor profiles rather than overloading `worker_type`.

Suggested backend model:

```json
{
  "profile_id": "codex-native",
  "display_name": "Codex native login",
  "worker_type": "codex",
  "auth_mode": "cli_native",
  "enabled": true,
  "command": null,
  "api_protocol": null,
  "provider_id": null,
  "model": null,
  "base_url": null,
  "permission_preset": "workspace_write",
  "native_auth_readonly": true
}
```

For project API profiles:

```json
{
  "profile_id": "opencode-project-api",
  "display_name": "OpenCode via project API",
  "worker_type": "opencode",
  "auth_mode": "project_api",
  "enabled": true,
  "api_protocol": "openai_compatible",
  "provider_id": "openai",
  "model": "gpt-4o",
  "base_url": "https://api.openai.com/v1",
  "credential_ref": "project:openai_api_key",
  "permission_preset": "workspace_write",
  "native_auth_readonly": false
}
```

Secrets must not be returned to frontend clients. The public API should return only configured status and non-secret metadata.

## Wrapper Design

The existing `agent_cli_executor.py` should remain the shared execution wrapper.

Add provider-specific renderers behind it:

```text
backend/app/workers/provider_renderers/
  base.py
  pi.py
  opencode.py
  claude_code.py
  codex.py
```

Each renderer returns:

- command argv
- environment overlay
- run-scoped config file paths
- redacted config summary
- unsupported-mode errors

The wrapper continues to own:

- stdout capture
- `BP_EVENT` passthrough
- output timeline
- manifest validation
- manifest repair prompt generation
- provider attempt trace
- command redaction

## UI Requirements

Runtime/settings UI should expose executor profiles.

Minimum controls:

- Executor: `Pi Agent`, `OpenCode`, `Claude Code`, `Codex`
- Authentication: `Use local CLI login` or `Use project API configuration`
- Provider/model/base URL fields only when `project_api` is selected
- Disabled message for `Claude Code + project_api`: `Claude Code project API injection is not supported`
- Disabled message for `Codex + project_api`: `Codex project API mode is not implemented yet`
- Status checks: CLI present, auth mode configured, project API key configured if needed

Card run UI should let the user select an executor profile per card/run while keeping `pi` as the default until broader support is stable.

The run summary should display:

- worker type
- auth mode
- provider/model/base URL when non-secret
- outer sandbox status
- whether native auth directories were only read through host root

## Permission Translation

Blueprint remains the source of truth for permissions.

Outer policy:

- `bwrap` controls writable paths.
- Host root stays read-only.
- Run/result/generated-script directories are writable.
- `.git/` and `graph/` remain masked.

Provider-native policy is a second layer:

- `pi`: continue using the existing prompt and run-local state approach.
- `opencode`: translate to OpenCode permission config or env.
- `claude_code`: translate to permission mode and allowed/disallowed tools where possible.
- `codex`: translate to Codex sandbox/approval settings where possible.

Native permissions must not be treated as the security boundary. If native permissions and Blueprint permissions conflict, the stricter effective behavior should win or the run should fail before launch.

## cc-switch Position

`cc-switch`-style behavior is useful as a reference for profile switching, but the first Blueprint implementation should not shell out to a global switcher or rewrite user-global CLI config.

Blueprint should implement a run-scoped switcher:

- It builds per-run config overlays.
- It records the selected profile in the run trace.
- It does not mutate `~/.claude`, `~/.codex`, or OpenCode global config.
- It works with concurrent runs.
- It works under the existing read-only host-root `bwrap` profile.

A future optional integration can import profile presets from a `cc-switch`-style config, but execution should still use Blueprint-owned run-scoped renderers.

## Implementation Phases

### Phase 1: Profile API and UI Skeleton

- Add executor profile model and project settings API.
- Keep `pi` as the default profile.
- Re-expose `opencode`, `claude_code`, and `codex` through profiles instead of raw worker capability list.
- Add UI controls for profile selection and auth mode.
- Add validation blocks for unsupported combinations.

Acceptance:

- UI can show `codex cli_native`, `claude_code cli_native`, `opencode cli_native`, and `opencode project_api`.
- `codex project_api` and `claude_code project_api` are rejected with clear reasons.
- Existing `pi` runs still behave as before.

### Phase 2: Shared Renderer Path

- Move `codex` to `AgentCliWorkerAdapter`.
- Add renderer registry.
- Render provider command/config from selected profile.
- Write `provider_config_plan.json` into the run directory.
- Preserve current `agent_trace.json` and manifest behavior.

Acceptance:

- Stub commands for all three providers can receive the prompt path and write a valid manifest.
- Commands and traces redact secrets.
- `project_api` and `cli_native` produce different env/config plans.

### Phase 3: OpenCode Real Support

- Implement OpenCode non-interactive command rendering.
- Implement `cli_native` without auth injection.
- Implement `project_api` with generated run-scoped config.
- Add status checks for CLI availability and project API key presence.

Acceptance:

- OpenCode native mode can use host-side config through read-only host root.
- OpenCode project API mode can run without host-side auth.

### Phase 4: Claude Code Real Support

- Implement Claude Code non-interactive command rendering.
- Implement native mode without Anthropic env injection.
- Reject project API mode before launch.

Acceptance:

- Claude Code native mode does not set project API env.
- Claude Code project API mode does not inject credentials or generated provider config.
- Project API Claude Code mode is not silently attempted.

### Phase 5: Codex Native Support

- Implement Codex non-interactive command rendering.
- Implement native mode without project API injection.
- Prefer structured output mode for event capture when available.
- Block project API mode.

Acceptance:

- Codex native mode launches through the same wrapper and manifest contract.
- Codex project API mode fails validation before launch with a clear message.

### Phase 6: Codex Project API Layer

Deferred.

Build only after native Codex support is stable.

Required work:

- Dedicated OpenAI-compatible config renderer.
- Model provider/base URL/key mapping.
- Clear separation from Codex native login state.
- Tests for no-auth-mixing behavior.

## Test Plan

Backend unit tests:

- Profile validation accepts supported combinations and rejects unsupported ones.
- `cli_native` renderers do not include provider API env keys.
- `project_api` renderers include only their selected protocol keys.
- `claude_code project_api` is rejected.
- `codex project_api` is rejected.
- Secret redaction covers command, trace, and config plan.

Integration tests:

- Stub `opencode`, `claude`, and `codex` CLIs write valid manifests.
- Stub CLIs emit structured stdout events and wrapper captures them.
- Manifest repair still works for each worker type.
- `sandbox_plan.json` includes only intended env keys and writable paths.

Manual smoke tests:

- `pi` project API run.
- `opencode cli_native` with host-side auth/config.
- `opencode project_api` with project key.
- `claude_code cli_native` with host-side login.
- `codex cli_native` with host-side login.

## Open Questions

- Whether native auth directories should be explicitly bound into the sandbox even though host root is already read-only. Explicit binds may make status checks and trace summaries clearer.
- Whether profile secrets should live only in `.env` initially or in a separate encrypted/permissioned project settings store.
- Whether UI should expose command templates for advanced users or keep them as environment-only deployment settings.
- Whether OpenCode project API should initially support only OpenAI-compatible providers or also Anthropic-compatible providers.
