# 油猴脚本账号标识展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 在油猴脚本左下角悬浮窗中常驻显示当前 `accountId`，方便确认页面对应的 Boss 用户。

**Architecture:** 复用现有 `deliveryIdentity.get()` 作为唯一数据源，在 `StatusIndicator` 的状态文案区域增加一个独立账号行。账号行使用 `textContent` 和 `title`，并沿用状态区的单行省略样式，不改变身份存储、设置保存、心跳或 Dashboard 协议。

**Tech Stack:** 原生 JavaScript 油猴脚本、Node.js `node:test`、现有 `tests/test_web_script.js` 虚拟 DOM 测试夹具。

---

### Task 1: Add the regression test first

**Files:**
- Modify: `tests/test_web_script.js` near the existing `status indicator exposes logs and settings without lifecycle buttons` test

- [ ] **Step 1: Write the failing test**

Add one focused test that preloads the account identifier before mounting the indicator and checks visible text, full-value tooltip, and bounded single-line styling:

```js
test('floating status shows the configured account identifier', () => {
    const document = createFakeDocument();
    const { hooks, localStorage } = loadHooks({ document, URL });
    localStorage.setItem(hooks.deliveryIdentity.accountKey, 'account-visible');

    const indicator = new hooks.StatusIndicator();
    indicator.update('connected', 'stopped');

    const account = document.getElementById('goodjobs-runtime-status')
        .querySelector('[data-goodjobs-account]');
    assert.ok(account);
    assert.equal(account.textContent, '账号：account-visible');
    assert.equal(account.title, '账号标识：account-visible');
    assert.match(account.style.cssText, /text-overflow:ellipsis/);
    assert.match(account.style.cssText, /white-space:nowrap/);
});
```

- [ ] **Step 2: Run the focused test and verify it fails for the missing UI**

Run:

```powershell
node --test --test-isolation=none tests/test_web_script.js
```

Expected: the new test fails because `[data-goodjobs-account]` is not present; existing tests may continue to pass.

### Task 2: Implement the minimal status-row change

**Files:**
- Modify: `web_script.js:1547-1640` for element creation, styling, and assembly
- Modify: `web_script.js:1880-1894` for render-time account text

- [ ] **Step 1: Add the account element to `StatusIndicator.mount()`**

Create a `span` beside `connection` and `execution`, mark it with `data-goodjobs-account`, and style it as a single-line ellipsis row. Set its `title` from the same account value used during rendering. Append it between the connection and execution elements so it remains visible in the existing status block.

- [ ] **Step 2: Update the element in `StatusIndicator.render()`**

Read `deliveryIdentity.get().accountId`, set `account.textContent` to `账号：${accountId}`, set `account.title` to `账号标识：${accountId}`, and leave the existing connection/execution state and root border behavior unchanged.

- [ ] **Step 3: Run the focused test and verify it passes**

Run:

```powershell
node --test --test-isolation=none tests/test_web_script.js
```

Expected: all tests in `test_web_script.js` pass, including the new account-identifier regression test.

### Task 3: Full verification and delivery

**Files:**
- Verify: `web_script.js`, `tests/test_web_script.js`, and the committed design/plan docs

- [ ] **Step 1: Run syntax and complete repository checks**

Run:

```powershell
node --check web_script.js
node --test --test-isolation=none tests/test_web_script.js
scripts/run_tests.ps1
```

Expected: each command exits with code 0 and the repository test runner prints `All checks passed.`

- [ ] **Step 2: Check for project processes and close any started by this task**

Inspect processes associated with the repository or its test runner; no development server is required for this DOM-only change. Stop only processes started for this task, then confirm none remain.

- [ ] **Step 3: Review the final diff and commit the implementation in Chinese**

Run `git diff --check` and review `git status --short`. Stage only `web_script.js` and `tests/test_web_script.js`, then commit with a concise Chinese message such as:

```powershell
git add web_script.js tests/test_web_script.js
git commit -m "修复：在油猴悬浮窗显示账号标识"
```

