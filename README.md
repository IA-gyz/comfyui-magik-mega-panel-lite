# Magik Mega Panel Lite

A persistent ComfyUI index counter with a compact floating controller.
Outputs: **index (INT)** and **padded_index (STRING)**.

## Install

Extract the ZIP into ComfyUI/custom_nodes/. The resulting path must be
ComfyUI/custom_nodes/magik_mega_panel_lite/__init__.py.
Restart ComfyUI and refresh the browser. Add **Magik Mega Panel Lite** from the
custom category. Connect an output to a downstream node on an executed output
path. An unused counter will not execute.

This is a separate extension and may coexist with the original Mega Panel.
It has its own node type, frontend namespace, API endpoint and state file.
No additional dependencies beyond ComfyUI's environment are required.

## Panel

Open it from the node's **Open Panel** button or the **Mega Lite** button at the
bottom right. Drag the dotted handle. The header includes orientation (⇆ / ⇅, matching Magik Bundles), settings,
collapse, and hide controls. The panel stays at screen size during canvas zoom.

Set Next (blue) and Freeze share a 3:1 row. The Fire row is 90 px tall,
three times the standard 30 px button row. Fire is green and its text fits the
button. The target selector is 60 px tall. Last/Next text is centered at 26 px;
the main index is 84 px. Aim is yellow and Clear Queue is red.
Settings offer ten themes: Dark (default), Light, Midnight, Forest, Plum,
Ocean, Ember, Slate, Rose and Sand.
The theme is saved per workflow; action colours remain consistent.
Settings include a live UI scale slider (50–200%, default 100%), saved per workflow.

Both layouts use fixed groups rather than freely positioned dashboard widgets.
Position, orientation, visibility, collapse, and active node selection are stored
in workflow JSON. Counter selection appears when several Lite nodes exist.
The panel is a singleton controlling one selected counter in the current workflow.
Small viewports use scrolling as a fallback to keep controls accessible.

The node displays a short Last/Next summary. Its backend serialization widgets
are hidden by the frontend, and remain present in workflow/API data.

## Counter

- Target defaults to 1; accepts 0–999999. Edit directly or use −1/+1.
- **Set Next** schedules a reset for future submissions. Last stays at the most
  recently produced output; Next becomes the target; Reset pending is displayed.
- Reset commits when the counter executes. Already queued workflows retain their
  captured controls and may use an older reset generation.
- Below Set Next/Freeze, index shortcuts apply a reset immediately: 1 on its
  own row, followed by ten phase starts in five rows of two in vertical layout.
  Horizontal layout places the shortcuts in a separate column between the
  counter and queue actions, with two rows of five buttons. The entire Total,
  End and Use Total section sits below the phase buttons in this column. These are the selected
  prompter's first ten thresholds + 1, excluding the final Facial threshold.
  Values refresh live; ascending threshold normalization matches the prompter.
  Missing thresholds show a disabled dash. These buttons also update Target.
  Each phase button displays its destination phase name above the index in
  yellow, slightly smaller text. Short labels include Tease, Grope, Group Grope
  and Anal; integer text size is unchanged.
- **Freeze** retains the same next index. Changing Freeze pauses Auto.
- Padding defaults to 3, configurable from 1–12 in settings.
- No implicit wrap occurs at Total or End.
- Runtime progress persists independently of workflow JSON in
  ~/.comfyui_magik_mega_panel_lite_state.json.
- State uses atomic writes, locking, backup recovery and UUID identities.
  Duplicate Lite nodes get independent identities.
- Entries untouched for 90 days are pruned; at most 2000 entries are retained.
- State recovery failure stops automatic queuing rather than guessing progress.

## Range and queue

Settings offer automatic or explicit supported Magik prompter selection.
Discovery reads live settings proxies, native widgets or JSON settings.
NL Meta Prompter variants use Facial; other recognized variants use max_index.
Ambiguous automatic matches require manual selection.

**Total** comes from the selected prompter. **Use Total** follows it; disable that
checkbox to enter an inclusive custom End. A valid positive prompter Total is
required for Aim and Auto, even with a custom End.

**Aim** tracks the remaining number of submissions:
max(0, end − next + 1 − outstanding), limited to 1000.
Pending workflows count as outstanding. A running workflow whose counter result
has been observed is already represented by Next and is not subtracted twice.
Queue −/+ disables Aim and adjusts the manual count between 0 and 1000.
**Reset**, below Aim, disables Aim and sets Fire count to 1 without changing
the counter index or interrupting Auto.
**Fire ×N** submits the current workflow N times.

**Auto** submits one workflow at a time and waits for full workflow completion.
Counter execution alone does not trigger another submission. Existing matching
queued work is allowed to finish first. Auto stops on execution errors,
interruption, workflow/counter changes, invalid totals, range/propmter changes,
Freeze or server disconnection. If a successful workflow did not execute the
selected counter, Auto stops and asks you to check its output connection.

Hiding or collapsing the panel keeps Auto active. The header and floating
show/hide button indicate Auto activity. Switching counters pauses Auto.
Auto and Aim are session actions and do not restart automatically on reload.
Keep the browser open for Auto. It is not a headless/server-side scheduler.

**Stop** interrupts server execution. **Clear Queue** clears the server's
pending queue. Both pause this panel's Auto and act on the server-wide queue.

## Implementation

- Python class: MagikMegaPanelLite
- Function: process
- Required inputs: knobValue, freeze, padding, current_index, reset_trigger, state_id
- Hidden input: unique_id
- GET /magik_mega_panel_lite/state/{state_id}
- Event: magik_mega_panel_lite_executed
- JavaScript: singleton panel, per-node display state, workflow preferences,
  lifecycle events and queue polling while Aim/Auto is active
- logic.js: queue arithmetic and supported prompter discovery

The state endpoint returns found, index, last_output and last_reset. index is
the persisted next output. Increment reset_trigger when changing current_index
in API submissions. knobValue alone does not reset the counter. Use a fresh UUID
for an independent API counter.

No tensors, model loading or GPU allocations are performed by this extension.
ComfyUI caching can reuse frozen outputs. Server-wide execution failures pause
Auto conservatively, including failures from other clients.

## Validation

Automated checks cover sequence/reset/Freeze behavior, zero index, identity
isolation, persistence recovery across instances, invalid inputs, queue range
boundaries, prompter ambiguity, stale execution notifications, reset previews,
Auto's existing-queue guard, failure handling and node removal.

Python compilation and JavaScript syntax checks passed. Frontend interaction was
tested with a DOM/API harness. Full visual and integration verification in a
running ComfyUI instance remains to be done.
