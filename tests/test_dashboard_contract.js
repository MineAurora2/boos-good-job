'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const APP_PATH = path.join(ROOT, 'dashboard', 'app.js');
const HTML_PATH = path.join(ROOT, 'dashboard', 'index.html');
const STYLES_PATH = path.join(ROOT, 'dashboard', 'styles.css');
const README_PATH = path.join(ROOT, 'readme.md');
const appSource = fs.readFileSync(APP_PATH, 'utf8');
const htmlSource = fs.readFileSync(HTML_PATH, 'utf8');
const stylesSource = fs.readFileSync(STYLES_PATH, 'utf8');
const readmeSource = fs.readFileSync(README_PATH, 'utf8');

function balancedSource(source, start, open, close) {
    const openAt = source.indexOf(open, start);
    assert.notEqual(openAt, -1, `missing ${open} after offset ${start}`);
    let depth = 0;
    let quote = '';
    let escaped = false;
    for (let index = openAt; index < source.length; index += 1) {
        const char = source[index];
        if (escaped) { escaped = false; continue; }
        if (quote) {
            if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') { quote = char; continue; }
        if (char === open) depth += 1;
        if (char === close) {
            depth -= 1;
            if (depth === 0) return source.slice(start, index + 1);
        }
    }
    assert.fail(`unterminated block beginning at offset ${start}`);
}

function matchingDelimiter(source, openAt, open, close) {
    let depth = 0;
    let quote = '';
    let escaped = false;
    for (let index = openAt; index < source.length; index += 1) {
        const char = source[index];
        if (escaped) { escaped = false; continue; }
        if (quote) {
            if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') { quote = char; continue; }
        if (char === open) depth += 1;
        if (char === close && --depth === 0) return index;
    }
    assert.fail(`unterminated ${open}${close} block at offset ${openAt}`);
}

function extractConst(name) {
    const start = appSource.indexOf(`const ${name} =`);
    assert.notEqual(start, -1, `missing const ${name}`);
    return `${balancedSource(appSource, start, '{', '}')};`;
}

function extractFunction(name) {
    const start = appSource.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `missing function ${name}`);
    const parametersOpen = appSource.indexOf('(', start);
    const parametersClose = matchingDelimiter(appSource, parametersOpen, '(', ')');
    const bodyOpen = appSource.indexOf('{', parametersClose);
    const body = balancedSource(appSource, bodyOpen, '{', '}');
    return appSource.slice(start, bodyOpen) + body;
}

function evaluate(expressionSource) {
    return vm.runInNewContext(expressionSource, Object.create(null));
}

test('application records omit matching score from every user-facing surface', () => {
    const stateSource = extractConst('state');
    const tableColumnsSource = extractConst('TABLE_COLUMNS');
    const renderCellSource = extractFunction('renderTableCell');
    const restorePreferencesSource = extractFunction('restoreTablePreferences');
    const drawerSource = extractFunction('openDrawer');
    const exportSource = extractFunction('exportRecords');
    const defaults = evaluate(`
        ${stateSource}
        function recordCity() {}
        ${tableColumnsSource}
        ({
            visibleColumns: [...state.visibleColumns],
            columnOrder: state.columnOrder,
            tableKeys: Object.keys(TABLE_COLUMNS),
        });
    `);

    assert.equal(defaults.visibleColumns.includes('score'), false);
    assert.equal(defaults.columnOrder.includes('score'), false);
    assert.equal(defaults.tableKeys.includes('score'), false);
    assert.doesNotMatch(renderCellSource, /record\.score|key === 'score'|score-pill/);
    assert.doesNotMatch(drawerSource, /匹配度|record\.score/);
    assert.doesNotMatch(exportSource, /匹配度|record\.score/);
    assert.doesNotMatch(stylesSource, /\.score-pill(?:\s|\{|\.)/);

    assert.match(restorePreferencesSource, /const validKeys = Object\.keys\(TABLE_COLUMNS\)/);
    assert.match(restorePreferencesSource, /saved\.visibleColumns\.filter\(\(key\) => validKeys\.includes\(key\)\)/);
    assert.match(restorePreferencesSource, /saved\.columnOrder\.filter\(\(key\) => validKeys\.includes\(key\)\)/);
    const restored = evaluate(`
        function recordCity() {}
        ${tableColumnsSource}
        const state = { visibleColumns: new Set(), columnOrder: [], density: 'compact' };
        const TABLE_PREFS_KEY = 'dashboard-table-preferences';
        const localStorage = {
            getItem() {
                return JSON.stringify({
                    visibleColumns: ['company', 'score', 'status'],
                    columnOrder: ['score', 'status', 'company'],
                });
            },
        };
        function $() { return { dataset: {} }; }
        function $$() { return []; }
        function renderColumnManager() {}
        ${restorePreferencesSource}
        restoreTablePreferences();
        ({ visibleColumns: [...state.visibleColumns], columnOrder: state.columnOrder });
    `);
    assert.equal(restored.visibleColumns.includes('score'), false);
    assert.equal(restored.columnOrder.includes('score'), false);
    assert.match(stylesSource, /\.score-card-grid\s*\{/);
});

test('HR active configuration uses the complete multi-select allow-list', () => {
    const source = `${extractConst('CONFIG_MULTI_OPTIONS')} CONFIG_MULTI_OPTIONS;`;
    const options = evaluate(source);
    assert.deepEqual(Array.from(options.hrActiveLevels, ([value]) => value), [
        'online', 'just_now', 'today', 'within_3_days', 'this_week', 'within_2_weeks',
        'this_month', 'within_2_months', 'within_3_months', 'within_4_months',
        'within_5_months', 'within_half_year', 'half_year_ago', 'unknown',
    ]);
    const renderer = extractFunction('renderConfigMultiOptions');
    const saveSource = extractFunction('saveAdminConfig');
    assert.match(renderer, /dataset\.valueType = 'enum-multi'/);
    assert.match(renderer, /checkbox\.type = 'checkbox'/);
    assert.match(saveSource, /input\.dataset\.valueType === 'enum-multi'/);
    assert.match(saveSource, /input\[type="checkbox"\]:checked/);
    assert.match(stylesSource, /\.config-multi-options/);
});

test('top range switch supports a local-calendar today filter', () => {
    assert.match(appSource, /range:\s*'today'/);
    assert.match(htmlSource, /<button class="active" data-range="today">今日<\/button>/);
    assert.doesNotMatch(htmlSource, /class="active" data-range="30"/);
    assert.match(htmlSource, /app\.js\?v=20260720-delivery-gate-v2-2/);
    const rangeStartDate = evaluate(`${extractFunction('rangeStartDate')} rangeStartDate;`);
    const now = new Date(2026, 6, 17, 18, 30, 0);
    const today = rangeStartDate('today', now);
    const sevenDays = rangeStartDate('7', now);
    assert.deepEqual(
        [today.getFullYear(), today.getMonth(), today.getDate(), today.getHours()],
        [2026, 6, 17, 0],
    );
    assert.deepEqual(
        [sevenDays.getFullYear(), sevenDays.getMonth(), sevenDays.getDate()],
        [2026, 6, 11],
    );
    assert.equal(rangeStartDate('all', now), null);
});

test('account AI decision reasons remain fully readable', () => {
    assert.match(htmlSource, /styles\.css\?v=20260720-delivery-gate-v2-2/);
    assert.match(htmlSource, /app\.js\?v=20260720-delivery-gate-v2-2/);
    assert.match(stylesSource, /\.decision-ai\s*\{[^}]*display:grid[^}]*grid-template-columns:max-content minmax\(0,1fr\)/);
    assert.match(stylesSource, /\.decision-ai em\s*\{[^}]*overflow:visible[^}]*overflow-wrap:anywhere[^}]*white-space:normal/);
    assert.doesNotMatch(stylesSource, /\.decision-ai em\s*\{[^}]*text-overflow:ellipsis/);
});

