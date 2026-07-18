# Browser Instance Liveness and Userscript Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide offline browser cards, treat an authenticated control poll as ephemeral liveness, and add a safe local log viewer to the userscript float.

**Architecture:** Preserve the full registered-worker control model while filtering only the Dashboard card projection. Refresh only the validated in-memory client timestamp from control polling. Keep userscript upload and display log buffers independent.

**Tech Stack:** Python 3, FastAPI, vanilla JavaScript, Node test runner, unittest.

**Workspace note:** The shared worktree already contains unrelated user changes, including edits in `web_script.js` and `dashboard/app.js`. Execution must preserve those changes and must not create commits that could mix unrelated work.

---

### Task 1: Validated Control Poll Liveness

**Files:**
- Modify: `app/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing valid-session liveness test**

Add a test that registers `worker-a`, ages `_seenMonotonic` beyond 30 seconds, verifies `online` is false, calls `desired_control()` with the matching session, then verifies `online` is true and `lastSeen` advanced.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_runtime.RuntimeControlProtocolTests.test_desired_control_poll_refreshes_valid_session_liveness -v
```

Expected: FAIL because `desired_control()` currently leaves `_seenMonotonic` unchanged.

- [ ] **Step 3: Write the failing stale-session protection test**

Capture `_seenMonotonic`, call `desired_control()` with a mismatched session, assert `stale_session`, and assert the timestamp is unchanged.

- [ ] **Step 4: Implement the minimal ephemeral touch**

Add a lock-only helper equivalent to:

```python
def _touch_client_liveness_locked(self, worker_id: str) -> None:
    client = self._clients.get(worker_id)
    if client is None:
        return
    client['_seenMonotonic'] = time.monotonic()
    client['lastSeen'] = self._now_iso()
```

Call it only after all session checks pass in each `desired_control()` loop iteration. Do not persist worker state.

- [ ] **Step 5: Run focused and runtime tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_runtime -v
```

Expected: PASS.

### Task 2: Online-Only Dashboard Cards

**Files:**
- Modify: `dashboard/app.js`
- Modify: `dashboard/index.html`
- Test: `tests/test_dashboard_contract.js`

- [ ] **Step 1: Write the failing rendering-contract test**

Assert `renderControlInstances()` creates an online-only projection, uses it for the empty state and iteration, and renders “暂无在线浏览器实例” when empty.

- [ ] **Step 2: Run the focused Dashboard test and verify RED**

```powershell
node --test --test-isolation=none --test-name-pattern="browser instance cards hide offline workers" tests\test_dashboard_contract.js
```

Expected: FAIL because the function currently iterates the original `instances` array.

- [ ] **Step 3: Implement the render-boundary filter**

Use:

```javascript
const onlineInstances = instances.filter((item) => item.online !== false);
```

Build the summary, empty state, and cards from `onlineInstances`, while leaving `normalizedControlState().instances` unchanged.

- [ ] **Step 4: Update monitor copy**

Keep “已登记实例 / 包含当前离线实例” for the registration metric, and change the browser-card summary/empty copy only.

- [ ] **Step 5: Run Dashboard tests and verify GREEN**

```powershell
node --test --test-isolation=none tests\test_dashboard_contract.js
```

Expected: PASS.

### Task 3: Userscript Local Log Viewer

**Files:**
- Modify: `web_script.js`
- Modify: `readme.md`
- Test: `tests/test_web_script.js`

- [ ] **Step 1: Write failing UI and buffer tests**

Add tests that require:

```javascript
const logToggle = root.querySelector('[data-goodjobs-log-toggle]');
const logPanel = root.querySelector('[data-goodjobs-log-panel]');
const logList = root.querySelector('[data-goodjobs-log-list]');
const clearLog = root.querySelector('[data-goodjobs-log-clear]');
```

Verify default collapsed state, toggle behavior, literal text rendering, warning/error level data, clear behavior, and a 100-entry cap.

- [ ] **Step 2: Write the failing queue-isolation test**

Queue one log through `ControlAgent.queueLog()`, clear `statusIndicator` history, and assert `ControlAgent.logs` still contains the pending upload entry.

- [ ] **Step 3: Run focused userscript tests and verify RED**

```powershell
node --test --test-isolation=none --test-name-pattern="userscript local log" tests\test_web_script.js
```

Expected: FAIL because the float currently has no log controls or display buffer.

- [ ] **Step 4: Implement StatusIndicator log state and DOM**

Add `logEntries`, `logsExpanded`, `addLog()`, `clearLogs()`, `toggleLogs()`, and `renderLogs()`. Limit entries with:

```javascript
if (this.logEntries.length > 100) this.logEntries.splice(0, this.logEntries.length - 100);
```

Render every message with `textContent`, use `role="log"`, `aria-live="polite"`, and keep the panel hidden until toggled.

- [ ] **Step 5: Connect queueing without coupling buffers**

In `ControlAgent.queueLog()` normalize once, append to `this.logs`, and call `this.statusIndicator.addLog(entry)`. `clearLogs()` must never access `ControlAgent.logs`.

- [ ] **Step 6: Bump userscript version and update documentation**

Change both metadata and `SCRIPT_VERSION` from `2026-07-18-remote-control.7` to `2026-07-19-remote-control.8`. Update the README to describe local logs, log toggle, and clear behavior.

- [ ] **Step 7: Run userscript tests and verify GREEN**

```powershell
node --check web_script.js
node --test --test-isolation=none tests\test_web_script.js
```

Expected: PASS.

### Task 4: Integration and Visual Verification

**Files:**
- Verify all modified files

- [ ] **Step 1: Run the full automated suite**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_tests.ps1
```

Expected: all five stages pass with no warnings or failures attributable to this change.

- [ ] **Step 2: Start the local server for browser QA**

Start `main.py` without automatic browser opening, verify Dashboard desktop and narrow viewports, and inspect console errors.

- [ ] **Step 3: Verify the userscript float in a local test harness**

Load `web_script.js` with `window.__GOODJOBS_TEST__ = true`, instantiate `StatusIndicator`, add normal/warning/error entries, and verify toggle and clear interactions at desktop and mobile widths.

- [ ] **Step 4: Stop project-related processes**

Resolve the listener process created for QA and terminate only that project process. Confirm no workspace Python/uvicorn helper remains.

- [ ] **Step 5: Review the final diff**

Confirm the unrelated user edits in `app/config.py`, `app/llm/tasks.py`, `app/routes/delivery.py`, `app/storage/admin_store.py`, `dashboard/app.js`, `user_config.example.json`, and `web_script.js` remain intact.
