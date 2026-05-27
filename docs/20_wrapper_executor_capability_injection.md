# Wrapper Executor Capability Injection

## Goal

The executor compatibility layer keeps Blueprint's execution model provider-neutral while letting each CLI consume the same run contract in its native form.

Blueprint-owned services should describe capabilities once in `task_packet.json`:

- `executor_context.skills`
- `executor_context.mcp_servers`
- `executor_context.tool_policy`
- `executor_context.runtime_bindings`
- run-local files under `runs/<run_id>/library/`

Provider-specific details belong in the wrapper/renderer layer, not in frontend state, card models, or `WorkerService`.

## Boundaries

`WorkerService` builds the unified contract. It resolves library bindings and writes them into `executor_context.template_metadata`, but it does not know Claude/OpenCode/Codex flags.

`CommandTemplateWorkerAdapter` materializes run-local files and passes stable environment variables into bwrap:

- `BLUEPRINT_EXECUTOR_SKILLS`
- `BLUEPRINT_EXECUTOR_MCP_SERVERS`
- `BLUEPRINT_EXECUTOR_SKILL_BINDINGS`
- `BLUEPRINT_EXECUTOR_MCP_BINDINGS`
- `BLUEPRINT_EXECUTOR_MCP_CONFIG`
- `BLUEPRINT_PI_SKILL_PATHS`

`agent_cli_executor` is the wrapper entrypoint. When a selected profile has an `auth_mode`, it asks the provider renderer to generate final argv, env overlay, and run-scoped config files.

`provider_renderers/*` translate Blueprint's contract into provider-native inputs:

- Claude Code: argv flags such as `--mcp-config`, `--allowedTools`, and `--permission-mode`.
- OpenCode: run-scoped config plus env pointing to skill/MCP binding files.
- Pi: env and skill path inputs.
- Claude Code and Codex: currently cli-native only for auth; provider API injection remains future work.

## Capability Mapping

### Native Support Matrix

This table describes the current implementation, not each CLI's theoretical maximum capability.

| Worker | Login / API injection | Tool policy native injection | MCP native injection | Skill native injection | Fallback boundary |
| --- | --- | --- | --- | --- | --- |
| `pi` | `project_api` and `cli_native`. Project API injects DeepSeek/Pi credentials through the launch env/script; cli_native uses host-side Pi auth. | Partial. Tool policy is mostly prompt-level plus bwrap enforcement. | Not native. MCP remains represented in Blueprint files/prompt. | Supported through `pi --skill <path>` generated from `BLUEPRINT_PI_SKILL_PATHS`. | bwrap, task packet, executor prompt, validation/review. |
| `opencode` | `cli_native` and `project_api`. Project API can generate OpenAI-compatible/provider-native config. | Partial. Policy is written to run-scoped OpenCode capability config; filesystem boundary remains bwrap. | Partial. MCP config is embedded into OpenCode config and exposed as `OPENCODE_MCP_CONFIG`. | Partial. Skill ids/paths are written to OpenCode config/env; exact native plugin semantics are best-effort. | bwrap, task packet, executor prompt, validation/review. |
| `claude_code` | `cli_native` only. Project Anthropic API injection is intentionally blocked. | Supported subset through `--permission-mode`, `--allowedTools`, and `--disallowedTools`. | Supported through `--mcp-config <path>`. | Not native. Skill paths are exposed through Blueprint env/prompt only. | bwrap, task packet, executor prompt, validation/review. |
| `codex` | `cli_native` only. Project OpenAI API injection is deferred. | Not native in current renderer. | Not native in current renderer. | Not native in current renderer. | bwrap, task packet, executor prompt, validation/review. |

### Skills

Skills are copied into `runs/<run_id>/library/skills/<skill_id>/` and listed in `skill_bindings.json`. Renderers expose these paths through provider config/env and keep prompt references in `executor_prompt.md`.

This is a file-level capability. Provider-native skill/plugin loading is best-effort and must remain optional because each CLI has different plugin semantics.

### MCP

MCP bindings are written to:

- `runs/<run_id>/library/mcp_bindings.json`
- `runs/<run_id>/library/mcp.json`

Renderers should pass `mcp.json` to CLIs that support native MCP config. For Claude Code this is `--mcp-config <path>` in cli-native mode. For OpenCode this is embedded into the run-scoped config directory and exposed with `OPENCODE_MCP_CONFIG`.

### Tool Policy

Blueprint tool policy is advisory plus sandbox-enforced:

- Network policy controls whether model-backed workers may run.
- bwrap controls filesystem visibility and write scope.
- Provider renderers translate safe subsets to CLI permissions.

Claude Code supports a closer native mapping through `--permission-mode`, `--allowedTools`, and `--disallowedTools` while still using local CLI login. OpenCode mapping is config/env based until exact CLI permission flags are finalized.

## Known Constraints

Default executor profiles must be resolvable at runtime, not only displayed by the profiles API. If `auth_mode` is not passed into the wrapper, renderers are bypassed and provider-native injection will not happen.

Deployment must expose CLI binaries to the backend/bwrap PATH or use absolute `*_COMMAND_JSON` entries. Otherwise the wrapper fails before provider capabilities matter.
