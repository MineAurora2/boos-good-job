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

test('schedule editor exposes the accessible dial and styled picker contracts', () => {
    assert.match(htmlSource, /id="scheduleDurationDial"/);
    assert.match(htmlSource, /id="scheduleDurationHandle"[^>]*role="slider"[^>]*aria-valuemin="1"[^>]*aria-valuemax="24"/);
    assert.doesNotMatch(htmlSource, /id="scheduleDurationDial"[\s\S]*?<svg[^>]*aria-hidden="true"[\s\S]*?id="scheduleDurationHandle"/);
    assert.doesNotMatch(htmlSource, /id="scheduleDurationMinutes"/);
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
    assert.doesNotMatch(stylesSource, /\.schedule-feedback:empty\s*\{[^}]*display\s*:\s*none/);

    assert.match(appSource, /scheduleConfigCollapse/);
    assert.match(appSource, /aria-hidden/);
    assert.match(appSource, /inert/);
});

test('duration dial has no separate plus or minus stepper controls', () => {
    assert.doesNotMatch(htmlSource, /scheduleDuration(?:Decrease|Increase)|schedule-duration-steppers/);
    assert.doesNotMatch(appSource, /scheduleDuration(?:Decrease|Increase)|schedule-duration-steppers/);
    assert.doesNotMatch(stylesSource, /schedule-duration-steppers/);
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
    assert.match(datePickerSource, /openSingle[\s\S]*close\(false\)/);
    assert.match(datePickerSource, /openRange[\s\S]*close\(false\)/);
    assert.match(extractFunction('setScheduleConfigExpanded'), /datePickerOpen[\s\S]*schedule-picker-close/);
    assert.match(stylesSource, /\.gj-calendar-days\s*\{[^}]*row-gap\s*:\s*3px/);
    assert.match(stylesSource, /button\.in-range\s*\{[^}]*border-radius\s*:\s*0/);
});

test('schedule payload preserves the backend contract with whole-hour durations', () => {
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
    assert.equal(JSON.stringify(payload), JSON.stringify({ enabled: true, mode: 'weekly', startTime: '09:30', durationMinutes: 120, weekdays: [0, 4], dateStart: '', dateEnd: '' }));
    assert.equal(helpers.validateSchedulePayload({ ...payload, durationMinutes: 90 }), '持续时长请选择 1 至 24 小时');
});

test('schedule helpers cover legacy values, cross-midnight arcs, keys, and reverse ranges', () => {
    const helpers = evaluate(`
        ${extractFunction('durationHoursFromMinutes')}
        ${extractFunction('scheduleWindowModel')}
        ${extractFunction('durationHoursFromDialAngle')}
        ${extractFunction('adjustScheduleDurationByKey')}
        ${extractFunction('normalizeDateRange')}
        ({ durationHoursFromMinutes, scheduleWindowModel, durationHoursFromDialAngle, adjustScheduleDurationByKey, normalizeDateRange });
    `);
    assert.equal(helpers.durationHoursFromMinutes(90), 2);
    assert.equal(helpers.scheduleWindowModel('23:30', 2).summary, '23:30 → 次日 01:30');
    assert.equal(helpers.durationHoursFromDialAngle('23:30', 22.5), 2);
    assert.equal(helpers.adjustScheduleDurationByKey(2, 'ArrowRight'), 3);
    assert.equal(JSON.stringify(helpers.normalizeDateRange('2026-07-24', '2026-07-20')), JSON.stringify(['2026-07-20', '2026-07-24']));
});
