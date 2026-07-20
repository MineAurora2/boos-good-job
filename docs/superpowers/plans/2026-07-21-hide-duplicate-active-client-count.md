# Hide Duplicate Active Client Count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the dashboard monitor's registered-instance card renders only the primary online-instance count.

**Architecture:** Keep the existing HTML compatibility nodes and JavaScript data updates intact. Add a narrowly scoped CSS visibility contract for hidden monitor metric elements, protected by a static regression test and rendered browser verification.

**Tech Stack:** HTML, CSS, Node.js built-in test runner, in-app Browser

---

### Task 1: Protect hidden monitor metrics

**Files:**
- Modify: `tests/test_dashboard_schedule.js`
- Modify: `dashboard/styles.css:189`

- [ ] **Step 1: Write the failing regression test**

Add this test to `tests/test_dashboard_schedule.js`:

```js
test('hidden compatibility metrics stay hidden in the live monitor', () => {
    assert.match(htmlSource, /id="activeClients" hidden/);
    assert.match(htmlSource, /id="heartbeatWindowHint" hidden/);
    assert.match(stylesSource, /\.monitor-metrics \[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important/);
});
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run: `node --test tests/test_dashboard_schedule.js`

Expected: FAIL in `hidden compatibility metrics stay hidden in the live monitor` because the scoped CSS rule does not exist.

- [ ] **Step 3: Add the minimal scoped CSS rule**

Add to `dashboard/styles.css` beside the monitor metric rules:

```css
.monitor-metrics [hidden] { display: none !important; }
```

- [ ] **Step 4: Run automated verification**

Run: `node --test tests/test_dashboard_schedule.js`

Expected: all tests pass.

- [ ] **Step 5: Verify the rendered dashboard**

Reload `http://127.0.0.1:47999/dashboard` and confirm page identity, meaningful content, no framework overlay, no relevant console errors, `#activeClients` and `#heartbeatWindowHint` are hidden, and `#connectedClients` remains visible as the only value in the card.

- [ ] **Step 6: Commit the implementation**

```powershell
git add -- dashboard/styles.css tests/test_dashboard_schedule.js
git commit -m "修复：隐藏重复在线实例数"
```