test('LLM interface management exposes an independently loaded usage panel', () => {
    const version = '20260720-delivery-gate-v2-2';
    assert.match(htmlSource, new RegExp(`styles\\.css\\?v=${version}`));
    assert.match(htmlSource, new RegExp(`app\\.js\\?v=${version}`));

    const llmViewStart = htmlSource.indexOf('<div class="admin-view" data-admin-view="llm">');
    const llmViewEnd = htmlSource.indexOf('<div class="admin-view" data-admin-view="resume">', llmViewStart);
    assert.ok(llmViewStart >= 0 && llmViewEnd > llmViewStart, 'LLM admin view should remain before resume view');
    const llmView = htmlSource.slice(llmViewStart, llmViewEnd);
    const headEnd = llmView.indexOf('</div>', llmView.indexOf('class="llm-console-head"'));
    const usageAt = llmView.indexOf('id="llmUsagePanel"');
    assert.ok(usageAt > headEnd, 'usage panel should follow the LLM console heading');
    assert.match(llmView, /id="llmUsagePanel"/);
    assert.match(llmView, /data-llm-usage-days="1"/);
    assert.match(llmView, /data-llm-usage-days="7"[^>]*class="active"|class="active"[^>]*data-llm-usage-days="7"/);
    assert.match(llmView, /data-llm-usage-days="30"/);
    assert.match(llmView, /id="llmUsageRefresh"[^>]*type="button"/);
    assert.match(llmView, /id="llmUsageStatus"/);
    assert.match(llmView, /id="llmUsageError"/);
    ['llmUsageTotalTokens', 'llmUsageInputTokens', 'llmUsageOutputTokens', 'llmUsageUpstreamCalls', 'llmUsageSuccessRate', 'llmUsageTrend', 'llmUsageProviderRanking', 'llmUsagePurposeRanking'].forEach((id) => {
        assert.match(llmView, new RegExp(`id="${id}"`), `missing ${id}`);
    });
    assert.match(stylesSource, /#llmUsageTrend|\.llm-usage-trend/);
    assert.match(stylesSource, /\.llm-usage-grid\s*\{[^}]*grid-template-columns:\s*repeat\(12/);
    assert.match(stylesSource, /@media \(max-width: 680px\)[\s\S]*llm-usage/);

    assert.match(appSource, /llmUsage:\s*\{[\s\S]*?days:\s*7[\s\S]*?data:\s*null[\s\S]*?loading:\s*false[\s\S]*?error:\s*null[\s\S]*?chart:/);
    assert.match(appSource, /function loadLlmUsage\(/);
    assert.match(appSource, /\/api\/admin\/llm\/usage\?days=/);
    const loadAdminSource = extractFunction('loadAdmin');
    assert.doesNotMatch(loadAdminSource, /llm\/usage/);
    assert.match(appSource, /usageReportedCalls/);
    assert.match(appSource, /inputTokens/);
    assert.match(appSource, /outputTokens/);
    assert.match(appSource, /successRate/);
    assert.match(appSource, /introduce_retry[\s\S]*招呼语纠正重试/);
    assert.match(appSource, /job_filter_retry[\s\S]*岗位筛选纠正重试/);
    assert.match(appSource, /connection_test[\s\S]*接口测活/);
    assert.match(appSource, /textContent/);
    assert.match(appSource, /stack:\s*'total'/);
    assert.match(appSource, /type:\s*'bar'/);
    assert.match(appSource, /type:\s*'line'/);
    assert.match(appSource, /applyTheme[\s\S]*llmUsage/);
    assert.match(appSource, /data-llm-usage-days/);
    assert.match(appSource, /setInterval\([^\n]*loadLlmUsage|loadLlmUsage[\s\S]*setInterval/);
});

test('LLM usage purpose labels stay explicit and safe for unknown purposes', () => {
    const source = extractFunction('llmUsagePurposeLabel');
    const helpers = evaluate(`${source} ({ llmUsagePurposeLabel });`);
    assert.equal(helpers.llmUsagePurposeLabel('introduce'), '招呼语生成');
    assert.equal(helpers.llmUsagePurposeLabel('introduce_retry'), '招呼语纠正重试');
    assert.equal(helpers.llmUsagePurposeLabel('job_filter'), '岗位筛选');
    assert.equal(helpers.llmUsagePurposeLabel('job_filter_retry'), '岗位筛选纠正重试');
    assert.equal(helpers.llmUsagePurposeLabel('connection_test'), '接口测活');
    assert.equal(helpers.llmUsagePurposeLabel('other_purpose'), 'other_purpose');
});

test('LLM provider identity survives editing, reordering, and health checks', () => {
    const cardSource = extractFunction('llmProviderCard');
    const collectSource = extractFunction('collectLlmProvider');
    const testSource = extractFunction('testLlmProvider');
    assert.match(cardSource, /dataset\.providerId\s*=\s*String\(provider\.providerId/);
    assert.match(collectSource, /providerId:\s*[^,]+/);
    assert.match(collectSource, /providerId[\s\S]*LLM_KEEP_SECRET|providerId[\s\S]*null/);
    assert.match(testSource, /result\.providerId/);
    assert.match(testSource, /dataset\.providerId/);
});

test('current job decisions are collapsible details with an accessible summary', () => {
    const decisionSource = extractFunction('decisionMarkup');
    assert.match(decisionSource, /<details class="instance-decision"/);
    assert.match(decisionSource, /<summary class="instance-decision-summary">/);
    assert.match(decisionSource, /instance-decision-body/);
    assert.match(stylesSource, /\.instance-decision-summary:focus-visible/);
});

test('duplicate job decisions use a readable instance-card verdict', () => {
    assert.match(appSource, /duplicate:\s*'重复投递'/);
    const decisionSource = extractFunction('decisionMarkup');
    assert.match(decisionSource, /DECISION_STATE_LABELS\[stateLabel\]/);
    assert.match(decisionSource, /decision\.finalPassed === false/);
});

test('monitor filter delivery rate uses evaluated jobs from the selected date range', () => {
    assert.match(htmlSource, /<span>筛选投递率<\/span><strong id="filterDeliveryRate">/);
    assert.doesNotMatch(htmlSource, /id="monitorFailures"/);
    const helpers = evaluate(`${extractFunction('localDateKey')} ${extractFunction('rangeStartDate')} ${extractFunction('evaluatedJobsForRange')} ({ evaluatedJobsForRange });`);
    const summary = {
        evaluatedJobs: 15,
        evaluatedJobsByDate: [
            { date: '2026-07-10', count: 4 },
            { date: '2026-07-16', count: 5 },
            { date: '2026-07-17', count: 6 },
        ],
    };
    const now = new Date(2026, 6, 17, 18, 30, 0);
    assert.equal(helpers.evaluatedJobsForRange(summary, 'today', now), 6);
    assert.equal(helpers.evaluatedJobsForRange(summary, '7', now), 11);
    assert.equal(helpers.evaluatedJobsForRange(summary, 'all', now), 15);
    assert.equal(helpers.evaluatedJobsForRange({ evaluatedJobs: 15 }, 'today', now), null);
});

test('daily progress sums quotas for unique online script accounts only', () => {
    const helpers = evaluate(`
        const state = {
            control: {
                clients: [
                    { workerId: 'worker-a', accountId: 'account-a', online: true },
                    { workerId: 'worker-a-copy', accountId: 'account-a', online: true },
                    { workerId: 'worker-b', accountId: 'account-b', online: true },
                    { workerId: 'worker-old', accountId: 'account-old', online: false },
                    { workerId: 'worker-anonymous', accountId: '', online: true },
                ],
                quotas: {
                    'account-a': { count: 10, limit: 80 },
                    'account-b': { used: 5, limit: 60 },
                    'account-old': { count: 99, limit: 150 },
                },
            },
            adminConfig: { backend: { daily_greet_limit: 90 } },
        };
        const TODAY_TARGET_FALLBACK = 20;
        ${extractFunction('onlineScriptDailyProgress')}
        ({ state, onlineScriptDailyProgress });
    `);

    assert.deepEqual(
        JSON.parse(JSON.stringify(helpers.onlineScriptDailyProgress(42))),
        { used: 15, limit: 140 },
    );

    helpers.state.control.clients = [
        { workerId: 'worker-old', accountId: 'account-old', online: false },
    ];
    assert.deepEqual(
        JSON.parse(JSON.stringify(helpers.onlineScriptDailyProgress(42))),
        { used: 0, limit: 0 },
    );
});

test('daily progress renders an explicit zero state when no scripts are online', () => {
    const helpers = evaluate(`
        const elements = {
            '#todayHint': { textContent: '' },
            '#goalText': { textContent: '' },
            '#goalBar': { style: { width: '' } },
            '#goalHint': { textContent: '' },
        };
        const state = {
            control: { clients: [], quotas: {} },
            adminConfig: { backend: { daily_greet_limit: 90 } },
            todayDelivered: 12,
        };
        const TODAY_TARGET_FALLBACK = 20;
        function $(selector) { return elements[selector]; }
        ${extractFunction('onlineScriptDailyProgress')}
        ${extractFunction('renderDailyGoal')}
        ({ state, elements, renderDailyGoal });
    `);

    helpers.renderDailyGoal(12);
    assert.equal(helpers.elements['#todayHint'].textContent, '今日目标完成度 0%');
    assert.equal(helpers.elements['#goalText'].textContent, '0 / 0');
    assert.equal(helpers.elements['#goalBar'].style.width, '0%');
    assert.equal(helpers.elements['#goalHint'].textContent, '暂无在线脚本');
});

test('four monitor metrics fill the desktop row and retain responsive columns', () => {
    assert.match(
        stylesSource,
        /\.monitor-metrics\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/,
    );
    assert.match(
        stylesSource,
        /@media \(max-width: 920px\)[\s\S]*?\.monitor-metrics\s*\{[^}]*grid-template-columns:\s*1fr 1fr/,
    );
    assert.match(
        stylesSource,
        /@media \(max-width: 430px\)[\s\S]*?\.monitor-metrics\s*\{[^}]*grid-template-columns:\s*1fr/,
    );
});

test('delivery trend is rendered by the locally hosted ECharts component', () => {
    const echartsScript = '<script src="/dashboard/vendor/echarts.min.js" defer></script>';
    const appScript = '<script src="/dashboard/app.js?v=20260720-delivery-gate-v2-2" defer></script>';
    assert.ok(htmlSource.includes(echartsScript));
    assert.ok(htmlSource.indexOf(echartsScript) < htmlSource.indexOf(appScript));
    assert.match(htmlSource, /id="trendChart"[^>]*role="img"/);
    assert.doesNotMatch(htmlSource, /id="trendScroll"|id="trendZoomIn"|id="trendZoomOut"|class="trend-scrollbar"/);

    assert.match(appSource, /echarts\.init\(container/);
    assert.match(appSource, /dataZoom:\s*\[[\s\S]*?type:\s*'inside'[\s\S]*?type:\s*'slider'/);
    assert.match(appSource, /markLine:\s*\{[\s\S]*?type:\s*'average'/);
    assert.match(appSource, /areaStyle:\s*\{/);
    assert.match(appSource, /trendChartInstance\.on\('click'/);
    assert.match(appSource, /applyChartFilter\(\{ exactDate:/);
    assert.match(appSource, /trendChartInstance\?\.resize\(\)/);

    const trendRenderer = extractFunction('renderTrend');
    const trendBindings = extractFunction('bindTrendInteractions');
    assert.doesNotMatch(trendRenderer, /<svg|<polyline|<polygon|trend-dot|trend-drag-indicator/);
    assert.doesNotMatch(trendBindings, /pointerdown|pointermove|setPointerCapture|trendScroll/);
});

test('browser instance cards use readable type sizes throughout', () => {
    assert.match(stylesSource, /\.control-instance-name strong\s*\{[^}]*font-size:13px/);
    assert.match(stylesSource, /\.control-instance-name small\s*\{[^}]*font-size:10px/);
    assert.match(stylesSource, /\.control-instance-state\s*\{[^}]*font-size:11px/);
    assert.match(stylesSource, /\.control-state-axes small\s*\{[^}]*font-size:10px/);
    assert.match(stylesSource, /\.control-state-axes b\s*\{[^}]*font-size:11px/);
    assert.match(stylesSource, /\.control-instance-detail span\s*\{[^}]*font-size:10px/);
    assert.match(stylesSource, /\.control-instance-detail b\s*\{[^}]*font-size:11px/);
    assert.match(stylesSource, /\.control-instance-actions button\s*\{[^}]*font-size:11px/);
    assert.match(stylesSource, /\.control-action-feedback\s*\{[^}]*font-size:10px/);
    assert.match(stylesSource, /\.control-instance-quota-summary > span\s*\{[^}]*font-size:10px/);
    assert.match(stylesSource, /\.control-instance-quota-summary em\s*\{[^}]*font-size:10px/);
});

test('dashboard lifecycle controls use desired-state endpoints and expose all three state axes', () => {
    assert.equal((htmlSource.match(/data-control-scope="global" data-desired-state="(?:running|paused|stopped)"/g) || []).length, 3);
    assert.match(htmlSource, /id="runningClients"/);
    assert.match(htmlSource, /id="globalControlFeedback"[^>]*aria-live="polite"/);
    assert.match(appSource, /'\/api\/control\/desired-state\/global'/);
    assert.match(appSource, /`\/api\/control\/desired-state\/workers\/\$\{encodeURIComponent\(workerId\)\}`/);
    assert.match(extractFunction('setDesiredLifecycleState'), /JSON\.stringify\(\{ desiredState \}\)/);
    assert.match(extractFunction('lifecycleButtonsMarkup'), /disabled aria-busy="true"/);
    assert.match(extractFunction('renderControlInstances'), /request\?\.failed/);
    assert.match(extractFunction('controlAxesMarkup'), /<small>连接<\/small>/);
    assert.match(extractFunction('controlAxesMarkup'), /<small>执行<\/small>/);
    assert.match(extractFunction('controlAxesMarkup'), /<small>同步<\/small>/);
    assert.match(stylesSource, /\.control-state-axes\s*\{[^}]*grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
    assert.match(appSource, /const CONTROL_DELIVERY_POLL_INTERVAL_MS = 150/);
    assert.match(extractFunction('setDesiredLifecycleState'), /reconcileLifecycleDelivery\(scope, workerId, result\)/);
});

test('dashboard exposes the multi-cycle delivery schedule editor', () => {
    assert.match(htmlSource, /id="deliveryScheduleCard"/);
    assert.match(htmlSource, /id="scheduleEnabled"/);
    assert.match(htmlSource, /id="scheduleMode"/);
    assert.match(htmlSource, /value="daily"/);
    assert.match(htmlSource, /value="weekly"/);
    assert.match(htmlSource, /value="weekdays"/);
    assert.match(htmlSource, /value="date_range"/);
    assert.match(htmlSource, /id="scheduleStartTime"/);
    assert.match(htmlSource, /id="scheduleTimeTrigger"/);
    assert.match(htmlSource, /id="scheduleDurationHours"/);
    assert.doesNotMatch(htmlSource, /id="scheduleDurationMinutes"/);
    assert.match(htmlSource, /id="scheduleDurationDial"/);
    assert.match(htmlSource, /id="scheduleDurationHandle"[^>]*role="slider"[^>]*aria-valuemin="1"[^>]*aria-valuemax="24"/);
    assert.doesNotMatch(htmlSource, /id="scheduleDurationDial"[\s\S]*?<svg[^>]*aria-hidden="true"[\s\S]*?id="scheduleDurationHandle"/);
    assert.match(htmlSource, /id="scheduleDurationValue"/);
    assert.match(htmlSource, /id="scheduleWindowSummary"/);
    assert.match(htmlSource, /id="scheduleWeekdays"/);
    assert.match(htmlSource, /id="scheduleDateStart"/);
    assert.match(htmlSource, /id="scheduleDateEnd"/);
    assert.match(htmlSource, /id="saveSchedule"/);
    assert.doesNotMatch(htmlSource, /id="applySchedule"/);
    assert.match(htmlSource, /id="scheduleFeedback"[^>]*aria-live="polite"/);
    assert.match(appSource, /'\/api\/control\/plan'/);
    assert.match(extractFunction('renderSchedulePanel'), /scheduleStatus/);
    assert.match(extractFunction('bindScheduleControls'), /saveSchedule\(true\)/);
    assert.match(appSource, /gj-time-picker/);
    assert.match(htmlSource, /data-date-picker-mode="range"/);
    assert.match(stylesSource, /\.schedule-duration-dial\s*\{/);
    assert.match(stylesSource, /\.schedule-mode-segments\s*\{/);
    assert.match(stylesSource, /\.schedule-weekdays\[hidden\],\s*\.schedule-hint\[hidden\],\s*\.schedule-date-range\[hidden\]\s*\{\s*display:none/);
});

test('schedule form payload converts whole hours and validates required modes', () => {
    const helpers = evaluate(`
        ${extractFunction('schedulePayloadFromValues')}
        ${extractFunction('validateSchedulePayload')}
        ({ schedulePayloadFromValues, validateSchedulePayload });
    `);
    const payload = helpers.schedulePayloadFromValues({
        enabled: true,
        mode: 'weekly',
        startTime: '09:30',
        durationHours: '2',
        weekdays: [4, 0, 4],
        dateStart: '',
        dateEnd: '',
    });
    assert.equal(JSON.stringify(payload), JSON.stringify({
        enabled: true,
        mode: 'weekly',
        startTime: '09:30',
        durationMinutes: 120,
        weekdays: [0, 4],
        dateStart: '',
        dateEnd: '',
    }));
    assert.equal(helpers.validateSchedulePayload({ ...payload, durationMinutes: 0 }), '持续时长请选择 1 至 24 小时');
    assert.equal(helpers.validateSchedulePayload({ ...payload, durationMinutes: 90 }), '持续时长请选择 1 至 24 小时');
    assert.equal(helpers.validateSchedulePayload({ ...payload, durationMinutes: 1500 }), '持续时长请选择 1 至 24 小时');
    assert.equal(helpers.validateSchedulePayload({ ...payload, weekdays: [] }), '每周模式至少选择一天');
    assert.equal(helpers.validateSchedulePayload({ ...payload, mode: 'date_range', dateStart: '', dateEnd: '' }), '日期范围不能为空');
});

test('legacy schedule durations round upward to the next whole hour', () => {
    const durationHoursFromMinutes = evaluate(`${extractFunction('durationHoursFromMinutes')} durationHoursFromMinutes;`);
    assert.equal(durationHoursFromMinutes(0), 0);
    assert.equal(durationHoursFromMinutes(1), 1);
    assert.equal(durationHoursFromMinutes(60), 1);
    assert.equal(durationHoursFromMinutes(61), 2);
    assert.equal(durationHoursFromMinutes(90), 2);
    assert.equal(durationHoursFromMinutes(1440), 24);
});

test('schedule window model labels cross-midnight end times', () => {
    const scheduleWindowModel = evaluate(`${extractFunction('scheduleWindowModel')} scheduleWindowModel;`);
    assert.equal(JSON.stringify(scheduleWindowModel('09:30', 4)), JSON.stringify({
        startMinutes: 570,
        endMinutes: 810,
        crossesMidnight: false,
        startLabel: '09:30',
        endLabel: '13:30',
        summary: '09:30 → 13:30',
    }));
    assert.equal(scheduleWindowModel('23:30', 2).summary, '23:30 → 次日 01:30');
});

test('schedule dial converts pointer angles to whole-hour durations', () => {
    const durationHoursFromDialAngle = evaluate(`${extractFunction('durationHoursFromDialAngle')} durationHoursFromDialAngle;`);
    assert.equal(durationHoursFromDialAngle('09:30', 202.5), 4);
    assert.equal(durationHoursFromDialAngle('23:30', 22.5), 2);
    assert.equal(durationHoursFromDialAngle('09:30', 142.5), 24);
});

test('schedule duration keyboard controls clamp to the supported range', () => {
    const adjustScheduleDurationByKey = evaluate(`${extractFunction('adjustScheduleDurationByKey')} adjustScheduleDurationByKey;`);
    assert.equal(adjustScheduleDurationByKey(2, 'ArrowRight'), 3);
    assert.equal(adjustScheduleDurationByKey(2, 'ArrowDown'), 1);
    assert.equal(adjustScheduleDurationByKey(1, 'ArrowLeft'), 1);
    assert.equal(adjustScheduleDurationByKey(24, 'ArrowUp'), 24);
    assert.equal(adjustScheduleDurationByKey(7, 'Home'), 1);
    assert.equal(adjustScheduleDurationByKey(7, 'End'), 24);
    assert.equal(adjustScheduleDurationByKey(7, 'Escape'), null);
});

test('schedule range picker normalizes reverse date selections', () => {
    const normalizeDateRange = evaluate(`${extractFunction('normalizeDateRange')} normalizeDateRange;`);
    assert.equal(JSON.stringify(normalizeDateRange('2026-07-24', '2026-07-20')), JSON.stringify(['2026-07-20', '2026-07-24']));
    assert.equal(JSON.stringify(normalizeDateRange('2026-07-20', '')), JSON.stringify(['2026-07-20', '']));
});

test('registered instance metric counts only online browsers', () => {
    assert.match(htmlSource, /<span>已登记实例<\/span><strong id="connectedClients">0<\/strong><small>仅统计在线实例<\/small>/);
    assert.doesNotMatch(htmlSource, /包含当前离线实例/);
});

test('control instance cards render only online workers while normalized state keeps all registered workers', () => {
    const rendererSource = extractFunction('renderControlInstances');
    assert.doesNotMatch(rendererSource, /\binstances\.forEach\(/);
    assert.doesNotMatch(rendererSource, /账号标识|今日投递|操作编号/);
    assert.match(rendererSource, /const onlineInstances = instances\.filter\(\(item\) => item\.online !== false\);/);
    assert.match(rendererSource, /\$\{onlineInstances\.length\} 个在线实例/);
    assert.match(rendererSource, /if \(!onlineInstances\.length\)/);
    assert.match(rendererSource, /onlineInstances\.forEach\(\(item\) =>/);

    const helpers = evaluate(`
        const container = {
            children: [],
            innerHTML: '',
            replaceChildren() { this.children = []; this.innerHTML = ''; },
            appendChild(card) { this.children.push(card); },
        };
        const summary = { textContent: '' };
        const state = { lifecycleRequests: {}, control: {}, runtime: null };
        const document = { createElement() { return { className: '', innerHTML: '' }; } };
        const DESIRED_STATE_LABELS = { stopped: 'stopped' };
        const EXECUTION_STATE_LABELS = { stopped: 'stopped' };
        const SYNC_STATE_LABELS = { synced: 'synced' };
        function $(selector) { return selector === '#controlInstanceList' ? container : summary; }
        function desiredStateOf(item) { return item.desiredState || 'stopped'; }
        function executionStateOf(item) { return item.executionState || 'stopped'; }
        function syncStateOf(item) { return item.syncState || 'synced'; }
        function lifecycleRequestKey(scope, workerId) { return scope + ':' + workerId; }
        function escapeHtml(value) { return String(value ?? ''); }
        function controlTime() { return ''; }
        function controlAxesMarkup() { return ''; }
        function instanceQuotaMarkup() { return ''; }
        function lifecycleButtonsMarkup() { return ''; }
        function decisionMarkup() { return ''; }
        function eventMessage(event) { return event; }
        ${extractFunction('controlArray')}
        ${extractFunction('normalizedControlState')}
        ${rendererSource}
        ({ state, container, summary, normalizedControlState, renderControlInstances });
    `);
    const registeredInstances = Object.freeze([
        Object.freeze({ workerId: 'online-worker', online: true }),
        Object.freeze({ workerId: 'offline-worker', online: false }),
        Object.freeze({ workerId: 'legacy-worker' }),
    ]);

    helpers.renderControlInstances(registeredInstances, []);
    assert.equal(helpers.summary.textContent, '2 个在线实例');
    assert.equal(helpers.container.children.length, 2);
    const renderedMarkup = helpers.container.children.map((card) => card.innerHTML).join('\n');
    assert.match(renderedMarkup, /online-worker/);
    assert.match(renderedMarkup, /legacy-worker/);
    assert.doesNotMatch(renderedMarkup, /offline-worker/);
    assert.deepEqual(registeredInstances.map((item) => item.workerId), ['online-worker', 'offline-worker', 'legacy-worker']);

    helpers.state.control = { instances: registeredInstances };
    const normalized = helpers.normalizedControlState();
    assert.equal(normalized.instances, registeredInstances);
    assert.equal(normalized.instances.length, 3);

    helpers.renderControlInstances([registeredInstances[1]], []);
    assert.equal(helpers.summary.textContent, '暂无在线浏览器实例');
    assert.equal(helpers.container.children.length, 0);
    assert.match(helpers.container.innerHTML, /暂无在线浏览器实例/);
});

test('fast lifecycle reconciliation distinguishes browser delivery from offline presets', () => {
    const helpers = evaluate(`
        const state = { control: { clients: [] }, runtime: null };
        const SYNC_STATE_LABELS = { pending: 'pending', applying: 'applying', synced: 'synced', failed: 'failed' };
        function controlArray(value) { return Array.isArray(value) ? value : []; }
        function desiredStateOf(item) { return item.desiredState || 'stopped'; }
        function executionStateOf(item) { return item.executionState || 'stopped'; }
        ${extractFunction('syncStateOf')}
        ${extractFunction('normalizedControlState')}
        ${extractFunction('lifecycleDeliveryObservation')}
        ({ state, lifecycleDeliveryObservation });
    `);
    const operation = { operationId: 'operation-7', revision: 7, targetCount: 1 };

    helpers.state.control.clients = [{
        workerId: 'worker-a', operationId: 'operation-7', revision: 7,
        online: true, desiredState: 'paused', executionState: 'running', syncState: 'pending',
    }];
    assert.equal(helpers.lifecycleDeliveryObservation('worker', 'worker-a', operation).status, 'waiting');

    helpers.state.control.clients[0].syncState = 'applying';
    assert.equal(helpers.lifecycleDeliveryObservation('worker', 'worker-a', operation).status, 'delivered');

    helpers.state.control.clients[0].online = false;
    helpers.state.control.clients[0].syncState = 'pending';
    const offline = helpers.lifecycleDeliveryObservation('worker', 'worker-a', operation);
    assert.equal(offline.status, 'delivered');
    assert.equal(offline.offline, true);

    helpers.state.control.clients[0].online = true;
    helpers.state.control.clients[0].syncState = 'failed';
    assert.equal(helpers.lifecycleDeliveryObservation('worker', 'worker-a', operation).status, 'failed');
});

test('account quota is embedded in each instance card and capped at 150', () => {
    assert.doesNotMatch(htmlSource, /id="accountQuotaBody"|<span>账号配额<\/span><small>按账号查看/);
    assert.doesNotMatch(appSource, /function renderAccountQuotas/);
    assert.match(extractFunction('renderControlInstances'), /instanceQuotaMarkup\(item, accounts\)/);
    assert.match(stylesSource, /\.control-instance-quota\s*\{[^}]*display:grid/);
    assert.match(stylesSource, /\.control-instance-quota-editor\[hidden\]\s*\{[^}]*display:\s*none/);
    assert.match(stylesSource, /@media \(max-width: 430px\)[\s\S]*?\.control-instance-quota\s*\{[^}]*grid-template-columns:1fr/);

    const helpers = evaluate(`
        const state = { accountLimitDrafts: new Map() };
        const ACCOUNT_DAILY_LIMIT_MIN = 0;
        const ACCOUNT_DAILY_LIMIT_MAX = 150;
        function escapeHtml(value) { return String(value ?? ''); }
        ${extractFunction('controlArray')}
        ${extractFunction('accountQuotaForInstance')}
        ${extractFunction('instanceQuotaMarkup')}
        ({ accountQuotaForInstance, instanceQuotaMarkup });
    `);
    const sharedQuota = { accountId: 'account-a', count: 35, limit: 80 };
    const accounts = [sharedQuota];
    const workerAQuota = helpers.accountQuotaForInstance({ workerId: 'worker-a', accountId: 'account-a' }, accounts);
    const workerBQuota = helpers.accountQuotaForInstance({ workerId: 'worker-b', accountId: 'account-a' }, accounts);
    assert.equal(workerAQuota.limit, 80);
    assert.equal(workerBQuota.count, 35);
    assert.equal(workerAQuota, workerBQuota);
    const markup = helpers.instanceQuotaMarkup({ accountId: 'account-a' }, accounts);
    assert.match(markup, /min="0" max="150" step="1"/);
    assert.match(markup, /35<small> \/ 80<\/small>/);
    assert.match(markup, /data-control-action="save_account_limit"/);
    const bindSource = extractFunction('bindEvents');
    assert.match(bindSource, /!accountId \|\| !rawLimit \|\| !Number\.isInteger\(dailyLimit\)/);
    assert.match(bindSource, /await updateControlResource\(`\/api\/control\/accounts\/\$\{encodeURIComponent\(accountId\)\}`/);
    assert.match(bindSource, /peer\.dataset\.accountLimit === accountId\) peer\.value = input\.value/);
    assert.match(extractFunction('updateControlResource'), /await loadControlState\(\)/);
});

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

test('account quota save settles only the current request and keeps newer drafts', () => {
    const helpers = evaluate(`
        const state = {
            accountLimitDrafts: new Map([['account-a', '74']]),
            accountLimitPending: new Map([['account-a', '74']]),
        };
        ${extractFunction('settleAccountLimitSave')}
        ({ state, settleAccountLimitSave });
    `);
    assert.equal(helpers.settleAccountLimitSave('account-a', '73', true), false);
    assert.equal(helpers.state.accountLimitPending.get('account-a'), '74');
    assert.equal(helpers.state.accountLimitDrafts.get('account-a'), '74');
    assert.equal(helpers.settleAccountLimitSave('account-a', '74', true), true);
    assert.equal(helpers.state.accountLimitDrafts.has('account-a'), false);
    helpers.state.accountLimitDrafts.set('account-a', '75');
    helpers.state.accountLimitPending.set('account-a', '75');
    assert.equal(helpers.settleAccountLimitSave('account-a', '75', false), true);
    assert.equal(helpers.state.accountLimitDrafts.get('account-a'), '75');
});

test('account quota editor disables controls while its save is pending', () => {
    const helpers = evaluate(`
        const state = {
            accountLimitDrafts: new Map([['account-a', '73']]),
            accountLimitPending: new Map([['account-a', '73']]),
        };
        const ACCOUNT_DAILY_LIMIT_MIN = 0;
        const ACCOUNT_DAILY_LIMIT_MAX = 150;
        function escapeHtml(value) { return String(value ?? ''); }
        ${extractFunction('controlArray')}
        ${extractFunction('accountQuotaForInstance')}
        ${extractFunction('instanceQuotaMarkup')}
        ({ instanceQuotaMarkup });
    `);
    const markup = helpers.instanceQuotaMarkup(
        { accountId: 'account-a' },
        [{ accountId: 'account-a', count: 10, limit: 60 }],
    );
    assert.match(markup, /data-account-limit="account-a"[^>]* disabled/);
    assert.match(markup, /data-control-action="save_account_limit"[^>]* disabled/);
    assert.match(markup, /data-control-action="cancel_account_limit"[^>]* disabled/);
});

test('global and worker lifecycle requests are mutually exclusive while pending', () => {
    const helpers = evaluate(`
        const state = { lifecycleRequests: {} };
        ${extractFunction('lifecycleRequestKey')}
        ${extractFunction('lifecycleControlsLocked')}
        ({ state, lifecycleControlsLocked });
    `);

    helpers.state.lifecycleRequests.global = { pending: true };
    assert.equal(helpers.lifecycleControlsLocked('global'), true);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-a'), true);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-b'), true);

    helpers.state.lifecycleRequests.global = { pending: false, failed: false };
    assert.equal(helpers.lifecycleControlsLocked('global'), false);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-a'), false);

    helpers.state.lifecycleRequests.global = { pending: false, failed: true };
    assert.equal(helpers.lifecycleControlsLocked('global'), false);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-a'), false);

    helpers.state.lifecycleRequests['worker:worker-a'] = { pending: true };
    assert.equal(helpers.lifecycleControlsLocked('global'), true);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-a'), true);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-b'), false);

    helpers.state.lifecycleRequests['worker:worker-a'] = { pending: false, failed: false };
    assert.equal(helpers.lifecycleControlsLocked('global'), false);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-a'), false);

    helpers.state.lifecycleRequests['worker:worker-a'] = { pending: false, failed: true };
    assert.equal(helpers.lifecycleControlsLocked('global'), false);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-a'), false);

    assert.match(extractFunction('lifecycleButtonsMarkup'), /lifecycleControlsLocked\(scope, workerId\)/);
    assert.match(extractFunction('renderGlobalLifecycle'), /const controlsLocked = lifecycleControlsLocked\('global'\)/);
    const submitSource = extractFunction('setDesiredLifecycleState');
    assert.match(submitSource, /if \(lifecycleControlsLocked\(scope, workerId\)\)/);
    assert.equal((submitSource.match(/pending: false[\s\S]*?renderControlCenter\(\)/g) || []).length, 2);
    assert.match(
        extractFunction('bindEvents'),
        /if \(lifecycleControlsLocked\(scope, workerId\)\)[\s\S]*?if \(scope === 'global' && desiredState === 'stopped' && !\(await confirmStopAll\(\)\)\) return;[\s\S]*?setDesiredLifecycleState\(scope, workerId, desiredState\)/,
    );
});

test('lifecycle submission restores the opposite scope after success and failure', async () => {
    const helpers = evaluate(`
        const DESIRED_STATE_LABELS = { running: 'running', paused: 'paused', stopped: 'stopped' };
        const state = { lifecycleRequests: {} };
        const calls = [];
        const renders = [];
        let resolveRequest = null;
        let rejectRequest = null;
        function apiJson(url, options) {
            calls.push({ url, options });
            return new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
        }
        function resolveNext(value) { resolveRequest(value); }
        function rejectNext(message) { rejectRequest(new Error(message)); }
        function renderControlCenter() { renders.push(JSON.parse(JSON.stringify(state.lifecycleRequests))); }
        function showToast() {}
        async function loadControlState() {}
        async function reconcileLifecycleDelivery() { return { status: 'delivered', offline: false }; }
        ${extractFunction('lifecycleRequestKey')}
        ${extractFunction('lifecycleControlsLocked')}
        async ${extractFunction('setDesiredLifecycleState')}
        ({ state, calls, renders, resolveNext, rejectNext, lifecycleControlsLocked, setDesiredLifecycleState });
    `);

    const workerRequest = helpers.setDesiredLifecycleState('worker', 'worker-a', 'running');
    assert.equal(helpers.calls.length, 1);
    assert.equal(await helpers.setDesiredLifecycleState('global', '', 'paused'), null);
    assert.equal(helpers.calls.length, 1);
    helpers.resolveNext({ operationId: 'worker-op', revision: 1 });
    await workerRequest;
    assert.equal(helpers.lifecycleControlsLocked('global'), false);
    assert.equal(helpers.renders.at(-1)['worker:worker-a'].pending, false);

    const globalRequest = helpers.setDesiredLifecycleState('global', '', 'stopped');
    assert.equal(helpers.calls.length, 2);
    assert.equal(await helpers.setDesiredLifecycleState('worker', 'worker-b', 'paused'), null);
    assert.equal(helpers.calls.length, 2);
    helpers.rejectNext('global request failed');
    assert.equal(await globalRequest, null);
    assert.equal(helpers.lifecycleControlsLocked('worker', 'worker-b'), false);
    assert.equal(helpers.renders.at(-1).global.failed, true);

    const retryRequest = helpers.setDesiredLifecycleState('worker', 'worker-b', 'paused');
    assert.equal(helpers.calls.length, 3);
    helpers.resolveNext({ operationId: 'worker-retry', revision: 2 });
    await retryRequest;
});

test('lifecycle labels cover desired, transitional execution, and synchronization states', () => {
    const labels = evaluate(`
        ${extractConst('DESIRED_STATE_LABELS')}
        ${extractConst('EXECUTION_STATE_LABELS')}
        ${extractConst('SYNC_STATE_LABELS')}
        ({ DESIRED_STATE_LABELS, EXECUTION_STATE_LABELS, SYNC_STATE_LABELS });
    `);
    assert.deepEqual(Array.from(Object.keys(labels.DESIRED_STATE_LABELS)), ['running', 'paused', 'stopped']);
    assert.deepEqual(Array.from(Object.keys(labels.EXECUTION_STATE_LABELS)), ['starting', 'running', 'pausing', 'paused', 'stopping', 'stopped', 'error']);
    assert.deepEqual(Array.from(Object.keys(labels.SYNC_STATE_LABELS)), ['pending', 'applying', 'synced', 'failed']);
    assert.match(htmlSource, /id="controlStopConfirm"[^>]*hidden/);
    assert.match(appSource, /scope === 'global' && desiredState === 'stopped' && !\(await confirmStopAll\(\)\)/);
});

test('all API requests share session-scoped bearer authorization while map assets stay public', () => {
    assert.doesNotMatch(appSource, /\bfetch\(\s*['"`]\/api\//);
    assert.match(appSource, /authorizedFetch\('\/api\/runtime'/);
    assert.match(appSource, /authorizedFetch\('\/api\/dashboard'/);
    assert.match(extractFunction('apiJson'), /authorizedFetch\(url/);
    assert.match(extractFunction('readAuthToken'), /sessionStorage\.getItem\(AUTH_TOKEN_KEY\)/);
    assert.match(extractFunction('writeAuthToken'), /sessionStorage\.setItem\(AUTH_TOKEN_KEY, token\)/);
    assert.doesNotMatch(appSource, /localStorage\.(?:getItem|setItem)\(AUTH_TOKEN_KEY/);
    assert.match(extractFunction('requestHeaders'), /`Bearer \$\{token\}`/);
    assert.match(extractFunction('requestAuthToken'), /if \(pendingAuthPrompt\) return pendingAuthPrompt/);
    assert.match(htmlSource, /id="authTokenInput"[^>]*type="password"[^>]*minlength="32"[^>]*maxlength="256"/);
    assert.match(appSource, /fetch\('\/dashboard\/china\.json'/);
    assert.match(appSource, /fetch\('\/dashboard\/china-cities\.json'/);
});

test('live logs are collapsed by default and remain explicitly expandable', () => {
    assert.match(appSource, /logsExpanded:\s*false/);
    assert.match(htmlSource, /id="liveLogCard"[^>]*>[\s\S]*?id="liveLogToggle"[^>]*aria-expanded="false"[\s\S]*?id="liveLogPanel"[^>]*hidden/);
    assert.match(extractFunction('setLiveLogsExpanded'), /panel\.hidden = !state\.logsExpanded/);
    assert.match(stylesSource, /\.live-log-card\.is-collapsed \.control-card-head/);
});

test('live log controls expose labeled filters, accessible icon buttons, and a responsive log region', () => {
    assert.match(htmlSource, /<span>账号<\/span><select id="liveLogAccountFilter">/);
    assert.match(htmlSource, /<span>发送方<\/span><select id="liveLogSenderFilter">[\s\S]*?<option value="system">系统<\/option>[\s\S]*?<option value="delivery">投递<\/option>[\s\S]*?<option value="claim">领取投递<\/option>[\s\S]*?<option value="queue">投递等待<\/option>/);
    assert.equal((htmlSource.match(/data-log-verbosity="(?:detailed|normal|concise)"/g) || []).length, 3);
    assert.match(htmlSource, /id="pauseLogs"[^>]*aria-controls="liveLogs"[^>]*aria-label="暂停自动滚动"[^>]*aria-pressed="false"[^>]*title="暂停自动滚动"/);
    assert.match(htmlSource, /id="clearLogs"[^>]*aria-controls="liveLogs"[^>]*aria-label="清空当前日志视图"[^>]*title="清空当前日志视图"/);
    assert.match(htmlSource, /id="liveLogCount"[^>]*aria-live="polite"/);
    assert.match(htmlSource, /id="liveLogs"[^>]*role="log"[^>]*aria-label="实时运行日志"/);
    assert.match(stylesSource, /\.live-log-tools\s*\{[^}]*display:grid[^}]*grid-template-columns:/);
    assert.match(stylesSource, /@media \(max-width: 430px\)[\s\S]*?\.live-log-tools\s*\{[^}]*grid-template-columns:minmax\(0,1fr\)/);
});

test('live log filters support sender inference and verbosity thresholds for legacy events', () => {
    const helpers = evaluate(`
        ${extractConst('LOG_SENDER_LABELS')}
        ${extractConst('LOG_VERBOSITY_RANK')}
        ${extractFunction('eventPayload')}
        ${extractFunction('eventLogAccount')}
        ${extractFunction('eventLogSender')}
        ${extractFunction('eventLogVerbosity')}
        ${extractFunction('eventMatchesLogFilters')}
        ({ LOG_SENDER_LABELS, eventLogSender, eventLogVerbosity, eventMatchesLogFilters });
    `);
    assert.deepEqual(Array.from(Object.entries(helpers.LOG_SENDER_LABELS)), [
        ['system', '系统'], ['delivery', '投递'], ['claim', '领取投递'], ['queue', '投递等待'],
    ]);

    const legacyClaim = { type: 'job_action', payload: { accountId: 'alpha', action: 'delivery_claim_failed' } };
    const legacyQueue = { type: 'client_state_changed', payload: { accountId: 'alpha', state: 'queued' } };
    const detailedDelivery = { type: 'script_log', payload: { accountId: 'beta', sender: 'delivery', verbosity: 'detailed', message: 'detail' } };
    const conciseSystem = { type: 'script_log', payload: { accountId: 'alpha', sender: 'system', verbosity: 'concise', message: 'state' } };

    assert.equal(helpers.eventLogSender(legacyClaim), 'claim');
    assert.equal(helpers.eventLogSender(legacyQueue), 'queue');
    assert.equal(helpers.eventLogVerbosity(legacyClaim), 'concise');
    assert.equal(helpers.eventMatchesLogFilters(legacyClaim, { account: 'alpha', sender: 'claim', verbosity: 'normal' }), true);
    assert.equal(helpers.eventMatchesLogFilters(legacyClaim, { account: 'beta', sender: 'claim', verbosity: 'detailed' }), false);
    assert.equal(helpers.eventMatchesLogFilters(detailedDelivery, { account: 'all', sender: 'delivery', verbosity: 'normal' }), false);
    assert.equal(helpers.eventMatchesLogFilters(detailedDelivery, { account: 'all', sender: 'delivery', verbosity: 'detailed' }), true);
    assert.equal(helpers.eventMatchesLogFilters(conciseSystem, { account: 'all', sender: 'system', verbosity: 'concise' }), true);
    assert.equal(helpers.eventMatchesLogFilters(conciseSystem, { account: 'all', sender: 'system', verbosity: 'normal' }), true);
    assert.equal(helpers.eventMatchesLogFilters(conciseSystem, { account: 'all', sender: 'system', verbosity: 'detailed' }), true);
});

test('salary analysis uses the requested five bucket boundaries', () => {
    const salaryBucket = evaluate(`${extractFunction('salaryBucket')} salaryBucket;`);
    const matchesSalaryBucket = evaluate(`${extractFunction('matchesSalaryBucket')} matchesSalaryBucket;`);
    assert.deepEqual([0, 4.9, 5, 7.9, 8, 11.9, 12, 19.9, 20, 80].map(salaryBucket), [
        '0–5K', '0–5K', '5–8K', '5–8K', '8–12K', '8–12K', '12–20K', '12–20K', '20K 以上', '20K 以上',
    ]);
    const renderSource = extractFunction('renderSalary');
    assert.match(renderSource, /\['0–5K', '5–8K', '8–12K', '12–20K', '20K 以上'\]/);
    assert.match(renderSource, /\[\[0, 5\], \[5, 8\], \[8, 12\], \[12, 20\], \[20, null\]\]/);
    assert.equal(matchesSalaryBucket({ salaryK: 4.9 }, '0:5'), true);
    assert.equal(matchesSalaryBucket({ salaryK: 5 }, '0:5'), false);
    assert.equal(matchesSalaryBucket({ salaryK: 5 }, '5:8'), true);
    assert.equal(matchesSalaryBucket({ salaryK: 20 }, '12:20'), false);
    assert.equal(matchesSalaryBucket({ salaryK: 20 }, '20:'), true);
    assert.match(extractFunction('bindChartInteractions'), /salaryBucket: state\.salaryBucket === range \? '' : range/);
});

test('scoring master switch is unique, lives with the rule editor, and exposes disabled decisions', () => {
    const fillSource = extractFunction('fillConfigForm');
    const toggleSource = extractFunction('renderScoringMasterToggle');
    const stateSource = extractFunction('updateScoringEditorState');
    const decisionSource = extractFunction('decisionMarkup');

    assert.match(fillSource, /'resume_name', 'scoring_enabled'/);
    assert.match(fillSource, /renderScoringMasterToggle\(scoring, Boolean\(config\.scoring_enabled\)\)/);
    assert.match(fillSource, /scoring\.className = 'scoring-rule-fieldset'/);
    assert.equal((appSource.match(/dataset\.configPath = 'scoring_enabled'/g) || []).length, 1);
    assert.match(toggleSource, /dataset\.configPath = 'scoring_enabled'/);
    assert.match(toggleSource, /dataset\.scoringToggleState/);
    assert.match(stateSource, /control\.disabled = !enabled/);
    assert.match(stateSource, /scoring-rules-disabled/);
    assert.match(decisionSource, /decision\.scoringEnabled !== false/);
    assert.match(decisionSource, /扣分规则已关闭/);
    assert.match(stylesSource, /\.scoring-master-bar/);
    assert.match(stylesSource, /\.scoring-rules-disabled \.scoring-rule-editor/);
});

test('legacy frontend server address stays hidden and untouched by the config editor', () => {
    const fillSource = extractFunction('fillConfigForm');
    const saveSource = extractFunction('saveAdminConfig');
    assert.match(appSource, /const HIDDEN_CONFIG_PATHS = new Set\(\['frontend\.serverHost'\]\)/);
    assert.match(fillSource, /filter\(\(\[key\]\) => !HIDDEN_CONFIG_PATHS\.has\(`\$\{groupKey\}\.\$\{key\}`\)\)/);
    assert.doesNotMatch(appSource, /serverHost:\s*'本地服务地址'/);
    assert.match(saveSource, /structuredClone\(state\.adminConfig\)/);
});

test('removed manual filter startup wait is absent from dashboard configuration', () => {
    assert.doesNotMatch(appSource, /manualFilterWaitMs|手动筛选等待/);
});

test('detail random delay settings have labels and documented global switch semantics', () => {
    assert.match(appSource, /detailRandomDelayMinMs: '职位详情随机延时下限（ms）'/);
    assert.match(appSource, /detailRandomDelayMaxMs: '职位详情随机延时上限（ms）'/);
    assert.match(readmeSource, /`frontend\.detailRandomDelayMinMs`/);
    assert.match(readmeSource, /`frontend\.detailRandomDelayMaxMs`/);
    assert.match(readmeSource, /详情.*随机.*0 ms.*0–600000/);
    assert.match(readmeSource, /antiDetectionEnabled.*总开关|总开关.*antiDetectionEnabled/);
});

test('delivery gate policy card exposes V2 safety plan and stage controls', () => {
    assert.match(htmlSource, /id="deliveryGateCard"/);
    assert.match(htmlSource, /id="deliveryProtocolBadge"[^>]*>[^<]*V2/);
    assert.match(htmlSource, /id="deliveryStageList"/);
    [
        'policyScanOnly', 'policyScanAiEnabled', 'policySendingDisabled',
        'policyOpeningDisabled', 'policyResumeSendingDisabled',
        'planDailyTarget', 'planHourlyLimit', 'planMaxConsecutiveFailures',
        'planActiveStart', 'planActiveEnd', 'planBreakStart', 'planBreakEnd',
        'planMinDelayMs', 'planMaxDelayMs', 'saveDeliveryPolicy',
    ].forEach((id) => assert.match(htmlSource, new RegExp(`id="${id}"`), `missing ${id}`));
    assert.match(appSource, /function renderDeliveryPolicy\(/);
    assert.match(appSource, /function deliverySafetyPayload\(/);
    assert.match(appSource, /function deliveryPlanPayload\(/);
    assert.match(appSource, /\/api\/control\/safety/);
    assert.match(appSource, /\/api\/control\/plan/);
    assert.match(appSource, /#saveDeliveryPolicy/);
    assert.match(appSource, /delivery-policy-dirty/);
    assert.match(stylesSource, /\.delivery-policy-grid\s*\{[^}]*grid-template-columns:/);
    assert.match(stylesSource, /@media \(max-width: 680px\)[\s\S]*?\.delivery-policy-grid\s*\{[^}]*grid-template-columns:\s*1fr/);
});

test('delivery gate payloads preserve server policy and parse editable fields', () => {
    const payloads = evaluate(`
        const SCHEDULE_DEFAULT = { enabled: false };
        const safetyInputs = [
            { dataset: { deliverySafety: 'scanOnly' }, checked: true },
            { dataset: { deliverySafety: 'scanAiEnabled' }, checked: false },
            { dataset: { deliverySafety: 'sendingDisabled' }, checked: true },
        ];
        const planInputs = [
            { dataset: { deliveryPlan: 'dailyTarget' }, value: '18' },
            { dataset: { deliveryPlan: 'hourlyLimit' }, value: '4' },
            { dataset: { deliveryPlan: 'maxConsecutiveFailures' }, value: '3' },
            { dataset: { deliveryPlan: 'activeStart' }, value: '09:00' },
            { dataset: { deliveryPlan: 'activeEnd' }, value: '18:30' },
            { dataset: { deliveryPlan: 'minDelayMs' }, value: '800' },
            { dataset: { deliveryPlan: 'maxDelayMs' }, value: '1600' },
        ];
        function $$ (selector) { return selector === '[data-delivery-safety]' ? safetyInputs : planInputs; }
        function normalizedControlState() {
            return {
                safety: { stopOnDailyLimit: true, scanOnly: false },
                plan: { stopAtTarget: true, schedule: { enabled: true } },
                schedule: { enabled: true },
            };
        }
        ${extractFunction('deliverySafetyPayload')}
        ${extractFunction('deliveryPlanPayload')}
        ({ safety: deliverySafetyPayload(), plan: deliveryPlanPayload() });
    `);
    assert.equal(payloads.safety.scanOnly, true);
    assert.equal(payloads.safety.scanAiEnabled, false);
    assert.equal(payloads.safety.sendingDisabled, true);
    assert.equal(payloads.safety.stopOnDailyLimit, true);
    assert.equal(payloads.plan.dailyTarget, 18);
    assert.equal(payloads.plan.hourlyLimit, 4);
    assert.equal(payloads.plan.minDelayMs, 800);
    assert.equal(payloads.plan.maxDelayMs, 1600);
    assert.equal(payloads.plan.stopAtTarget, true);
    assert.equal(payloads.plan.schedule.enabled, true);
});

test('delivery stage mapping and policy save bindings are executable V2 contracts', () => {
    const currentDeliveryStage = evaluate(`
        const DELIVERY_STAGES = ['detail','duplicate','rules','hr','random','qualified','claim','ai','queued','sent'];
        const DELIVERY_STAGE_BY_STATE = {
            evaluating:'rules', duplicate:'duplicate', hr_filtered:'hr', random_skipped:'random',
            qualified:'qualified', claiming:'claim', ai_passed:'ai', queued:'queued', sent:'sent',
        };
        ${extractFunction('currentDeliveryStage')}
        currentDeliveryStage;
    `);
    assert.equal(currentDeliveryStage([
        { online: true, currentDecision: { decisionState: 'qualified' } },
        { online: true, currentDecision: { decisionState: 'queued' } },
    ]), 'queued');
    assert.equal(currentDeliveryStage([
        { online: false, currentDecision: { decisionState: 'sent' } },
        { online: true, currentDecision: { decisionState: 'claiming' } },
    ]), 'claim');
    assert.match(appSource, /invalid_fields:\s*'字段不完整'/);
    assert.match(appSource, /quota_rejected:\s*'额度门禁未通过'/);
    assert.match(appSource, /policy_blocked:\s*'运行策略已阻止'/);
    assert.match(appSource, /claim_rejected:\s*'投递权领取失败'/);
    assert.match(appSource, /invalid_fields:\s*'detail'/);
    assert.match(appSource, /quota_rejected:\s*'qualified'/);
    assert.match(appSource, /policy_blocked:\s*'qualified'/);
    assert.match(appSource, /claim_rejected:\s*'claim'/);

    const bindSource = extractFunction('bindEvents');
    const saveSource = extractFunction('saveDeliveryPolicy');
    const renderSource = extractFunction('renderDeliveryPolicy');
    assert.match(bindSource, /#deliveryGateCard'\)\.addEventListener\('input', markDeliveryPolicyDirty\)/);
    assert.match(bindSource, /#deliveryGateCard'\)\.addEventListener\('change', markDeliveryPolicyDirty\)/);
    assert.match(bindSource, /#saveDeliveryPolicy'\)\.addEventListener\('click', saveDeliveryPolicy\)/);
    assert.match(saveSource, /apiJson\('\/api\/control\/safety',\s*\{\s*method:\s*'PUT'/);
    assert.match(saveSource, /apiJson\('\/api\/control\/plan',\s*\{\s*method:\s*'PUT'/);
    assert.match(renderSource, /item\.protocolVersion/);
    assert.match(renderSource, /item\.scriptApiVersion/);
    assert.match(renderSource, /currentDeliveryStage\(data\.instances\)/);
});

test('dashboard and documentation call behavioral timing rhythm randomization', () => {
    assert.doesNotMatch(appSource, /randomNoIntroduceRatio|随机不带招呼语|防检测随机化/);
    assert.doesNotMatch(htmlSource, /随机空招呼|随机省略招呼|防检测随机化/);
    assert.doesNotMatch(readmeSource, /randomNoIntroduceRatio|随机不带招呼语|随机省略招呼语|防检测随机化/);
    assert.match(appSource + htmlSource + readmeSource, /节奏随机化/);
    assert.match(readmeSource, /qualify[\s\S]*claim[\s\S]*job-filter|资格[\s\S]*领取投递权[\s\S]*AI/);
});

test('mobile scoring deduction result has enough stable width for its full label', () => {
    assert.match(stylesSource, /\.score-keyword-card\s*\{[^}]*grid-template-columns:\s*minmax\(120px,\s*1fr\)\s+32px/);
    assert.match(stylesSource, /\.deduction-star-result\s*\{[^}]*white-space:\s*nowrap/);
});

test('removed job cache dashboard surface stays absent', () => {
    assert.doesNotMatch(htmlSource, /岗位缓存|cacheSection|cacheDetailDrawer/);
    assert.doesNotMatch(appSource, /\/api\/cache|cacheMode|loadCache|bindCacheInteractions/);
    assert.doesNotMatch(stylesSource, /\.cache-panel|\.cache-summary|\.cache-detail-drawer/);
});
