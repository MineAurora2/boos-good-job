# 配额编辑器轮询保持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Dashboard 账号配额编辑器在 3 秒控制状态轮询重绘后继续保持展开并保留未保存输入。

**Architecture:** 使用页面内 `Map` 保存账号级配额草稿，渲染函数以该映射作为编辑状态的单一来源。轮询继续重绘完整实例卡，事件处理器负责创建、更新和清除草稿。

**Tech Stack:** 原生 JavaScript、Node.js `node:test`、浏览器交互测试

---

## 文件结构

- Modify: `tests/test_dashboard_contract.js` - 增加轮询重绘保持配额草稿的回归契约。
- Modify: `dashboard/app.js` - 保存账号级编辑草稿，并在实例卡重绘时恢复编辑器。

### Task 1: 配额编辑状态回归测试

**Files:**
- Modify: `tests/test_dashboard_contract.js:270`

- [ ] **Step 1: 写出失败的重绘回归测试**

增加一个独立测试，在 VM 上下文中提供 `state.accountLimitDrafts`，将 `account-a` 的草稿设为 `73`，再次调用真实的 `instanceQuotaMarkup()`，并断言“修改”按钮隐藏、编辑器展开且输入值为 `73`：

```javascript
test('account quota editor restores its draft after control-center rerenders', () => {
    const helpers = evaluate(`
        const state = { accountLimitDrafts: new Map([['account-a', '73']]) };
        const ACCOUNT_DAILY_LIMIT_MIN = 0;
        const ACCOUNT_DAILY_LIMIT_MAX = 150;
        function escapeHtml(value) { return String(value ?? ''); }
        ${extractFunction('controlArray')}
        ${extractFunction('accountQuotaForInstance')}
        ${extractFunction('instanceQuotaMarkup')}
        ({ state, instanceQuotaMarkup });
    `);
    const markup = helpers.instanceQuotaMarkup(
        { accountId: 'account-a' },
        [{ accountId: 'account-a', count: 10, limit: 60 }],
    );
    assert.match(markup, /class="control-quota-edit"[^>]* hidden/);
    assert.doesNotMatch(markup, /class="control-instance-quota-editor" hidden/);
    assert.match(markup, /value="73" data-account-limit="account-a"/);
});
```

- [ ] **Step 2: 运行测试并确认因编辑状态未持久化而失败**

Run: `node --test --test-isolation=none --test-name-pattern="account quota editor restores" tests/test_dashboard_contract.js`

Expected: FAIL，失败点为“修改”按钮缺少 `hidden` 或编辑器仍包含 `hidden`。

### Task 2: 最小状态恢复实现

**Files:**
- Modify: `dashboard/app.js:50-60`
- Modify: `dashboard/app.js:1568-1582`
- Modify: `dashboard/app.js:2628-2685`

- [ ] **Step 1: 增加账号级草稿状态并由渲染函数读取**

在 `state` 中增加 `accountLimitDrafts: new Map()`。`instanceQuotaMarkup()` 读取账号草稿，存在草稿时隐藏“修改”按钮、展开编辑器并使用转义后的草稿值；不存在草稿时保持当前默认收起行为。

- [ ] **Step 2: 让编辑事件维护草稿生命周期**

点击“修改”时写入当前输入；输入事件更新草稿；“取消”删除草稿并重绘；保存成功删除草稿并重绘，保存失败不删除草稿。

- [ ] **Step 3: 运行聚焦测试并确认通过**

Run: `node --test --test-isolation=none --test-name-pattern="account quota" tests/test_dashboard_contract.js`

Expected: 所有账号配额相关测试 PASS。

### Task 3: 完整验证与提交

**Files:**
- Verify: `dashboard/app.js`
- Verify: `tests/test_dashboard_contract.js`

- [ ] **Step 1: 运行语法、聚焦和完整测试**

Run: `node --check dashboard/app.js`

Expected: exit code 0。

Run: `node --test --test-isolation=none tests/test_dashboard_contract.js`

Expected: 新增回归测试 PASS；本次修改前已有的 3 个失配仍单独记录。

Run: `powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1`

Expected: 若仍失败，只允许是已确认的既有 Dashboard 契约失配；Python、userscript 和新增配额测试均通过。

- [ ] **Step 2: 浏览器验证完整交互**

在 `http://127.0.0.1:47999/dashboard` 点击账号配额“修改”，填写一个不提交的草稿，等待超过 3 秒，确认编辑器与草稿仍在；点击“取消”确认收起。检查页面身份、非空内容、框架错误覆盖层、控制台错误和截图。

- [ ] **Step 3: 检查差异并提交**

Run: `git diff --check`

Expected: 无输出，exit code 0。

Run: `git status --short`

Expected: 仅包含本计划、设计文档、`dashboard/app.js` 和 `tests/test_dashboard_contract.js` 的预期改动。

Commit: `fix(dashboard): preserve quota editor during refresh`

