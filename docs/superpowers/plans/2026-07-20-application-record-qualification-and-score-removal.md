# 投递记录经验学历采集与匹配度移除 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让新版油猴脚本可靠采集岗位经验和学历，并让“全部投递记录”的表格、详情与 CSV 不再展示匹配度。

**Architecture:** 在 `web_script.js` 中增加一个只读资格提取函数，优先读取 BOSS 当前岗位头部元素，并以页面描述元数据作为降级来源；现有动作日志与 Dashboard 聚合链路保持不变。Dashboard 仅删除投递记录的 `score` 展示入口，评分算法、AI 筛选、阈值判断和评分配置继续保留。

**Tech Stack:** 原生 JavaScript 油猴脚本、FastAPI 静态 Dashboard、Node.js `node:test` 契约测试、PowerShell 测试入口、Chrome 浏览器检查。

---

## File Structure

- Modify: `web_script.js` - 定义岗位资格选择器、提取函数、版本号和测试钩子，并让职位详情采集调用该函数。
- Modify locally: `tests/test_web_script.js` - 用无浏览器副作用的根节点桩覆盖当前 DOM、兼容拼写、元数据降级和空值。
- Modify: `dashboard/app.js` - 删除投递记录匹配度列、单元格、详情和 CSV 字段。
- Modify: `dashboard/styles.css` - 删除只服务于匹配度进度条的样式。
- Modify: `dashboard/index.html` - 更新 Dashboard 静态资源缓存版本。
- Modify locally: `tests/test_dashboard_contract.js` - 增加匹配度移除与旧列偏好过滤契约，并更新资源版本断言。

`tests/` 被仓库 `.gitignore` 明确忽略，因此测试修改用于当前工作区的红绿验证，不使用 `git add -f` 将整份本地测试夹具引入主仓库。

### Task 1: Fix BOSS qualification extraction

**Files:**
- Modify: `tests/test_web_script.js` near the existing job-detail selector tests
- Modify: `web_script.js:3-17`
- Modify: `web_script.js:322-336`
- Modify: `web_script.js:3818-3834`
- Modify: `web_script.js:4610-4637`

- [ ] **Step 1: Write the failing qualification tests**

Add these focused tests to `tests/test_web_script.js`:

```js
test('job qualification extraction reads the current BOSS header fields', () => {
    const { hooks } = loadHooks();
    const values = new Map([
        ['.job-primary .text-experiece, .job-primary .text-experience', { innerText: ' 1-3年 ' }],
        ['.job-primary .text-degree', { innerText: ' 本科 ' }],
    ]);
    const selectors = [];
    const root = {
        querySelector(selector) {
            selectors.push(selector);
            return values.get(selector) || null;
        },
    };

    const result = hooks.extractJobQualifications(root);

    assert.equal(result.experience, '1-3年');
    assert.equal(result.education, '本科');
    assert.ok(selectors.some((selector) => selector.includes('.text-experiece')));
    assert.ok(selectors.some((selector) => selector.includes('.text-experience')));
    assert.ok(selectors.some((selector) => selector.includes('.text-degree')));
});

test('job qualification extraction falls back to BOSS metadata', () => {
    const { hooks } = loadHooks();
    const root = {
        querySelector(selector) {
            if (selector === 'meta[name="description"]') {
                return {
                    getAttribute(name) {
                        return name === 'content'
                            ? '示例公司运维工程师招聘，薪资：10-15K，地点：深圳，要求：5-10年，学历：硕士，福利：五险一金。'
                            : null;
                    },
                };
            }
            return null;
        },
    };

    const result = hooks.extractJobQualifications(root);

    assert.equal(result.experience, '5-10年');
    assert.equal(result.education, '硕士');
});

test('job qualification extraction stays empty when every source is missing', () => {
    const { hooks } = loadHooks();
    const result = hooks.extractJobQualifications({ querySelector() { return null; } });

    assert.equal(result.experience, '');
    assert.equal(result.education, '');
});
```

- [ ] **Step 2: Run the userscript tests and verify RED**

Run:

```powershell
node --test --test-isolation=none tests/test_web_script.js
```

Expected: the three new tests fail because `hooks.extractJobQualifications` does not exist.

- [ ] **Step 3: Add selectors and the pure extraction function**

Replace `QUALIFICATION_TAGS` in `SELECTORS.ZHIPIN.DETAIL` with:

```js
EXPERIENCE: '.job-primary .text-experiece, .job-primary .text-experience',
EDUCATION: '.job-primary .text-degree',
METADATA_DESCRIPTION: 'meta[name="description"]',
```

Add this function immediately after `SELECTORS`:

