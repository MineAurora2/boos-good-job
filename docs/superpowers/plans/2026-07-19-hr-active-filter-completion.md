# HR 活跃筛选完整状态与严格白名单 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 补齐日志中确认的 14 个 HR 活跃状态，并让“未知”与其他状态一样严格按管理页白名单过滤。

**Architecture:** 后端 `HR_ACTIVE_LEVELS` 作为配置和值校验的单一来源；浏览器脚本维护同顺序的归一化映射和严格成员判断；Dashboard 从同一顺序渲染多选项。旧版 `hrActiveMinLevel` 只迁移时间状态，不接受 `unknown`，已有多选配置不自动扩充；启动遇到显式无效配置时归一化为空白名单，避免 fail-open。

**Tech Stack:** Python 配置/存储层、原生 JavaScript userscript、原生 Dashboard JavaScript、Python `unittest`、Node test runner。

---

### Task 1: 建立失败回归测试

**Files:**
- Modify: `tests/test_config_validation.py`
- Modify: `tests/test_dashboard_contract.js`
- Modify: `tests/test_web_script.js`

- [x] **Step 1: 扩展 Python 配置断言**

把默认状态断言改为完整顺序，并新增包含新时间状态和 `unknown` 的合法多选测试；增加启动加载回归，断言 `hrActiveMinLevel: "unknown"`、`hrActiveLevels: null` 和全非法数组都加载为空白名单。

~~~python
expected_levels = [
    'online', 'just_now', 'today', 'within_3_days', 'this_week',
    'within_2_weeks', 'this_month', 'within_2_months',
    'within_3_months', 'within_4_months', 'within_5_months',
    'within_half_year', 'half_year_ago', 'unknown',
]
self.assertEqual(DEFAULT_USER_CONFIG['frontend']['hrActiveLevels'], expected_levels)

def test_invalid_hr_active_settings_load_as_empty_allow_list(self) -> None:
    invalid_frontends = [
        {'hrActiveMinLevel': 'unknown'},
        {'hrActiveLevels': None},
        {'hrActiveLevels': ['not-a-level']},
    ]
    for frontend in invalid_frontends:
        with self.subTest(frontend=frontend):
            loaded = config_module.load_user_config()
            self.assertEqual(loaded['frontend']['hrActiveLevels'], [])
~~~

- [x] **Step 2: 扩展 Dashboard 选项契约**

把 HR 选项值断言改为 14 个值，固定为：`online`、`just_now`、`today`、`within_3_days`、`this_week`、`within_2_weeks`、`this_month`、`within_2_months`、`within_3_months`、`within_4_months`、`within_5_months`、`within_half_year`、`half_year_ago`、`unknown`。

- [x] **Step 3: 写浏览器归一化和严格过滤的失败断言**

在 userscript 测试中覆盖新增中文文案，并断言开启筛选后只有选中的状态通过：

~~~javascript
hooks.OPTIONS.hrActiveFilterEnabled = true;
hooks.OPTIONS.hrActiveLevels = ['within_2_weeks'];
assert.equal(hooks.hrActivePasses('within_2_weeks'), true);
assert.equal(hooks.hrActivePasses('unknown'), false);

hooks.OPTIONS.hrActiveLevels = ['unknown'];
assert.equal(hooks.hrActivePasses('unknown'), true);

hooks.OPTIONS.hrActiveLevels = [];
assert.equal(hooks.hrActivePasses('online'), false);
~~~

- [x] **Step 4: 运行红灯测试**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_config_validation
node --test --test-isolation=none tests\test_dashboard_contract.js
node --test --test-isolation=none tests\test_web_script.js
~~~

预期：测试因新增枚举不存在、`unknown` 无条件放行和空白名单仍放行而失败，不得因语法或加载错误失败。

### Task 2: 更新后端状态模型与兼容迁移

**Files:**
- Modify: `app/config.py`
- Modify: `app/storage/admin_store.py`
- Modify: `user_config.example.json`

- [x] **Step 1: 扩展单一枚举来源**

按设计顺序将 `HR_ACTIVE_LEVELS` 改为 14 项，并让默认 `frontend.hrActiveLevels` 复制完整列表；同时定义不含 `unknown` 的时间档位集合供旧阈值使用。

- [x] **Step 2: 保持旧最低档位迁移安全**

