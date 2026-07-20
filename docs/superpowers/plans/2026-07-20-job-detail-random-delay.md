# 职位详情随机延迟实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为搜索页和聊天页全部职位详情入口增加独立、可配置且可中断的随机延迟。

**Architecture:** 在现有 `frontend` 配置中增加详情延迟上下限，由后端默认配置和保存校验负责兼容与约束。用户脚本在 `antiDetection` 中复用分片等待核心，保留投递延迟 API 并新增详情延迟 API；三个详情入口在真正打开或点击详情之前调用新 API。

**Tech Stack:** Python 3、FastAPI 配置层、原生 JavaScript、Node `node:test`、Python `unittest`、PowerShell。

---

### Task 1: 锁定详情延迟配置契约

**Files:**
- Modify: `tests/test_config_validation.py`
- Modify: `app/config.py`
- Modify: `app/storage/admin_store.py`
- Modify: `user_config.example.json`

- [ ] **Step 1: 添加失败的默认值与校验测试**

在 `RuntimeConfigValidationTests` 中增加以下行为：

```python
def test_detail_random_delay_defaults_are_disabled(self) -> None:
    self.assertEqual(DEFAULT_USER_CONFIG['frontend']['detailRandomDelayMinMs'], 0)
    self.assertEqual(DEFAULT_USER_CONFIG['frontend']['detailRandomDelayMaxMs'], 0)

def test_detail_random_delay_accepts_closed_range_boundaries(self) -> None:
    config = copy.deepcopy(DEFAULT_USER_CONFIG)
    config['frontend']['detailRandomDelayMinMs'] = 0
    config['frontend']['detailRandomDelayMaxMs'] = 600000
    validate_config(config)

def test_detail_random_delay_rejects_reversed_range(self) -> None:
    config = copy.deepcopy(DEFAULT_USER_CONFIG)
    config['frontend']['detailRandomDelayMinMs'] = 2
    config['frontend']['detailRandomDelayMaxMs'] = 1
    with self.assertRaisesRegex(ValueError, 'detailRandomDelayMaxMs'):
        validate_config(config)
```

同时循环验证两个字段拒绝 `-1`、`600001`、布尔值和字符串。

- [ ] **Step 2: 运行配置测试并确认正确失败**

```powershell
python -m unittest tests.test_config_validation.RuntimeConfigValidationTests -v
```

预期：因默认配置缺少详情延迟字段而失败，不出现导入或测试语法错误。

- [ ] **Step 3: 增加默认配置和保存校验**

在 `DEFAULT_USER_CONFIG['frontend']` 和 `user_config.example.json` 中加入：

```text
detailRandomDelayMinMs = 0
detailRandomDelayMaxMs = 0
```

在 `validate_config()` 中将两个字段纳入 `0..600000` 数值校验，并增加：

```python
if frontend['detailRandomDelayMaxMs'] < frontend['detailRandomDelayMinMs']:
    raise ValueError('detailRandomDelayMaxMs 不能小于 detailRandomDelayMinMs')
```

- [ ] **Step 4: 运行配置测试并确认通过**

```powershell
python -m unittest tests.test_config_validation.RuntimeConfigValidationTests -v
```

预期：配置测试全部通过。

### Task 2: 锁定用户脚本随机等待与调用边界

**Files:**
- Modify: `tests/test_web_script.js`
- Modify: `web_script.js`

- [ ] **Step 1: 添加失败的详情随机延迟单元测试**

通过测试钩子暴露 `antiDetection` 和 `tools`，用固定 `Math.random` 与替换后的 `tools.asyncSleep` 验证：

```javascript
assert.equal(antiDetection.randomDelayMs(100, 200), 100);
assert.deepEqual(await runDetailDelay({ enabled: true, min: 450, max: 450 }), [200, 200, 50]);
assert.deepEqual(await runDetailDelay({ enabled: false, min: 450, max: 450 }), []);
assert.equal(await interruptedDetailDelay(), false);
```

- [ ] **Step 2: 添加失败的三个详情入口契约测试**

从源码中提取搜索页 `getJobInfo` 和两个聊天分支，断言 `await antiDetection.detailDelay(...)` 均位于对应 `openTabNSetTimestamp(...)` 或 `chatInfo.jobEl.click()` 之前，并且聊天页匹配到两处调用。

