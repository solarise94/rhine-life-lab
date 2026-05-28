# Manager Auto Input Button UI Plan

## Context

The current Manager chat UI already exposes auto mode in two places:

- `SideNav` marks the owner session with an `AUTO` badge and applies breathing animation for `running` / `thinking`.
- `ManagerChatPanel` changes the send button from `Send` to `Sparkles` whenever the current session owns auto mode.

The problem is that the composer button currently uses only ownership as the visual switch. It does not distinguish auto idle from auto running:

- auto owner idle still breathes, which makes the UI look busy when no background work is active;
- auto running is not represented as an interruptible state on the composer button;
- the text input remains available during auto running, even though user steering should happen only by explicitly interrupting or by queuing directives at safe wake points;
- `busy` is a manual chat-stream state and should not be reused as the source of truth for auto background progress.

## Goal

Reuse the existing Manager composer send button as the only primary action surface. Do not add a separate auto control button.

The button should encode four states:

| State | Icon | Visual | Click behavior | Text input |
| --- | --- | --- | --- | --- |
| Auto off + idle | existing `Send` | existing style | send normal Manager message | enabled |
| Auto off + background task running | existing `Send` | existing style | send normal Manager message | enabled |
| Auto on + idle | auto-specific icon, e.g. `Sparkles` | auto idle color, static | send user message as auto directive | enabled |
| Auto on + running | auto stop/interruption icon, e.g. `Square` | running color + breathing | stop auto mode | disabled, draft retained |

Only `Auto on + running` disables the composer and repurposes the button into an interrupt action.

## State Derivation

Add a small derived UI state in `ManagerChatPanel`.

```ts
const isAutoOwnerSession = Boolean(
  managerAuto?.enabled &&
  sessionId &&
  managerAuto.owner_session_id === sessionId,
);

const autoBackgroundRunning = Boolean(
  managerAuto?.state === "running" ||
    managerAuto?.state === "thinking" ||
    managerAuto?.active_run_id ||
    managerAuto?.active_job_id,
);

const autoComposerState = !isAutoOwnerSession
  ? "normal"
  : autoBackgroundRunning
    ? "auto_running"
    : "auto_idle";
```

Do not treat `busy` as an auto-running signal. `busy` only means the local manual chat stream is active.

## Composer Behavior

### Normal

When `autoComposerState === "normal"`:

- render the existing `Send` icon;
- keep the current button style;
- keep current `submit()` behavior;
- do not disable the textarea unless the session is loading or failed.

This includes the case where auto mode is off but some backend executor run is still active. The composer should stay a chat input, not a run-control surface.

### Auto Idle

When `autoComposerState === "auto_idle"`:

- render an auto-specific icon, preferably `Sparkles` to match the existing UI;
- use an auto idle color;
- do not apply breathing animation;
- keep the textarea enabled;
- keep `submit()` behavior, which currently routes owner-session messages through `api.addManagerAutoDirective()`.

This state means "auto is enabled and the next user message will steer auto mode." It must not imply that work is currently executing.

### Auto Running

When `autoComposerState === "auto_running"`:

- render a stop/interruption icon, preferably `Square`;
- apply running color and the existing `auto-breathe` animation;
- disable the textarea and keep the current draft in place;
- block keyboard submit;
- clicking the button calls `api.stopManagerAuto(projectId, sessionId, "user_stop", "因用户停止任务，已退出 auto 模式。")`;
- refresh workspace and auto state after the stop call.

First version stop semantics should match the existing `/auto stop` contract: stop auto scheduling and exit auto mode. It should not silently kill an already running executor process unless the backend command explicitly supports that behavior.

## CSS Direction

Split the current broad class:

```css
.manager-send-button.auto-owner { ... animation: auto-breathe ... }
```

into explicit states:

```css
.manager-send-button.auto-idle {
  background: linear-gradient(135deg, #0f766e, #155eef);
  box-shadow: 0 0 0 1px rgba(34, 211, 238, 0.16), 0 10px 24px rgba(21, 94, 239, 0.18);
}

.manager-send-button.auto-running {
  background: linear-gradient(135deg, #0f766e, #2563eb);
  box-shadow: 0 0 0 1px rgba(34, 211, 238, 0.2), 0 12px 26px rgba(37, 99, 235, 0.24);
  animation: auto-breathe 1.8s ease-in-out infinite;
}
```

Keep `.manager-stop-button` for manual chat-stream aborts. Do not merge manual stream stop and auto stop into the same state variable.

## Implementation Points

Primary files:

- `frontend/components/manager-chat/ManagerChatPanel.tsx`
- `frontend/app/globals.css`

Recommended steps:

1. Derive `autoBackgroundRunning` and `autoComposerState` near the existing `isAutoOwnerSession`.
2. Add `stopAutoFromComposer()` that wraps the existing `api.stopManagerAuto()` call and `onRefresh()`.
3. Set `textarea.disabled` to `sessionBusy || Boolean(sessionLoadError) || autoComposerState === "auto_running"`.
4. In `onKeyDown`, return early for submit shortcuts when `autoComposerState === "auto_running"`.
5. Replace the send button class from `auto-owner` to `auto-idle` / `auto-running`.
6. Change button `onClick`:
   - `auto_running` -> `stopAutoFromComposer`
   - otherwise -> `submit`
7. Change button `disabled`:
   - `auto_running`: disabled only when stop request is already pending, session is missing, or session load failed;
   - otherwise: keep existing `!draft.trim() || sessionBusy || sessionLoadError`.
8. Change tooltip:
   - normal: `发送`
   - auto idle: `Auto mode 已开启，发送为追加指令`
   - auto running: `停止 Auto 推进`
9. Keep `manager-auto-chip` as secondary text status. The composer button is the primary state signal.

## Regression Checks

Manual checks:

- Auto off + idle: button shows `Send`; message sends normally.
- Auto on + idle: button shows auto icon, does not breathe, textarea is enabled.
- Auto on + running/thinking: button breathes, shows stop icon, textarea is disabled, draft text remains.
- Clicking the running button exits auto mode and returns the button to auto-off or idle styling after refresh.
- Auto off while an executor run is active: button remains normal `Send`; textarea remains enabled.
- Manual chat streaming still uses the existing stop button and abort behavior.

Suggested test coverage:

- A component-level test or lightweight browser smoke check for the four visual states.
- A functional test that auto-running click calls the stop endpoint rather than the chat submit endpoint.
- A check that `busy` manual streaming still renders `.manager-stop-button` and is not confused with auto-running.