让 `hr_active_levels_from_minimum()` 对 `unknown` 返回 `None`，对新增时间值返回从 `online` 到该值的前缀。加载配置时，显式 `null`、非法类型、全非法数组和无法迁移的旧值归一化为空白名单；完全没有提供字段时才使用默认配置。保存时继续拒绝空数组、重复值和非法值。

- [x] **Step 3: 更新示例配置**

把示例 JSON 的 `hrActiveLevels` 改为完整 14 项。

- [x] **Step 4: 运行后端绿灯测试**

~~~powershell
.\.venv\Scripts\python.exe -m unittest tests.test_config_validation
~~~

预期：配置测试全部通过。

### Task 3: 修复 userscript 归一化与严格过滤

**Files:**
- Modify: `web_script.js`

- [x] **Step 1: 扩展状态标签和顺序**

在 `HR_ACTIVE_LEVELS`、`HR_ACTIVE_LEVEL_ORDER` 和 `HR_ACTIVE_LABELS` 中加入 14 项状态，按从近到远排列，把 `unknown` 放在顺序末尾；旧最低档位计算排除 `unknown`。

- [x] **Step 2: 添加真实文案归一化**

加入 `2周内活跃`、`2月内活跃`、`3月内活跃`、`4月内活跃`、`5月内活跃`、`近半年活跃`、`半年前活跃` 映射，并保留已有同义词和空白归一化。

- [x] **Step 3: 让未知和空白配置严格按白名单**

关闭筛选返回 `true`；开启筛选时有效选择为空返回 `false`；否则直接返回 `selected.includes(level)`，移除 `unknown` 无条件放行。

- [x] **Step 4: 运行 userscript 绿灯测试**

~~~powershell
node --test --test-isolation=none tests\test_web_script.js
~~~

预期：userscript 全部测试通过。

### Task 4: 同步 Dashboard、文档与契约

**Files:**
- Modify: `dashboard/app.js`
- Modify: `readme.md`
- Modify: `tests/test_dashboard_contract.js`

- [x] **Step 1: 同步 Dashboard 标签和多选项**

`HR_ACTIVE_LABELS` 与 `CONFIG_MULTI_OPTIONS.hrActiveLevels` 使用同一 14 项顺序和值，确保管理页、详情和导出显示新状态。

- [x] **Step 2: 更新使用说明**

说明开启后必须命中所选状态；无法识别文案归入 `unknown`，只有勾选 `unknown` 才放行，并列出完整配置值。

- [x] **Step 3: 运行前端契约与语法测试**

~~~powershell
node --check web_script.js
node --check dashboard/app.js
node --test --test-isolation=none --test-name-pattern="HR active configuration" tests\test_dashboard_contract.js
~~~

### Task 5: 全量验证、清理进程并提交

**Files:**
- Verify: `app/`、`web_script.js`、`dashboard/`
- Commit: 设计文档、实施计划、实现与说明文件；保留工作区中用户的无关改动

- [x] **Step 1: 运行完整测试套件**

运行 `.\scripts\run_tests.ps1`。预期 HR 相关测试和 Python 既有测试通过；若工作区并行改动导致无关测试导入或旧契约失败，单独运行并记录这些基线问题，不修改无关模块。

- [x] **Step 2: 检查差异和格式**

运行 `git diff --check`、`git status --short`、`node --check` 和针对 HR 状态的 `rg`，确认没有修改 `user_config.json`、日志、数据库或无关用户文件。

- [x] **Step 3: 关闭本次运行涉及的项目进程**

检查监听端口和进程列表，结束本次任务启动的开发服务器、测试服务器或项目后台进程；不结束无关系统进程。

- [x] **Step 4: 提交到 main**

确认当前分支为 `main`，只暂存本任务文件（对混有用户改动的 `web_script.js` 只暂存 HR hunks），执行中文提交：

~~~powershell
git add -f docs/superpowers/specs/2026-07-19-hr-active-filter-completion-design.md docs/superpowers/plans/2026-07-19-hr-active-filter-completion.md
git add app/config.py app/storage/admin_store.py dashboard/app.js readme.md user_config.example.json
git add web_script.js
git commit -m "修复：补全HR活跃时间筛选并严格过滤未知状态"
~~~

提交后用 `git status --short --branch` 验证工作区干净（除用户未跟踪文件外不应有本任务残留）。