- [ ] **Step 3: 运行 Node 测试并确认正确失败**

```powershell
node --test --test-isolation=none tests/test_web_script.js
```

预期：因缺少详情配置、`detailDelay()` 和三个入口调用而失败。

- [ ] **Step 4: 实现独立详情随机延迟**

在 `OPTIONS` 中加入两个默认字段。在 `antiDetection` 内让随机毫秒函数接收明确的最小值和最大值，并由共享的 `delayWithRange(min, max, shouldInterrupt)` 完成最多 `200 ms` 的可中断分片等待：

```javascript
async delay(shouldInterrupt = null) {
    return this.delayWithRange(OPTIONS.randomDelayMinMs, OPTIONS.randomDelayMaxMs, shouldInterrupt);
},
async detailDelay(shouldInterrupt = null) {
    return this.delayWithRange(
        OPTIONS.detailRandomDelayMinMs,
        OPTIONS.detailRandomDelayMaxMs,
        shouldInterrupt
    );
},
```

搜索页等待失败时返回跳过结果，由调用方已有停止/暂停检查退出；聊天页等待失败时 `continue`，不触发卡片点击。

- [ ] **Step 5: 运行 Node 测试并确认通过**

```powershell
node --check web_script.js
node --test --test-isolation=none tests/test_web_script.js
```

预期：语法检查成功，用户脚本测试全部通过。

### Task 3: 更新可见配置说明

**Files:**
- Modify: `dashboard/app.js`
- Modify: `readme.md`
- Test: `tests/test_dashboard_contract.js`

- [ ] **Step 1: 添加失败的配置标签与文档契约**

在 Dashboard 契约测试中断言 `detailRandomDelayMinMs` 和 `detailRandomDelayMaxMs` 均有中文标签，并断言 README 包含配置键、默认值、范围和总开关说明。

- [ ] **Step 2: 运行契约测试并确认正确失败**

```powershell
node --test --test-isolation=none tests/test_dashboard_contract.js
```

预期：因缺少两个详情延迟标签和文档行而失败。

- [ ] **Step 3: 增加 Dashboard 标签与 README 参数行**

Dashboard 标签使用“职位详情随机延时下限（ms）”和“职位详情随机延时上限（ms）”。README 在防检测随机化表格中说明该区间覆盖搜索页与聊天页全部详情入口，且关闭总开关后失效。

- [ ] **Step 4: 运行契约测试并确认通过**

```powershell
node --check dashboard/app.js
node --test --test-isolation=none tests/test_dashboard_contract.js
```

预期：语法检查成功，Dashboard 契约测试全部通过。

### Task 4: 完整验证、清理与提交

**Files:**
- Modify: `app/config.py`
- Modify: `app/storage/admin_store.py`
- Modify: `web_script.js`
- Modify: `dashboard/app.js`
- Modify: `user_config.example.json`
- Modify: `readme.md`
- Modify: `tests/test_config_validation.py`
- Modify: `tests/test_web_script.js`
- Modify: `tests/test_dashboard_contract.js`

- [ ] **Step 1: 运行完整测试**

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_tests.ps1
```

预期：Python 编译、全部 Python 单元测试、JavaScript 语法检查和全部 Node 测试通过。

- [ ] **Step 2: 检查差异和需求覆盖**

```powershell
git diff --check
git status --short
```

确认两个详情字段贯穿默认配置、保存校验、客户端配置、用户脚本、Dashboard 标签、示例和 README；确认三个详情入口均调用专用延迟；确认现有用户改动未被覆盖或暂存。

- [ ] **Step 3: 关闭本次启动的项目相关进程**

仅关闭本任务启动且命令行指向 `E:\dev\czc-good-job` 的测试或服务进程，不影响用户原有进程。

- [ ] **Step 4: 中文提交到 main**

只暂存本功能新增或修改的补丁，避免纳入工作区既有 Dashboard 改动，然后提交：

```powershell
git commit -m "新增：支持职位详情独立随机延迟"
```

预期：当前分支仍为 `main`，产生中文功能提交；用户原有未提交文件保持原状。