```js
function extractJobQualifications(root = document) {
    const readText = (selector) => root?.querySelector?.(selector)?.innerText?.trim() || '';
    const directExperience = readText(SELECTORS.ZHIPIN.DETAIL.EXPERIENCE);
    const directEducation = readText(SELECTORS.ZHIPIN.DETAIL.EDUCATION);
    if (directExperience && directEducation) {
        return { experience: directExperience, education: directEducation };
    }

    const description = root?.querySelector?.(SELECTORS.ZHIPIN.DETAIL.METADATA_DESCRIPTION)
        ?.getAttribute?.('content') || '';
    const metadataExperience = description.match(/要求[：:]\s*([^，,。；;]+)/)?.[1]?.trim() || '';
    const metadataEducation = description.match(/学历[：:]\s*([^，,。；;]+)/)?.[1]?.trim() || '';
    return {
        experience: directExperience || metadataExperience,
        education: directEducation || metadataEducation,
    };
}
```

- [ ] **Step 4: Wire the extractor into job detail collection**

Replace the `qualificationTexts` scan in `getJobInfo()` with:

```js
const { experience, education } = extractJobQualifications(document);
```

Add `extractJobQualifications` to `window.__GOODJOBS_TEST_HOOKS__`.

- [ ] **Step 5: Bump and align the userscript version**

Change both metadata and runtime constants to the same value:

```js
// @version      2026-07-20-application-records.1
const SCRIPT_VERSION = '2026-07-20-application-records.1';
```

Update the expected version in `tests/test_web_script.js` to `2026-07-20-application-records.1`.

- [ ] **Step 6: Run userscript tests and syntax check for GREEN**

Run:

```powershell
node --test --test-isolation=none tests/test_web_script.js
node --check web_script.js
```

Expected: both commands exit with code 0; the qualification tests report `1-3年`/`本科`, metadata fallback reports `5-10年`/`硕士`, and all prior tests remain green.

### Task 2: Remove matching score from application records

**Files:**
- Modify: `tests/test_dashboard_contract.js`
- Modify: `dashboard/app.js:1-35`
- Modify: `dashboard/app.js:1114-1158`
- Modify: `dashboard/app.js:1212-1239`
- Modify: `dashboard/app.js:1400-1406`
- Modify: `dashboard/app.js:2002-2006`
- Modify: `dashboard/styles.css:413-414`
- Modify: `dashboard/styles.css:773`
- Modify: `dashboard/index.html:9`
- Modify: `dashboard/index.html:450`

- [ ] **Step 1: Write the failing Dashboard contract test**

Add this test to `tests/test_dashboard_contract.js`:

```js
test('application records omit matching score from every user-facing surface', () => {
    const stateSource = extractConst('state');
    const columnsSource = extractConst('TABLE_COLUMNS');
    const cellSource = extractFunction('renderTableCell');
    const preferencesSource = extractFunction('restoreTablePreferences');
    const drawerSource = extractFunction('openDrawer');
    const exportSource = extractFunction('exportRecords');

    assert.doesNotMatch(stateSource, /['"]score['"]/);
    assert.doesNotMatch(columnsSource, /\bscore\s*:/);
    assert.doesNotMatch(cellSource, /record\.score|key === 'score'|score-pill/);
    assert.doesNotMatch(drawerSource, /匹配度|record\.score/);
    assert.doesNotMatch(exportSource, /匹配度|record\.score/);
    assert.doesNotMatch(stylesSource, /\.score-pill/);
    assert.match(preferencesSource, /saved\.visibleColumns\.filter\(\(key\) => validKeys\.includes\(key\)\)/);
    assert.match(preferencesSource, /saved\.columnOrder\.filter\(\(key\) => validKeys\.includes\(key\)\)/);
    assert.match(appSource, /score-card-grid/);
});
```

- [ ] **Step 2: Run the Dashboard contract test and verify RED**

Run:

```powershell
node --test --test-isolation=none tests/test_dashboard_contract.js
```

Expected: the new test fails on the existing `score` column, cell renderer, drawer, CSV and `.score-pill` styles.

- [ ] **Step 3: Remove the score column and renderer**

In the initial `state`, use these application record columns:

```js
visibleColumns: new Set(['company', 'salary', 'city', 'industry', 'experience', 'education', 'hrActive', 'loggedAt', 'status']),
columnOrder: ['company', 'salary', 'city', 'industry', 'experience', 'education', 'hrActive', 'loggedAt', 'status'],
```

Delete the `score` entry from `TABLE_COLUMNS`. In `renderTableCell`, delete the `score` local variable and the `key === 'score'` branch. Keep the generic preference filtering unchanged so persisted `score` keys are dropped against `Object.keys(TABLE_COLUMNS)`.

- [ ] **Step 4: Remove score from detail and CSV output**

Set the drawer details to:

