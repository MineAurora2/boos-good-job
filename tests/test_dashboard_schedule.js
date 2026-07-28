'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const appSource = fs.readFileSync(path.join(ROOT, 'dashboard', 'app.js'), 'utf8');
const htmlSource = fs.readFileSync(path.join(ROOT, 'dashboard', 'index.html'), 'utf8');
const stylesSource = fs.readFileSync(path.join(ROOT, 'dashboard', 'styles.css'), 'utf8');

function extractFunction(name) {
    const start = appSource.indexOf(`function ${name}(`);
    assert.notEqual(start, -1, `missing function ${name}`);
    const bodyStart = appSource.indexOf('{', start);
    let depth = 0;
    let quote = '';
    let escaped = false;
    for (let index = bodyStart; index < appSource.length; index += 1) {
        const char = appSource[index];
        if (escaped) { escaped = false; continue; }
        if (quote) {
            if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') { quote = char; continue; }
        if (char === '{') depth += 1;
        if (char === '}' && --depth === 0) return appSource.slice(start, index + 1);
    }
    assert.fail(`unterminated function ${name}`);
}

function evaluate(source) {
    return vm.runInNewContext(source, Object.create(null));
}

// scheduleTimeToMinutes references a module-level constant; extract it so the
// sandboxed helper copies resolve it the same way the browser bundle does.
function extractConstant(name) {
    const match = new RegExp(`const ${name}\\s*=\\s*[^;]+;`).exec(appSource);
    assert.ok(match, `missing constant ${name}`);
    return match[0];
}

const SCHEDULE_TIME_PATTERN_SOURCE = extractConstant('SCHEDULE_TIME_PATTERN');

test('hidden compatibility metrics stay hidden in the live monitor', () => {
    assert.match(htmlSource, /id="activeClients" hidden/);
    assert.match(htmlSource, /id="heartbeatWindowHint" hidden/);
    assert.match(stylesSource, /\.monitor-metrics \[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important/);
});

test('schedule editor exposes the intervals editor and reuses the styled time picker', () => {
    assert.match(htmlSource, /id="scheduleIntervalList"/);
    assert.match(htmlSource, /id="scheduleIntervalAdd"/);
    assert.match(htmlSource, /id="scheduleIntervalsEmpty"/);
    assert.doesNotMatch(htmlSource, /id="scheduleDurationDial"/);
    assert.doesNotMatch(htmlSource, /id="scheduleDurationHandle"/);
    assert.doesNotMatch(htmlSource, /id="scheduleStartTime"/);
    assert.match(htmlSource, /data-date-picker-mode="range"/);
    assert.match(appSource, /gj-time-picker/);
    assert.match(stylesSource, /\.schedule-weekdays\[hidden\],\s*\.schedule-hint\[hidden\],\s*\.schedule-date-range\[hidden\]\s*\{\s*display:none/);
    assert.match(stylesSource, /\.schedule-actions \.control-command\.start\s*\{[^}]*color:var\(--green\)/);
});

test('schedule settings are collapsed until the enable toggle is selected', () => {
    const cardStart = htmlSource.indexOf('id="deliveryScheduleCard"');
    assert.notEqual(cardStart, -1, 'missing schedule card');
    const card = htmlSource.slice(cardStart, htmlSource.indexOf('</article>', cardStart));

    assert.match(card, /<input\b(?=[^>]*id="scheduleEnabled")(?=[^>]*aria-controls="scheduleConfigCollapse")[^>]*>/);
    assert.match(card, /<input\b(?=[^>]*id="scheduleEnabled")(?=[^>]*aria-expanded="false")[^>]*>/);
    assert.match(card, /<[^>]*\bid="scheduleConfigCollapse"(?=[^>]*class="schedule-config-collapse")(?=[^>]*aria-hidden="true")[^>]*>/);
    assert.match(card, /class="schedule-config-inner"/);
    assert.match(card, /class="schedule-actions"[^>]*hidden[^>]*aria-hidden="true"/);
    assert.ok(card.indexOf('id="scheduleFeedback"') < card.indexOf('class="schedule-actions"'));

    assert.match(stylesSource, /\.schedule-config-collapse[^,{]*\{[^}]*grid-template-rows\s*:\s*0fr[^}]*transition/);
    assert.match(stylesSource, /\.schedule-config-collapse[^}]*\.schedule-config-inner[^}]*min-height\s*:\s*0/);
    assert.match(stylesSource, /\.schedule-config-collapse[^}]*grid-template-rows\s*:\s*1fr/);
    assert.match(stylesSource, /\.schedule-actions\[hidden\]\s*\{[^}]*display\s*:\s*none/);

    assert.match(appSource, /scheduleConfigCollapse/);
    assert.match(appSource, /aria-hidden/);
    assert.match(appSource, /inert/);
});

