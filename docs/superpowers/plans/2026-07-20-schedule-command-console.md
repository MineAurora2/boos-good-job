# 定时投递运行编排台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有定时投递卡片重做为只有一个总开关、启用后设置项平铺、状态与保存路径清晰的运行编排台。

**Architecture:** 保留原生 HTML/CSS/JavaScript、SVG 圆盘、现有 API 和调度字段。HTML 调整卡片内部信息架构，JavaScript 增加可测试的草稿状态模型和 ARIA 错误状态，CSS 负责紧凑双栏与单列响应式；不引入新依赖或后端变更。

**Tech Stack:** 原生 HTML、CSS、JavaScript、SVG、Node `node:test`、PowerShell、Browser/IAB。

---

### Task 1: 锁定单一总开关和平铺编排台契约

**Files:**
- Modify: `tests/test_dashboard_schedule.js`
- Test: `tests/test_dashboard_schedule.js`

- [ ] **Step 1: 添加失败的 DOM 与 CSS 契约测试**

新增断言，要求定时卡片只包含一个 `.schedule-toggle`，包含 `schedule-workspace`、`schedule-setting-row`、`scheduleDailyHint` 和 `scheduleDraftState`，并且没有 `details`、内部折叠按钮或持续时长加减按钮：

```javascript
assert.equal((card.match(/class="schedule-toggle"/g) || []).length, 1);
assert.match(card, /class="schedule-composer schedule-workspace"/);
assert.match(card, /class="schedule-setting-row"/);
assert.match(card, /id="scheduleDailyHint"[^>]*data-schedule-section="daily"/);
assert.match(card, /id="scheduleDraftState"/);
assert.doesNotMatch(card, /<details\b|scheduleDuration(?:Decrease|Increase)/);
assert.match(stylesSource, /\.schedule-duration-dial[^,{]*\{[^}]*width\s*:\s*min\(266px,\s*100%\)/);
assert.match(stylesSource, /\.schedule-settings[^,{]*\{[^}]*min-height\s*:\s*0/);
```

- [ ] **Step 2: 添加失败的草稿状态纯函数测试**

从 `app.js` 提取 `scheduleDraftStatus` 并验证四个状态：

```javascript
const { scheduleDraftStatus } = evaluate(`
    ${extractFunction('scheduleDraftStatus')}
    ({ scheduleDraftStatus });
`);
assert.equal(scheduleDraftStatus({ enabled: true }, true, false).text, '有未保存的更改');
assert.equal(scheduleDraftStatus({ enabled: false }, true, false).text, '计划将关闭，尚未保存');
assert.equal(scheduleDraftStatus({ enabled: true }, true, true).buttonLabel, '保存中…');
assert.equal(scheduleDraftStatus({ enabled: true }, false, false).tone, 'synced');
```

- [ ] **Step 3: 运行测试并确认正确失败**

运行：

```powershell
node --test --test-isolation=none tests/test_dashboard_schedule.js
```

预期：因缺少新 DOM、紧凑圆盘样式和 `scheduleDraftStatus` 而失败，不出现测试语法错误。

### Task 2: 重构平铺标记与视图状态

**Files:**
- Modify: `dashboard/index.html:130-231`
- Modify: `dashboard/app.js:1980-2185`
- Test: `tests/test_dashboard_schedule.js`

- [ ] **Step 1: 重组设置区标记**

保留唯一总开关和折叠容器，将工作区改为以下骨架：

```html
<div class="schedule-composer schedule-workspace">
    <section class="schedule-settings" aria-label="投递计划设置">
        <div class="schedule-field-group">...</div>
        <div class="schedule-setting-row">
            <div id="scheduleTimeField" class="schedule-field-group">...</div>
            <div class="schedule-mode-detail">
                <div id="scheduleDailyHint" class="schedule-hint" data-schedule-section="daily">...</div>
                <div id="scheduleWeekdays" ...>...</div>
                <div id="scheduleWeekdayHint" ...>...</div>
                <div id="scheduleDateRange" ...>...</div>
            </div>
        </div>
        <div class="schedule-plan-summary">...</div>
    </section>
    <section class="schedule-duration-panel">...</section>
</div>
```

在操作栏加入 `<span id="scheduleDraftState" class="schedule-draft-state"></span>`。给开始时间、日期范围和圆盘句柄分别增加对应错误节点的 `aria-describedby`。

- [ ] **Step 2: 添加草稿状态模型并接入渲染**

在 `app.js` 增加纯函数：