```js
const details = [['薪资范围', record.salary], ['城市 / 地区', record.city || record.location || '历史记录未采集'], ['经验要求', record.experience || '未采集'], ['学历要求', record.education || '未采集'], ['所属行业', record.industry || '未采集'], ['HR 活跃', record.hrActive || HR_ACTIVE_LABELS[record.hrActiveLevel] || '未知'], ['搜索关键词', record.keyword || '未记录'], ['投递日期', `${datetime.date} ${datetime.time}`], ['投递账号', record.accountId || '默认账号']];
```

Set the CSV header and record row to:

```js
const rows = [['公司', '岗位', '行业', '薪资', '最低K', '最高K', '城市', '经验', '学历', 'HR 活跃', '关键词', '投递时间', '状态', '账号']];
records.forEach((record) => rows.push([record.company, record.title, record.industry, record.salary, record.salaryMinK ?? '', record.salaryMaxK ?? '', record.city || record.location, record.experience, record.education, record.hrActive || HR_ACTIVE_LABELS[record.hrActiveLevel] || '', record.keyword, record.loggedAt, STATUS_LABELS[record.status] || record.status, record.accountId]));
```

- [ ] **Step 5: Remove dedicated score-pill styles**

Delete these rules from `dashboard/styles.css`:

```css
.score-pill { display: inline-flex; align-items: center; gap: 6px; }
.score-pill i { width: 26px; height: 3px; overflow: hidden; border-radius: 3px; background: #1a2a35; }.score-pill i span { display: block; height: 100%; background: var(--violet); }
html[data-theme="light"] .score-pill i { background: #dbe6ea; }
```

Do not remove `.score-card-*`, `.score-keyword-*` or scoring configuration code.

- [ ] **Step 6: Bump Dashboard asset cache keys**

Change both static resource URLs in `dashboard/index.html` to:

```html
<link rel="stylesheet" href="/dashboard/styles.css?v=20260720-application-records-1">
<script src="/dashboard/app.js?v=20260720-application-records-1" defer></script>
```

Replace the three old hard-coded cache version expectations in `tests/test_dashboard_contract.js` with `20260720-application-records-1`.

- [ ] **Step 7: Run Dashboard tests and syntax check for GREEN**

Run:

```powershell
node --test --test-isolation=none tests/test_dashboard_contract.js
node --check dashboard/app.js
```

Expected: both commands exit with code 0; the new test confirms all application-record score surfaces are absent while scoring configuration contracts remain present.

### Task 3: Verify, inspect, clean up and commit

**Files:**
- Verify: `web_script.js`
- Verify: `dashboard/app.js`
- Verify: `dashboard/styles.css`
- Verify: `dashboard/index.html`
- Verify locally: `tests/test_web_script.js`
- Verify locally: `tests/test_dashboard_contract.js`
- Commit: runtime files plus the implementation plan document; the design document is already committed

- [ ] **Step 1: Run the complete repository verification**

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1
```

Expected: compile checks, Python unit/API tests, userscript syntax, Dashboard syntax and every local Node test exit with code 0, ending with `All checks passed.`

- [ ] **Step 2: Start the Dashboard only for browser QA**

Run this as a long-lived command and retain its execution handle:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 47999 --no-proxy-headers --log-level info
```

Expected: Uvicorn reports `Uvicorn running on http://127.0.0.1:47999`. This imports `main:app` without calling `run_server()`, so it does not start the repository's automatic dashboard opener.

- [ ] **Step 3: Inspect the application-record UI in Chrome**

Open `http://127.0.0.1:47999/dashboard` in a temporary Chrome tab. Verify:

```text
表头和列设置：无“匹配度”
记录详情：无“匹配度”
经验、学历筛选与列：仍存在
控制台：无新增 error
```

Use the current local action log only as display data. Do not delete, export or mutate any record during QA.

- [ ] **Step 4: Close task-started processes and browser tabs**

Finalize the temporary Chrome tab, terminate the retained execution handle from Step 2, and wait for that command to exit. Confirm that the handle reports completion; preserve every unrelated user process.

- [ ] **Step 5: Review the diff and stage only intended files**

Run:

```powershell
git diff --check
git status --short
git diff -- web_script.js dashboard/app.js dashboard/styles.css dashboard/index.html
```

Expected: runtime diffs are limited to qualification extraction, application-record score removal and cache versions. Exclude `reports/~$2026-07-19-delivery-conversion-review.pptx`, local databases/logs and ignored local tests.

- [ ] **Step 6: Commit the completed implementation to main with a Chinese summary**

Run:

```powershell
git add -- web_script.js dashboard/app.js dashboard/styles.css dashboard/index.html
git add -f -- docs/superpowers/plans/2026-07-20-application-record-qualification-and-score-removal.md
git commit -m "修复：采集投递经验学历并移除匹配度"
```

Expected: commit succeeds on `main`; `git status --short --branch` shows only the pre-existing untracked PowerPoint lock file.
