# Offline Browser Instance Unregistration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically unregister browser instances that have exceeded the heartbeat TTL or explicitly exited, including removal from persisted worker registrations.

**Architecture:** Add one lock-scoped pruning helper to `RuntimeMonitor` and call it at the boundaries that expose or target registered workers. Persist the reduced worker map before committing the matching in-memory removal, so snapshots and lifecycle commands only operate on online registrations.

**Tech Stack:** Python 3, `unittest`, FastAPI runtime state, Node.js built-in test runner for Dashboard contracts.

---

### Task 1: Runtime registration lifecycle

**Files:**
- Modify: `tests/test_runtime.py`
- Modify: `app/runtime.py`

- [ ] **Step 1: Write failing expiry and exit tests**

Add tests that age `_seenMonotonic` beyond the configured TTL and that heartbeat with `state='closed'`. Assert `clients == []`, both counts are zero, the worker is absent from `_clients`, and the JSON `workers` map no longer contains its ID. Re-heartbeat the expired ID after previously setting it to `running` and assert the new control defaults to `stopped`.

- [ ] **Step 2: Verify the tests fail for the old retained-registration behavior**

Run: `python -m unittest tests.test_runtime.RuntimeControlProtocolTests.test_expired_worker_is_unregistered_and_reregisters_stopped tests.test_runtime.RuntimeControlProtocolTests.test_closed_worker_is_unregistered_immediately -v`

Expected: FAIL because expired/closed workers remain in `clients` and persisted `workers`.

- [ ] **Step 3: Add the lock-scoped pruning implementation**

Add `_prune_offline_workers_locked(now_monotonic)` that identifies missing, expired, `closed`, or `exited` clients, persists a copied worker map without them, then replaces `_workers`, removes matching `_clients`, and notifies waiters. Invoke it in `snapshot`, `desired_control`, `set_global_desired_state`, and `set_worker_desired_state`. Capture heartbeat control before snapshot generation so a final closed heartbeat can return without looking up an already-unregistered worker.

- [ ] **Step 4: Update persistence/restart and liveness tests to the new contract**

Replace assertions that expect restored or expired offline workers to remain registered. Assert a restored monitor snapshot has no clients and clears its persisted worker map; keep long-poll liveness coverage by aging a live client within TTL and verifying `desired_control` refreshes it.

- [ ] **Step 5: Verify runtime tests**

Run: `python -m unittest tests.test_runtime -v`

Expected: all runtime tests PASS.

- [ ] **Step 6: Commit the runtime lifecycle change**

Run: `git add app/runtime.py tests/test_runtime.py && git commit -m "fix: 注销离线浏览器实例"`

### Task 2: Dashboard registration wording

**Files:**
- Modify: `dashboard/index.html`
- Modify: `tests/test_dashboard_contract.js`

- [ ] **Step 1: Write a failing Dashboard contract assertion**

Assert the registration metric says `仅统计在线实例` and no longer contains `包含当前离线实例`.

- [ ] **Step 2: Verify the Dashboard contract test fails**

Run: `node --test tests/test_dashboard_contract.js`

Expected: FAIL because `dashboard/index.html` still advertises offline registrations.

- [ ] **Step 3: Update the metric copy**

Change the small explanatory text under `已登记实例` from `包含当前离线实例` to `仅统计在线实例`.

- [ ] **Step 4: Verify Dashboard contracts**

Run: `node --test tests/test_dashboard_contract.js`

Expected: all Dashboard contract tests PASS.

- [ ] **Step 5: Commit the Dashboard wording and plan**

Run: `git add dashboard/index.html tests/test_dashboard_contract.js docs/superpowers/plans/2026-07-19-unregister-offline-browser-instances.md && git commit -m "docs: 明确实例仅在线登记"`

### Task 3: Full verification

**Files:**
- Verify only

- [ ] **Step 1: Run the complete Python suite**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: all Python tests PASS.

- [ ] **Step 2: Run the complete JavaScript test suite**

Run: `node --test tests/*.js`

Expected: all JavaScript tests PASS.

- [ ] **Step 3: Inspect the final diff and process list**

Run: `git diff --check` and inspect `git status --short`. Confirm only scoped files changed in addition to pre-existing user deletions. Confirm no project server or test process remains running, satisfying `AGENTS.md`.