```javascript
function scheduleDraftStatus(schedule, hasDraft, saving) {
    if (saving) return { tone: 'saving', text: '正在保存计划…', buttonLabel: '保存中…' };
    if (!hasDraft) return { tone: 'synced', text: schedule.enabled ? '设置已同步' : '', buttonLabel: '保存并立即应用' };
    if (!schedule.enabled) return { tone: 'closing', text: '计划将关闭，尚未保存', buttonLabel: '保存并立即应用' };
    return { tone: 'dirty', text: '有未保存的更改', buttonLabel: '保存并立即应用' };
}
```

`renderSchedulePanel()` 根据 `state.scheduleDraft` 和 `state.scheduleSaving` 更新 `#scheduleDraftState`、`data-tone`、按钮文本、`disabled` 与 `aria-busy`。操作栏继续在启用状态或存在关闭草稿时显示。

- [ ] **Step 3: 补齐错误 ARIA 状态**

`clearScheduleErrors()` 同时移除 `.schedule-field-invalid` 和 `aria-invalid`；`showScheduleValidationError()` 在实际聚焦控件上设置 `aria-invalid="true"`，并保持现有错误文本和自动聚焦行为。

- [ ] **Step 4: 运行定向测试**

运行：

```powershell
node --check dashboard/app.js
node --test --test-isolation=none tests/test_dashboard_schedule.js
```

预期：语法检查成功，定时投递测试全部通过。

### Task 3: 实现运行编排台视觉系统与响应式布局

**Files:**
- Modify: `dashboard/styles.css:211-307`
- Modify: `dashboard/styles.css:950-980`
- Test: `tests/test_dashboard_schedule.js`

- [ ] **Step 1: 重做桌面信息层级**

将工作区设为左侧设置、右侧 266px 圆盘；设置区取消固定高度，并使用平铺双列：

```css
.schedule-workspace { grid-template-columns:minmax(0,1fr) minmax(244px,286px); gap:20px; align-items:stretch; }
.schedule-settings { min-height:0; padding:2px 20px 2px 0; }
.schedule-setting-row { display:grid; grid-template-columns:minmax(180px,.8fr) minmax(260px,1.2fr); gap:12px; align-items:start; }
.schedule-duration-dial { width:min(266px,100%); }
```

压低圆盘辉光，使用现有 `--line`、`--surface-input`、`--cyan`、`--green` 和 6–8px 圆角。状态条、预览和操作栏使用分隔线形成层级，不创建嵌套卡片。

- [ ] **Step 2: 完善草稿与保存状态样式**

为 `schedule-draft-state[data-tone="dirty|closing|saving|synced"]` 提供 cyan、red/amber、muted、green 范围内的克制状态色；保存按钮保持现有 green 主操作样式，禁用时不改变布局尺寸。

- [ ] **Step 3: 调整 680px 与 430px 断点**

680px 以下工作区单列、圆盘在上、设置在下；430px 以下圆盘为 236px、周期为两列、设置行单列、保存按钮撑满可用宽度。日期和时间弹层保留底部定位、`overflow-y:auto` 和 `overscroll-behavior:contain`。

- [ ] **Step 4: 运行定向测试并检查差异**

运行：

```powershell
node --test --test-isolation=none tests/test_dashboard_schedule.js
git diff --check
```

预期：全部通过，差异检查无输出。

### Task 4: 完整回归、视觉验收与提交

**Files:**
- Modify: `dashboard/index.html`
- Modify: `dashboard/styles.css`
- Modify: `dashboard/app.js`
- Modify: `tests/test_dashboard_schedule.js`

- [ ] **Step 1: 运行完整测试**

运行：

```powershell
scripts/run_tests.ps1
```

预期：Python、API、用户脚本语法、Dashboard 语法和全部 Node 契约测试通过。

- [ ] **Step 2: 使用 Browser 验证真实页面**

启动项目服务，验证深色/浅色和 1280px、680px、390px：关闭态无空白，打开态设置全部平铺，圆盘比例正确，无横向溢出。走通每天、每周、工作日、日期范围、跨午夜、键盘圆盘、校验错误、保存中、关闭待保存和共享日期弹层。

- [ ] **Step 3: 对照批准设计截图**

使用 `view_image` 同时检查批准的 A 方案视觉稿与最终 Browser 截图，核对布局、字号、边框、间距、圆盘占比、主按钮、移动端顺序和弹层位置；修复所有可见偏差。

- [ ] **Step 4: 清理并提交**

关闭本次启动的 Browser 标签、可视化伴侣、项目服务和相关进程。确认工作区只包含本任务文件后运行：

```powershell
git add dashboard/index.html dashboard/styles.css dashboard/app.js tests/test_dashboard_schedule.js docs/superpowers/plans/2026-07-20-schedule-command-console.md
git commit -m "美化：重制定时投递运行编排台"
```

预期：当前 `main` 产生中文功能提交，工作区无未提交修改。