test('schedule card can be collapsed for storage and shows a progress ring', () => {
    assert.match(htmlSource, /id="scheduleCollapseToggle"[^>]*aria-controls="scheduleCardBody"/);
    assert.match(htmlSource, /id="scheduleCardBody"/);
    assert.match(htmlSource, /id="scheduleProgressRing"[^>]*hidden/);
    assert.match(htmlSource, /id="scheduleProgressArc"/);
    assert.match(htmlSource, /id="scheduleProgressLabel"/);
    assert.match(appSource, /SCHEDULE_CARD_COLLAPSE_KEY/);
    assert.match(appSource, /function setScheduleCardCollapsed/);
    assert.match(appSource, /function renderScheduleProgressRing/);
    assert.match(stylesSource, /\.schedule-card-body\[hidden\]\s*\{\s*display:none/);
    assert.match(stylesSource, /\.schedule-progress-arc\s*\{[^}]*stroke:var\(--green\)/);
});

test('schedule editor exposes one save-and-apply command', () => {
    assert.equal((htmlSource.match(/id="saveSchedule"/g) || []).length, 1);
    assert.match(htmlSource, /<button\b(?=[^>]*id="saveSchedule")(?=[^>]*class="[^"]*\bcontrol-command\b[^"]*\bstart\b[^"]*")[^>]*>\s*保存并立即应用\s*<\/button>/);
    assert.doesNotMatch(htmlSource, /id="applySchedule"/);
    assert.doesNotMatch(appSource, /applySchedule/);

    const bindSource = extractFunction('bindScheduleControls');
    const saveSource = extractFunction('saveSchedule');
    assert.match(bindSource, /saveSchedule\(true\)/);
    assert.match(saveSource, /!schedule\.enabled[\s\S]*scheduleEnabled[\s\S]*focus\(\)/);
    assert.doesNotMatch(bindSource, /saveSchedule\(false\)/);
    assert.doesNotMatch(bindSource, /applySchedule/);
});

test('range trigger keeps equal date columns and positions its calendar icon independently', () => {
    assert.match(stylesSource, /\.schedule-range-trigger[^,{]*\{[^}]*position\s*:\s*relative/);
    assert.match(stylesSource, /\.schedule-range-trigger[^,{]*\{[^}]*grid-template-columns\s*:\s*(?:minmax\(0,\s*1fr\)|1fr)\s+24px\s+(?:minmax\(0,\s*1fr\)|1fr)/);
    assert.doesNotMatch(stylesSource, /\.schedule-range-trigger[^,{]*\{[^}]*grid-template-columns[^}]*27px/);
    assert.match(stylesSource, /\.schedule-range-trigger\s+\.schedule-calendar-icon\s*\{[^}]*position\s*:\s*absolute/);

    const datePickerSource = extractFunction('initDatePicker');
    assert.match(datePickerSource, /rect\.left\s*\+\s*\(rect\.width\s*-\s*width\)\s*\/\s*2/);
    assert.match(datePickerSource, /schedule-picker-close/);
    assert.match(extractFunction('setScheduleConfigExpanded'), /datePickerOpen[\s\S]*schedule-picker-close/);
    assert.match(stylesSource, /\.gj-calendar-days\s*\{[^}]*row-gap\s*:\s*3px/);
    assert.match(stylesSource, /button\.in-range\s*\{[^}]*border-radius\s*:\s*0/);
});

test('schedule payload sends a sorted, non-overlapping intervals array', () => {
    const helpers = evaluate(`
        ${SCHEDULE_TIME_PATTERN_SOURCE}
        ${extractFunction('scheduleTimeToMinutes')}
        ${extractFunction('normalizeScheduleIntervals')}
        ${extractFunction('schedulePayloadFromValues')}
        ${extractFunction('validateSchedulePayload')}
        ({ schedulePayloadFromValues, validateSchedulePayload });
    `);
    const payload = helpers.schedulePayloadFromValues({
        enabled: true,
        mode: 'weekdays',
        intervals: [{ start: '14:00', end: '18:00' }, { start: '08:00', end: '12:00' }],
        weekdays: [4, 0, 4],
        dateStart: '',
        dateEnd: '',
    });
    assert.equal(JSON.stringify(payload.intervals), JSON.stringify([{ start: '08:00', end: '12:00' }, { start: '14:00', end: '18:00' }]));
    assert.equal(payload.mode, 'weekdays');
    assert.equal(helpers.validateSchedulePayload(payload), '');
});

test('schedule validation rejects empty, reversed, and overlapping intervals', () => {
    const helpers = evaluate(`
        ${SCHEDULE_TIME_PATTERN_SOURCE}
        ${extractFunction('scheduleTimeToMinutes')}
        ${extractFunction('validateSchedulePayload')}
        ({ validateSchedulePayload });
    `);
    const base = { enabled: true, mode: 'daily', weekdays: [], dateStart: '', dateEnd: '' };
    assert.equal(helpers.validateSchedulePayload({ ...base, intervals: [] }), '请至少添加一个投递时段');
    assert.equal(helpers.validateSchedulePayload({ ...base, intervals: [{ start: '18:00', end: '09:00' }] }), '每个时段的结束时间必须晚于开始时间');
    assert.equal(helpers.validateSchedulePayload({ ...base, intervals: [{ start: '08:00', end: '12:00' }, { start: '11:00', end: '15:00' }] }), '时段之间不能重叠');
    assert.equal(helpers.validateSchedulePayload({ ...base, intervals: [{ start: '08:00', end: '12:00' }, { start: '14:00', end: '18:00' }] }), '');
});

test('schedule helpers summarize intervals and reverse date ranges', () => {
    const helpers = evaluate(`
        ${SCHEDULE_TIME_PATTERN_SOURCE}
        ${extractFunction('scheduleTimeToMinutes')}
        ${extractFunction('scheduleIntervalsSummary')}
        ${extractFunction('scheduleTotalMinutes')}
        ${extractFunction('normalizeDateRange')}
        ({ scheduleIntervalsSummary, scheduleTotalMinutes, normalizeDateRange });
    `);
    assert.equal(helpers.scheduleIntervalsSummary([{ start: '08:00', end: '12:00' }, { start: '14:00', end: '18:00' }]), '08:00-12:00、14:00-18:00');
    assert.equal(helpers.scheduleTotalMinutes([{ start: '08:00', end: '12:00' }, { start: '14:00', end: '18:00' }]), 480);
    assert.equal(JSON.stringify(helpers.normalizeDateRange('2026-07-24', '2026-07-20')), JSON.stringify(['2026-07-20', '2026-07-24']));
});

test('schedule progress text formats remaining time', () => {
    const helpers = evaluate(`
        ${extractFunction('scheduleProgressText')}
        ({ scheduleProgressText });
    `);
    assert.equal(helpers.scheduleProgressText(3725), '1:02:05');
    assert.equal(helpers.scheduleProgressText(125), '02:05');
    assert.equal(helpers.scheduleProgressText(0), '00:00');
});
