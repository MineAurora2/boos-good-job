'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const SCRIPT_PATH = path.join(ROOT, 'web_script.js');
const scriptSource = fs.readFileSync(SCRIPT_PATH, 'utf8');

function createStorage(initial = {}) {
    const values = new Map(Object.entries(initial).map(([key, value]) => [key, String(value)]));
    return {
        get length() {
            return values.size;
        },
        key(index) {
            return [...values.keys()][Number(index)] ?? null;
        },
        getItem(key) {
            return values.has(String(key)) ? values.get(String(key)) : null;
        },
        setItem(key, value) {
            values.set(String(key), String(value));
        },
        removeItem(key) {
            values.delete(String(key));
        },
        clear() {
            values.clear();
        },
    };
}

function loadHooks(globals = {}) {
    const localStorage = globals.localStorage || createStorage();
    const windowOverrides = globals.window || {};
    const window = {
        __GOODJOBS_TEST__: true,
        addEventListener() {},
        removeEventListener() {},
        setTimeout,
        clearTimeout,
        ...windowOverrides,
    };
    const contextGlobals = { ...globals };
    delete contextGlobals.window;
    delete contextGlobals.localStorage;
    const context = vm.createContext({
        AbortController,
        console,
        clearInterval,
        clearTimeout,
        document: {},
        localStorage,
        queueMicrotask,
        setInterval,
        setTimeout,
        window,
        ...contextGlobals,
    });
    new vm.Script(scriptSource, { filename: SCRIPT_PATH }).runInContext(context);
    assert.ok(window.__GOODJOBS_TEST_HOOKS__, 'web_script.js did not publish test hooks');
    return { hooks: window.__GOODJOBS_TEST_HOOKS__, localStorage, window };
}

function createFakeDocument() {
    const createElement = (tagName) => {
        const attributes = new Map();
        const listeners = new Map();
        const element = {
            tagName: String(tagName).toUpperCase(),
            children: [],
            style: { cssText: '' },
            dataset: {},
            className: '',
            id: '',
            innerText: '',
            textContent: '',
            title: '',
            type: '',
            scrollTop: 0,
            scrollHeight: 0,
            appendChild(child) {
                this.children.push(child);
                child.parentNode = this;
                child.parentElement = this;
                return child;
            },
            removeChild(child) {
                const index = this.children.indexOf(child);
                if (index >= 0) this.children.splice(index, 1);
                return child;
            },
            remove() {
                this.parentNode?.removeChild(this);
            },
            addEventListener(type, listener) {
                if (!listeners.has(type)) listeners.set(type, []);
                listeners.get(type).push(listener);
            },
            removeEventListener(type, listener) {
                if (!listeners.has(type)) return;
                listeners.set(type, listeners.get(type).filter((item) => item !== listener));
            },
            dispatchEvent(event) {
                event.target = event.target || this;
                for (const listener of listeners.get(event.type) || []) listener(event);
                return true;
            },
            click() {
                if (this.disabled) return;
                this.dispatchEvent({ type: 'click', target: this });
            },
            setAttribute(name, value) {
                attributes.set(String(name), String(value));
            },
            getAttribute(name) {
                return attributes.get(String(name)) ?? null;
            },
            querySelector(selector) {
                const matches = (candidate) => {
                    if (selector === '[data-goodjobs-connection]') return candidate.dataset.goodjobsConnection !== undefined;
                    if (selector === '[data-goodjobs-execution]') return candidate.dataset.goodjobsExecution !== undefined;
                    const dataMatch = selector.match(/^\[data-([a-z0-9-]+)\]$/i);
                    if (dataMatch) {
                        const key = dataMatch[1].replace(/-([a-z])/g, (_match, char) => char.toUpperCase());
                        return candidate.dataset[key] !== undefined;
                    }
                    if (/^[a-z]+$/i.test(selector)) return candidate.tagName === selector.toUpperCase();
                    return false;
                };
                const queue = [...this.children];
                while (queue.length) {
                    const candidate = queue.shift();
                    if (matches(candidate)) return candidate;
                    queue.push(...candidate.children);
                }
                return null;
            },
            querySelectorAll(selector) {
                const result = [];
                const queue = [...this.children];
                while (queue.length) {
                    const candidate = queue.shift();
                    const dataMatch = selector.match(/^\[data-([a-z0-9-]+)\]$/i);
                    const dataKey = dataMatch?.[1].replace(/-([a-z])/g, (_match, char) => char.toUpperCase());
                    if ((dataKey && candidate.dataset[dataKey] !== undefined)
                        || (/^[a-z]+$/i.test(selector) && candidate.tagName === selector.toUpperCase())) {
                        result.push(candidate);
                    }
                    queue.push(...candidate.children);
                }
                return result;
            },
            contains(candidate) {
                if (candidate === this) return true;
                return this.children.some((child) => child.contains?.(candidate));
            },
            focus() {},
        };
        Object.defineProperty(element, 'firstChild', {
            get() {
                return this.children[0] || null;
            },
        });
        return element;
    };
    const body = createElement('body');
    return {
        createElement,
        body,
        getElementById(id) {
            const queue = [body];
            while (queue.length) {
                const candidate = queue.shift();
                if (candidate.id === id) return candidate;
                queue.push(...candidate.children);
            }
            return null;
        },
    };
}

test('extracts BOSS qualifications from current job header selectors', () => {
    const { hooks } = loadHooks();
    const queriedSelectors = [];
    const root = {
        querySelector(selector) {
            queriedSelectors.push(selector);
            if (selector === '.job-primary .text-experiece, .job-primary .text-experience') {
                return { innerText: ' 1-3年 ' };
            }
            if (selector === '.job-primary .text-degree') {
                return { innerText: ' 本科 ' };
            }
            return null;
        },
    };

    const qualifications = hooks.extractJobQualifications(root);

    assert.equal(qualifications.experience, '1-3年');
    assert.equal(qualifications.education, '本科');
    assert.equal(queriedSelectors.includes('meta[name="description"]'), false);
});

test('keeps a browser instance job card visible when delivery is duplicated', () => {
    const { hooks } = loadHooks();
    assert.equal(typeof hooks.createDuplicateDecision, 'function');

    const decision = hooks.createDuplicateDecision(
        {
            workerId: 'worker-current',
            accountId: 'account-current',
            company: 'Acme',
            title: 'Backend Engineer',
            score: 88,
            deductions: [{ keyword: 'legacy', deductStars: 1 }],
            decisionState: 'claiming',
            finalPassed: true,
        },
        {
            company: 'Acme',
            title: 'Backend Engineer',
            salary: '20-30K',
            location: 'Shenzhen',
            hrActive: 'online',
            hrActiveLevel: 'online',
        },
        {
            existing: {
                account_id: 'account-owner',
                worker_id: 'worker-owner',
                status: 'sent',
            },
        },
    );

    assert.equal(decision.decisionState, 'duplicate');
    assert.equal(decision.finalPassed, false);
    assert.equal(decision.company, 'Acme');
    assert.equal(decision.title, 'Backend Engineer');
    assert.equal(decision.salary, '20-30K');
    assert.equal(decision.location, 'Shenzhen');
    assert.equal(decision.score, 88);
    assert.deepEqual(decision.deductions, [{ keyword: 'legacy', deductStars: 1 }]);
    assert.equal(decision.duplicateOf.workerId, 'worker-owner');
    assert.equal(decision.duplicateOf.accountId, 'account-owner');
    assert.match(decision.decisionReason, /Acme/);
    assert.match(decision.decisionReason, /Backend Engineer/);

    const action = hooks.createDuplicateActionPayload(
        'search',
        { ...decision, salary: '20-30K', location: 'Shenzhen' },
        decision,
        { accountId: 'account-current', workerId: 'worker-current' },
        'backend',
    );
    assert.equal(action.action, 'company_duplicate_skipped');
    assert.equal(action.existingWorkerId, 'worker-owner');
    assert.equal(action.workerId, 'worker-current');
    assert.equal(action.keyword, 'backend');
});

test('all duplicate delivery paths publish the preserved decision card', () => {
    assert.ok((scriptSource.match(/publishDuplicateDecision/g) || []).length >= 2);
    assert.match(scriptSource, /deliveryFlow\.isDuplicate\(prepared\)/);
    assert.match(scriptSource, /DUPLICATE_CARD_RETENTION_MS/);
    assert.match(scriptSource, /duplicateCardUntil/);
    assert.match(scriptSource, /action: 'company_duplicate_skipped'/);
    assert.match(scriptSource, /const publishDecision = \(currentJob, currentDecision, phase = '聊天岗位匹配评分', state = 'evaluating'\)/);
    assert.match(scriptSource, /if \(data\.currentDecision\.decisionState === 'duplicate'\) \{[\s\S]*runtime\.duplicateCard = data\.currentDecision;[\s\S]*runtime\.duplicateCardUntil = Date\.now\(\) \+ DUPLICATE_CARD_RETENTION_MS;[\s\S]*\} else \{/);
});

test('detail delay uses 200ms slices and the dedicated range', async () => {
    const { hooks } = loadHooks();
    assert.equal(typeof hooks.antiDetection, 'object');
    assert.equal(typeof hooks.tools, 'object');
    hooks.OPTIONS.antiDetectionEnabled = true;
    hooks.OPTIONS.detailRandomDelayMinMs = 450;
    hooks.OPTIONS.detailRandomDelayMaxMs = 450;
    const sleeps = [];
    hooks.tools.asyncSleep = async (ms) => sleeps.push(ms);

    assert.equal(await hooks.antiDetection.detailDelay(), true);
    assert.deepEqual(sleeps, [200, 200, 50]);
});

test('detail delay is disabled by the global switch or a zero detail range', async () => {
    const { hooks } = loadHooks();
    const sleeps = [];
    hooks.tools.asyncSleep = async (ms) => sleeps.push(ms);
    hooks.OPTIONS.detailRandomDelayMinMs = 450;
    hooks.OPTIONS.detailRandomDelayMaxMs = 450;
    hooks.OPTIONS.antiDetectionEnabled = false;
    assert.equal(await hooks.antiDetection.detailDelay(), true);

    hooks.OPTIONS.antiDetectionEnabled = true;
    hooks.OPTIONS.detailRandomDelayMinMs = 0;
    hooks.OPTIONS.detailRandomDelayMaxMs = 0;
    assert.equal(await hooks.antiDetection.detailDelay(), true);
    assert.deepEqual(sleeps, []);
});

test('detail delay honors an interruption callback even when no wait is configured', async () => {
    const { hooks } = loadHooks();
    hooks.OPTIONS.antiDetectionEnabled = false;
    hooks.OPTIONS.detailRandomDelayMinMs = 0;
    hooks.OPTIONS.detailRandomDelayMaxMs = 0;

    assert.equal(await hooks.antiDetection.detailDelay(() => true), false);
});

test('detail delay stops between slices when its interrupt callback returns true', async () => {
    const { hooks } = loadHooks();
    hooks.OPTIONS.antiDetectionEnabled = true;
    hooks.OPTIONS.detailRandomDelayMinMs = 450;
    hooks.OPTIONS.detailRandomDelayMaxMs = 450;
    const sleeps = [];
    let checks = 0;
    hooks.tools.asyncSleep = async (ms) => sleeps.push(ms);

    const result = await hooks.antiDetection.detailDelay(() => ++checks > 1);

    assert.equal(result, false);
    assert.deepEqual(sleeps, [200]);
    assert.equal(checks, 2);
});

test('random delay ranges are closed and delivery/detail ranges stay independent', async () => {
    const lowMath = Object.create(Math);
    lowMath.random = () => 0;
    const { hooks: lowHooks } = loadHooks({ Math: lowMath });
    assert.equal(lowHooks.antiDetection.randomDelayMs(10, 20), 10);

    const highMath = Object.create(Math);
    highMath.random = () => 0.999999999;
    const { hooks: highHooks } = loadHooks({ Math: highMath });
    assert.equal(highHooks.antiDetection.randomDelayMs(10, 20), 20);

    highHooks.OPTIONS.antiDetectionEnabled = true;
    highHooks.OPTIONS.randomDelayMinMs = 10;
    highHooks.OPTIONS.randomDelayMaxMs = 10;
    highHooks.OPTIONS.detailRandomDelayMinMs = 30;
    highHooks.OPTIONS.detailRandomDelayMaxMs = 30;
    const sleeps = [];
    highHooks.tools.asyncSleep = async (ms) => sleeps.push(ms);
    assert.equal(await highHooks.antiDetection.delay(), true);
    assert.equal(await highHooks.antiDetection.detailDelay(), true);
    assert.deepEqual(sleeps, [10, 30]);
});

test('detail delay gates one search open and both chat job clicks before timestamps', () => {
    const searchStart = scriptSource.indexOf('const getJobInfo = async');
    const searchEnd = scriptSource.indexOf('// 接收职位信息', searchStart);
    assert.ok(searchStart >= 0 && searchEnd > searchStart);
    const searchSource = scriptSource.slice(searchStart, searchEnd);
    assert.equal((searchSource.match(/await deliveryFlow\.beforeDetailOpen\(\(\) => control\.stopped \|\| this\.pause\)/g) || []).length, 1);
    assert.match(searchSource, /if \(!opening\.allowed\)/);
    assert.ok(searchSource.indexOf('beforeDetailOpen') < searchSource.indexOf('tools.openTabNSetTimestamp'));

    const clickMatches = [...scriptSource.matchAll(/chatInfo\.jobEl\.click\(\)/g)];
    assert.equal(clickMatches.length, 2);
    for (const match of clickMatches) {
        const before = scriptSource.slice(Math.max(0, match.index - 900), match.index);
        assert.match(before, /await deliveryFlow\.beforeDetailOpen\(\(\) => this\.pause\)/);
        assert.match(before, /if \(!opening\.allowed\)[\s\S]*?continue;[\s\S]*?localStorage\.setItem/);
        assert.ok(before.lastIndexOf('beforeDetailOpen') < before.lastIndexOf('localStorage.setItem'));
    }
});

test('BOSS experience selector supports current and corrected class spellings', () => {
    const { hooks } = loadHooks();
    const queriedSelectors = [];

    hooks.extractJobQualifications({
        querySelector(selector) {
            queriedSelectors.push(selector);
            return null;
        },
    });

    const experienceSelector = queriedSelectors[0];
    assert.ok(experienceSelector.includes('.text-experiece'));
    assert.ok(experienceSelector.includes('.text-experience'));
});

test('falls back to BOSS metadata for missing qualifications', () => {
    const { hooks } = loadHooks();
    const description = '示例公司运维工程师招聘，薪资：10-15K，地点：深圳，要求：5-10年，学历：硕士，福利：五险一金。';
    const root = {
        querySelector(selector) {
            if (selector === 'meta[name="description"]') {
                return {
                    getAttribute(name) {
                        return name === 'content' ? description : null;
                    },
                };
            }
            return null;
        },
    };

    const qualifications = hooks.extractJobQualifications(root);

    assert.equal(qualifications.experience, '5-10年');
    assert.equal(qualifications.education, '硕士');
});

test('direct BOSS qualifications independently take precedence over metadata', () => {
    const { hooks } = loadHooks();
    const description = '要求：5-10年，学历：硕士。';
    const createRoot = ({ experience = null, education = null }) => ({
        querySelector(selector) {
            if (selector === '.job-primary .text-experiece, .job-primary .text-experience') {
                return experience === null ? null : { innerText: experience };
            }
            if (selector === '.job-primary .text-degree') {
                return education === null ? null : { innerText: education };
            }
            if (selector === 'meta[name="description"]') {
                return { getAttribute: () => description };
            }
            return null;
        },
    });

    const directExperience = hooks.extractJobQualifications(createRoot({ experience: '1-3年' }));
    assert.equal(directExperience.experience, '1-3年');
    assert.equal(directExperience.education, '硕士');

    const directEducation = hooks.extractJobQualifications(createRoot({ education: '本科' }));
    assert.equal(directEducation.experience, '5-10年');
    assert.equal(directEducation.education, '本科');
});

test('returns empty qualifications when roots or metadata accessors are absent', () => {
    const { hooks } = loadHooks();

    for (const root of [null, {}, { querySelector: () => ({}) }]) {
        const qualifications = hooks.extractJobQualifications(root);
        assert.equal(qualifications.experience, '');
        assert.equal(qualifications.education, '');
    }
});

test('normalizes every supported HR active label and unknown values', () => {
    const { hooks } = loadHooks();
    const cases = new Map([
        ['在线', 'online'],
        ['当前在线', 'online'],
        ['刚刚在线', 'online'],
        ['刚刚活跃', 'just_now'],
        ['刚活跃', 'just_now'],
        ['今日活跃', 'today'],
        ['3日内活跃', 'within_3_days'],
        ['3天内活跃', 'within_3_days'],
        ['本周活跃', 'this_week'],
        ['2周内活跃', 'within_2_weeks'],
        ['本月活跃', 'this_month'],
        ['2月内活跃', 'within_2_months'],
        ['3月内活跃', 'within_3_months'],
        ['4月内活跃', 'within_4_months'],
        ['5月内活跃', 'within_5_months'],
        ['近半年活跃', 'within_half_year'],
        ['半年前活跃', 'half_year_ago'],
        [' 3 天内 活跃 ', 'within_3_days'],
        ['', 'unknown'],
        [null, 'unknown'],
        ['最近活跃', 'unknown'],
    ]);

    for (const [label, expected] of cases) {
        assert.equal(hooks.normalizeHrActive(label), expected, String(label));
    }
});

test('HR threshold comparison keeps unknown behind explicit selection', () => {
    const { hooks } = loadHooks();
    hooks.OPTIONS.hrActiveFilterEnabled = true;
    hooks.OPTIONS.hrActiveLevels = null;
    hooks.OPTIONS.hrActiveMinLevel = 'within_3_days';

    assert.equal(hooks.hrActivePasses('online'), true);
    assert.equal(hooks.hrActivePasses('just_now'), true);
    assert.equal(hooks.hrActivePasses('today'), true);
    assert.equal(hooks.hrActivePasses('within_3_days'), true);
    assert.equal(hooks.hrActivePasses('this_week'), false);
    assert.equal(hooks.hrActivePasses('this_month'), false);
    assert.equal(hooks.hrActivePasses('within_2_months'), false);
    assert.equal(hooks.hrActivePasses('half_year_ago'), false);
    assert.equal(hooks.hrActivePasses('unknown'), false);
    assert.equal(hooks.hrActivePasses('invalid'), false);

    hooks.OPTIONS.hrActiveMinLevel = 'online';
    assert.equal(hooks.hrActivePasses('online'), true);
    assert.equal(hooks.hrActivePasses('just_now'), false);

    hooks.OPTIONS.hrActiveFilterEnabled = false;
    assert.equal(hooks.hrActivePasses('this_month'), true);
});

test('HR active multi-select strictly matches selected status', () => {
    const { hooks } = loadHooks();
    hooks.OPTIONS.hrActiveFilterEnabled = true;
    hooks.OPTIONS.hrActiveLevels = ['within_2_weeks'];

    assert.deepEqual(Array.from(hooks.configuredHrActiveLevels()), ['within_2_weeks']);
    assert.equal(hooks.hrActivePasses('within_2_weeks'), true);
    assert.equal(hooks.hrActivePasses('online'), false);
    assert.equal(hooks.hrActivePasses('unknown'), false);

    hooks.OPTIONS.hrActiveLevels = ['unknown'];
    assert.equal(hooks.hrActivePasses('unknown'), true);

    hooks.OPTIONS.hrActiveLevels = [];
    assert.equal(hooks.hrActivePasses('online'), false);
});

test('runtime log entries infer sender, verbosity, and severity for legacy calls', () => {
    const { hooks } = loadHooks();
    const cases = [
        ['--程序启动--', 'system', 'concise', 'info'],
        ['预加载第 2 轮：岗位 10 -> 20', 'queue', 'detailed', 'info'],
        ['重复投递检查服务不可用，为安全起见已跳过本岗位', 'claim', 'normal', 'error'],
        ['打招呼成功', 'delivery', 'concise', 'info'],
    ];

    for (const [message, sender, verbosity, level] of cases) {
        const entry = hooks.createRuntimeLogEntry(message);
        assert.equal(entry.sender, sender, message);
        assert.equal(entry.verbosity, verbosity, message);
        assert.equal(entry.level, level, message);
        assert.equal(entry.message, message);
        assert.equal(Object.hasOwn(entry, 'source'), false);
    }
});

test('explicit runtime log metadata wins without reusing severity as verbosity', () => {
    const { hooks } = loadHooks();
    const loggedAt = '2026-07-17T12:34:56.000Z';
    const entry = hooks.createRuntimeLogEntry('打招呼失败', {
        sender: 'queue',
        verbosity: 'detailed',
        level: 'info',
        loggedAt,
    });

    assert.equal(entry.sender, 'queue');
    assert.equal(entry.verbosity, 'detailed');
    assert.equal(entry.level, 'info');
    assert.equal(entry.loggedAt, loggedAt);
    assert.equal(hooks.createRuntimeLogEntry('打招呼成功', { sender: 'invalid' }).sender, 'delivery');
    assert.equal(hooks.createRuntimeLogEntry('x'.repeat(2100)).message.length, 2000);
});

test('action logs always include identity and filter metadata', () => {
    const { hooks } = loadHooks();
    const identity = { accountId: 'account-alpha', workerId: 'worker-alpha' };
    const cases = [
        ['delivery_claim_rejected', 'claim', 'concise', 'warning'],
        ['greet_queued', 'queue', 'normal', 'info'],
        ['greet_sent', 'delivery', 'concise', 'info'],
        ['job_decision_consumed', 'delivery', 'detailed', 'info'],
        ['job_ai_rejected', 'delivery', 'concise', 'warning'],
        ['job_below_threshold', 'delivery', 'normal', 'warning'],
    ];

    for (const [action, sender, verbosity, level] of cases) {
        const payload = hooks.createRuntimeActionPayload({ action }, identity);
        assert.equal(payload.accountId, identity.accountId, action);
        assert.equal(payload.workerId, identity.workerId, action);
        assert.equal(payload.sender, sender, action);
        assert.equal(payload.verbosity, verbosity, action);
        assert.equal(payload.level, level, action);
    }

    const explicit = hooks.createRuntimeActionPayload({
        action: 'greet_sent',
        accountId: 'account-explicit',
        sender: 'system',
        verbosity: 'detailed',
        level: 'warning',
    }, identity);
    assert.equal(explicit.accountId, 'account-explicit');
    assert.equal(explicit.workerId, identity.workerId);
    assert.equal(explicit.sender, 'system');
    assert.equal(explicit.verbosity, 'detailed');
    assert.equal(explicit.level, 'warning');
});

test('qualification and AI decisions queue one heartbeat without awaiting transport', () => {
    const { hooks } = loadHooks();
    assert.equal(typeof hooks.queueRuntimeHeartbeat, 'function');

    let requests = 0;
    const result = hooks.queueRuntimeHeartbeat({
        requestHeartbeat() {
            requests += 1;
            return new Promise(() => {});
        },
    });
    assert.equal(result, undefined);
    assert.equal(requests, 1);

    assert.match(scriptSource, /decisionState = 'qualified'|decisionState:\s*'qualified'/);
    assert.match(scriptSource, /sendRuntimeHeartbeat\(\)/);
});

test('search runtime heartbeats always use the coalescing scheduler', () => {
    const heartbeatStart = scriptSource.indexOf('const sendRuntimeHeartbeat =');
    const heartbeatEnd = scriptSource.indexOf('const transitionIsCurrent =', heartbeatStart);
    const heartbeatHelper = scriptSource.slice(heartbeatStart, heartbeatEnd);
    assert.match(heartbeatHelper, /queueRuntimeHeartbeat\(this\.controlAgent\)/);
    assert.doesNotMatch(heartbeatHelper, /\.pulse\(/);
});

test('chat status heartbeat uses the void scheduler without Promise chaining', () => {
    assert.doesNotMatch(scriptSource, /sendRuntimeHeartbeat\(\)\.catch\(/);
});

test('heartbeat scheduler starts immediately then coalesces triggers for two seconds', () => {
    let now = 0;
    let nextTimerId = 1;
    let intervalDelay = null;
    const timers = new Map();
    class FakeDate extends Date {
        static now() { return now; }
    }
    const runDueTimers = () => {
        const due = [...timers.entries()]
            .filter(([, timer]) => timer.due <= now)
            .sort((left, right) => left[1].due - right[1].due);
        for (const [id, timer] of due) {
            timers.delete(id);
            timer.callback();
        }
    };
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        Date: FakeDate,
        document,
        URL,
        window: {
            location: { pathname: '/web/geek/job' },
            setTimeout(callback, delay) {
                const id = nextTimerId++;
                timers.set(id, { callback, due: now + delay });
                return id;
            },
            clearTimeout(id) { timers.delete(id); },
            setInterval(_callback, delay) { intervalDelay = delay; return 99; },
            clearInterval() {},
        },
    });
    const agent = new hooks.ControlAgent();
    const starts = [];
    agent.pulse = () => { starts.push(now); };
    agent.startControlPolling = () => {};

    agent.start();
    agent.requestHeartbeat();
    agent.requestHeartbeat();

    assert.equal(intervalDelay, 15000);
    assert.deepEqual(starts, [0]);
    assert.equal(timers.size, 1);
    now = 1999;
    runDueTimers();
    assert.deepEqual(starts, [0]);
    now = 2000;
    runDueTimers();
    assert.deepEqual(starts, [0, 2000]);
    assert.equal(timers.size, 0);
});

test('heartbeat scheduler delays one busy trailing request to the minimum interval', async () => {
    let now = 0;
    let nextTimerId = 1;
    const timers = new Map();
    class FakeDate extends Date {
        static now() { return now; }
    }
    const runDueTimers = () => {
        const due = [...timers.entries()]
            .filter(([, timer]) => timer.due <= now)
            .sort((left, right) => left[1].due - right[1].due);
        for (const [id, timer] of due) {
            timers.delete(id);
            timer.callback();
        }
    };
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        Date: FakeDate,
        document,
        URL,
        window: {
            location: { pathname: '/web/geek/job' },
            setTimeout(callback, delay) {
                const id = nextTimerId++;
                timers.set(id, { callback, due: now + delay });
                return id;
            },
            clearTimeout(id) { timers.delete(id); },
        },
    });
    const agent = new hooks.ControlAgent();
    const starts = [];
    const resolvers = [];
    agent.api.heartbeat = () => {
        starts.push(now);
        return new Promise((resolve) => resolvers.push(resolve));
    };

    agent.requestHeartbeat();
    agent.requestHeartbeat();
    agent.requestHeartbeat();
    assert.deepEqual(starts, [0]);
    assert.equal(timers.size, 0);

    now = 1000;
    resolvers.shift()({ ok: true });
    await new Promise(setImmediate);
    assert.deepEqual(starts, [0]);
    assert.equal(timers.size, 1);

    now = 1999;
    runDueTimers();
    assert.deepEqual(starts, [0]);
    now = 2000;
    runDueTimers();
    assert.deepEqual(starts, [0, 2000]);
    resolvers.shift()({ ok: true });
    await new Promise(setImmediate);
});

test('heartbeat scheduler cleanup cancels timers and ignores late triggers', () => {
    let now = 0;
    let nextTimerId = 1;
    const timers = new Map();
    const listeners = new Map();
    const clearedIntervals = [];
    class FakeDate extends Date {
        static now() { return now; }
    }
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        Date: FakeDate,
        document,
        URL,
        window: {
            location: { pathname: '/web/geek/job' },
            addEventListener(type, listener) { listeners.set(type, listener); },
            removeEventListener(type, listener) {
                if (listeners.get(type) === listener) listeners.delete(type);
            },
            setTimeout(callback, delay) {
                const id = nextTimerId++;
                timers.set(id, { callback, due: now + delay });
                return id;
            },
            clearTimeout(id) { timers.delete(id); },
            setInterval() { return 77; },
            clearInterval(id) { clearedIntervals.push(id); },
        },
    });
    const agent = new hooks.ControlAgent();
    const starts = [];
    agent.pulse = () => { starts.push(now); };
    agent.startControlPolling = () => { agent.controlPolling = true; };

    agent.start();
    agent.requestHeartbeat();
    assert.equal(timers.size, 1);
    assert.equal(listeners.has('beforeunload'), true);

    agent.stop();
    assert.deepEqual(clearedIntervals, [77]);
    assert.equal(timers.size, 0);
    assert.equal(agent.controlPolling, false);
    for (const type of ['focus', 'online', 'pageshow', 'beforeunload']) {
        assert.equal(listeners.has(type), false);
    }

    agent.requestHeartbeat();
    assert.deepEqual(starts, [0]);
    assert.equal(timers.size, 0);
});

test('heartbeat scheduler establishes the new session before control polling', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        document,
        URL,
        window: {
            location: { pathname: '/web/geek/job' },
            setInterval() { return 31; },
            clearInterval() {},
        },
    });
    const agent = new hooks.ControlAgent();
    let finishHeartbeat;
    let pollStarts = 0;
    agent.pulse = () => new Promise((resolve) => { finishHeartbeat = resolve; });
    agent.startControlPolling = () => { pollStarts += 1; };

    agent.start();
    assert.equal(pollStarts, 0);

    finishHeartbeat();
    await new Promise(setImmediate);
    assert.equal(pollStarts, 1);
    agent.stop();
});

test('AI rejection uses its own action instead of the rule threshold action', () => {
    const { hooks } = loadHooks();
    assert.equal(typeof hooks.searchRejectionAction, 'function');
    assert.equal(hooks.searchRejectionAction(true, false), 'job_ai_rejected');
    assert.equal(hooks.searchRejectionAction(false, true), 'job_below_threshold');
    assert.equal(hooks.searchRejectionAction(false, false), 'job_ai_rejected');
    assert.equal(hooks.searchRejectionAction(true, true), '');

    assert.match(scriptSource, /searchRejectionAction\(rulePassed, aiPassed\)/);
});

test('Logger is headless and only forwards normalized metadata', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    let starts = 0;
    let pauses = 0;
    let forwarded = null;
    const logger = new hooks.Logger(
        () => { starts += 1; },
        () => { pauses += 1; },
        (message, metadata) => { forwarded = { message, metadata }; }
    );
    await logger.start();
    assert.equal(starts, 1);
    await logger.pause();
    assert.equal(pauses, 1);
    assert.equal(document.body.children.length, 0);

    logger.add('领取成功，等待进入队列', { sender: 'claim', verbosity: 'concise', level: 'warning' });
    assert.equal(forwarded.message, '领取成功，等待进入队列');
    assert.equal(forwarded.metadata.sender, 'claim');
    assert.equal(forwarded.metadata.verbosity, 'concise');
    assert.equal(forwarded.metadata.level, 'warning');
});

test('connection origin is normalized and remains userscript-owned', () => {
    const writes = [];
    const { hooks } = loadHooks({
        URL,
        GM_getValue: (_key, fallback) => fallback,
        GM_setValue: (key, value) => writes.push([key, value]),
    });
    assert.equal(hooks.normalizeServerOrigin('192.168.1.20:48000/'), 'http://192.168.1.20:48000');
    assert.equal(hooks.normalizeServerOrigin('https://jobs.example.com'), 'https://jobs.example.com');
    assert.throws(() => hooks.normalizeServerOrigin('ftp://127.0.0.1:21'), /HTTP/);
    assert.throws(() => hooks.normalizeServerOrigin('https://user:pass@example.com'), /凭据/);
    assert.throws(() => hooks.normalizeServerOrigin('https://example.com/api'), /origin/);
    hooks.connectionSettings.write({ baseUrl: 'http://10.0.0.8:47999/', token: 'secret-token' });
    assert.equal(hooks.OPTIONS.serverHost, 'http://10.0.0.8:47999');
    assert.equal(writes.length, 1);
    hooks.applyFrontendConfig({ serverHost: 'http://127.0.0.1:1', thread: 60 });
    assert.equal(hooks.OPTIONS.serverHost, 'http://10.0.0.8:47999');
    assert.equal(hooks.OPTIONS.thread, 60);
});

test('userscript lifecycle control uses authenticated persisted desired-state PUT', async () => {
    let requestOptions = null;
    const token = 'shared-token-that-is-at-least-32-chars';
    const { hooks } = loadHooks({
        URL,
        GM_getValue: () => ({ baseUrl: 'https://backend.example', token }),
        GM_xmlhttpRequest: (options) => {
            requestOptions = options;
            queueMicrotask(() => options.onload({
                status: 202,
                response: { operationId: 'operation-21', revision: 21, desiredState: 'paused', targetCount: 1 },
            }));
            return { abort() {} };
        },
    });

    const result = await new hooks.Api().setDesiredState('worker/a', 'paused');
    assert.equal(result.revision, 21);
    assert.equal(requestOptions.method, 'PUT');
    assert.equal(requestOptions.url, 'https://backend.example/api/control/desired-state/workers/worker%2Fa');
    assert.equal(requestOptions.headers.Authorization, `Bearer ${token}`);
    assert.deepEqual(JSON.parse(requestOptions.data), { desiredState: 'paused' });
    assert.equal(requestOptions.timeout, 5000);
});

test('status indicator exposes logs and settings without lifecycle buttons', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();
    indicator.update('connected', 'stopped');
    const root = document.getElementById('goodjobs-runtime-status');
    assert.ok(root);
    assert.equal(root.getAttribute('role'), 'group');
    assert.doesNotMatch(root.style.cssText, /pointer-events:none/);
    const statusRow = root.querySelector('[data-goodjobs-status-row]');
    const settingsButton = root.querySelector('[data-goodjobs-settings-toggle]');
    const logButton = root.querySelector('[data-goodjobs-log-toggle]');
    const settings = root.querySelector('[data-goodjobs-settings]');
    assert.ok(statusRow);
    assert.equal(root.querySelector('[data-goodjobs-start]'), null);
    assert.equal(root.querySelector('[data-goodjobs-pause]'), null);
    assert.ok(settingsButton);
    assert.ok(logButton);
    assert.equal(settings.hidden, true);
    assert.equal(settingsButton.textContent, '设置');
    assert.equal(settingsButton.getAttribute('aria-expanded'), 'false');
    assert.equal(settingsButton.getAttribute('aria-controls'), settings.id);
    assert.match(settingsButton.parentElement.style.cssText, /repeat\(2,minmax\(0,1fr\)\)/);

    settingsButton.click();
    assert.equal(settings.hidden, false);
    assert.equal(settingsButton.getAttribute('aria-expanded'), 'true');
    assert.equal(root.dataset.settingsOpen, 'true');
    assert.equal(root.querySelector('[data-goodjobs-connection]').textContent, '后端已连接');
    assert.equal(root.querySelector('[data-goodjobs-execution]').textContent, '脚本：已结束');
    assert.doesNotMatch(root.title, /127\.0\.0\.1|https?:\/\//);

    settingsButton.click();
    assert.equal(settings.hidden, true);
    assert.equal(settingsButton.getAttribute('aria-expanded'), 'false');
    settingsButton.click();
    indicator.closeSettings();
    assert.equal(settings.hidden, true);
    assert.equal(settingsButton.getAttribute('aria-expanded'), 'false');

    let prevented = false;
    statusRow.dispatchEvent({
        type: 'contextmenu',
        preventDefault() { prevented = true; },
        stopPropagation() {},
    });
    assert.equal(prevented, false);
    assert.equal(settings.hidden, true);
    assert.equal(statusRow.title, '');
    assert.doesNotMatch(statusRow.style.cssText, /context-menu/);

    indicator.update('disconnected', 'stopped');
    assert.equal(document.body.children.length, 1);
    assert.equal(settings.hidden, true);
    assert.equal(root.querySelector('[data-goodjobs-connection]').textContent, '后端已断开');
});

test('floating status shows the configured account identifier', () => {
    const document = createFakeDocument();
    const { hooks, localStorage } = loadHooks({ document, URL });
    localStorage.setItem(hooks.deliveryIdentity.accountKey, 'account-visible');

    const indicator = new hooks.StatusIndicator();
    indicator.update('connected', 'stopped');

    const root = document.getElementById('goodjobs-runtime-status');
    const connection = root.querySelector('[data-goodjobs-connection]');
    const execution = root.querySelector('[data-goodjobs-execution]');
    const account = root.querySelector('[data-goodjobs-account]');
    assert.ok(account);
    assert.equal(account.parentElement.children[0], connection);
    assert.equal(account.parentElement.children[1], execution);
    assert.equal(account.parentElement.children[2], account);
    assert.equal(account.textContent, '账号：account-visible');
    assert.equal(account.title, '账号标识：account-visible');
    assert.match(account.style.cssText, /min-width:0/);
    assert.match(account.style.cssText, /overflow:hidden/);
    assert.match(account.style.cssText, /text-overflow:ellipsis/);
    assert.match(account.style.cssText, /white-space:nowrap/);
    assert.match(account.style.cssText, /pointer-events:auto/);
});

test('local log viewer is collapsed by default with accessible controls and stable layout', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();
    indicator.update('connected', 'stopped');

    const root = document.getElementById('goodjobs-runtime-status');
    const toggleButton = root.querySelector('[data-goodjobs-log-toggle]');
    const panel = root.querySelector('[data-goodjobs-log-panel]');
    const list = root.querySelector('[data-goodjobs-log-list]');
    const clearButton = root.querySelector('[data-goodjobs-log-clear]');

    assert.ok(toggleButton);
    assert.ok(panel);
    assert.ok(list);
    assert.ok(clearButton);
    assert.equal(toggleButton.textContent, '日志');
    assert.equal(toggleButton.getAttribute('aria-expanded'), 'false');
    assert.equal(toggleButton.getAttribute('aria-controls'), panel.id);
    assert.equal(panel.hidden, true);
    assert.equal(panel.style.display, 'none');
    const logTitle = panel.querySelector('strong');
    assert.equal(panel.getAttribute('role'), 'region');
    assert.equal(panel.getAttribute('aria-labelledby'), logTitle.id);
    assert.ok(logTitle.id);
    assert.equal(list.getAttribute('role'), 'log');
    assert.equal(list.getAttribute('aria-live'), 'polite');
    assert.equal(list.getAttribute('aria-label'), '本地运行日志列表');
    assert.equal(list.tabIndex, 0);
    assert.equal(root.querySelector('[data-goodjobs-settings]').hidden, true);
    assert.match(toggleButton.parentElement.style.cssText, /repeat\(2,minmax\(0,1fr\)\)/);
    assert.match(root.style.cssText, /max-height:/);
    assert.match(root.style.cssText, /overflow:/);
});

test('local log toggle opens, scrolls to the latest entry, and closes again', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();
    indicator.update('connected', 'stopped');

    const root = document.getElementById('goodjobs-runtime-status');
    const toggleButton = root.querySelector('[data-goodjobs-log-toggle]');
    const panel = root.querySelector('[data-goodjobs-log-panel]');
    const list = root.querySelector('[data-goodjobs-log-list]');
    list.scrollHeight = 480;

    toggleButton.click();
    assert.equal(indicator.logsExpanded, true);
    assert.equal(toggleButton.getAttribute('aria-expanded'), 'true');
    assert.equal(panel.hidden, false);
    assert.equal(panel.style.display, 'grid');
    assert.equal(list.scrollTop, 480);

    toggleButton.click();
    assert.equal(indicator.logsExpanded, false);
    assert.equal(toggleButton.getAttribute('aria-expanded'), 'false');
    assert.equal(panel.hidden, true);
    assert.equal(panel.style.display, 'none');
});

test('local log rows render literal text, time, and restrained level-specific colors', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();
    const unsafeMessage = '<img src=x onerror="window.__unsafe=true">';
    indicator.addLog(hooks.createRuntimeLogEntry(unsafeMessage, {
        loggedAt: '2026-07-19T12:34:56.000Z',
        level: 'warning',
    }));
    indicator.addLog(hooks.createRuntimeLogEntry('上传失败', {
        loggedAt: '2026-07-19T12:35:57.000Z',
        level: 'error',
    }));

    const list = document.getElementById('goodjobs-runtime-status')
        .querySelector('[data-goodjobs-log-list]');
    assert.equal(list.children.length, 0);
    indicator.toggleLogs(true);
    const [warningRow, errorRow] = list.children;
    assert.equal(warningRow.children[1].textContent, unsafeMessage);
    assert.equal(warningRow.querySelector('img'), null);
    assert.match(warningRow.children[0].textContent, /\d{2}:\d{2}:\d{2}/);
    assert.equal(warningRow.dataset.level, 'warning');
    assert.equal(errorRow.dataset.level, 'error');
    assert.match(warningRow.getAttribute('aria-label'), /警告/);
    assert.match(warningRow.getAttribute('aria-label'), /<img src=x/);
    assert.match(errorRow.getAttribute('aria-label'), /错误/);
    assert.match(errorRow.getAttribute('aria-label'), /上传失败/);
    assert.notEqual(warningRow.style.cssText, errorRow.style.cssText);
    assert.match(warningRow.style.cssText, /color:/);
    assert.match(errorRow.style.cssText, /color:/);
});

test('local log history keeps exactly the latest 100 entries', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();

    for (let index = 0; index <= 100; index += 1) {
        indicator.addLog(hooks.createRuntimeLogEntry(`entry-${index}`));
    }

    const list = document.getElementById('goodjobs-runtime-status')
        .querySelector('[data-goodjobs-log-list]');
    assert.equal(indicator.logEntries.length, 100);
    assert.equal(indicator.logEntries[0].message, 'entry-1');
    assert.equal(indicator.logEntries.at(-1).message, 'entry-100');
    assert.equal(list.children.length, 0);
    indicator.toggleLogs(true);
    assert.equal(list.children.length, 100);
    assert.equal(list.children[0].children[1].textContent, 'entry-1');
});

test('expanded local log rows append incrementally and preserve identity and scroll on status updates', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();
    indicator.addLog(hooks.createRuntimeLogEntry('first', { loggedAt: '2026-07-19T12:34:56.000Z' }));

    const list = document.getElementById('goodjobs-runtime-status')
        .querySelector('[data-goodjobs-log-list]');
    assert.equal(list.children.length, 0);
    indicator.toggleLogs(true);
    const firstRow = list.children[0];
    list.scrollHeight = 600;
    list.scrollTop = 37;

    indicator.update('disconnected', 'running');
    assert.strictEqual(list.children[0], firstRow);
    assert.equal(list.scrollTop, 37);

    indicator.addLog(hooks.createRuntimeLogEntry('second', { loggedAt: '2026-07-19T12:35:56.000Z' }));
    assert.strictEqual(list.children[0], firstRow);
    assert.equal(list.children.length, 2);
});

test('expanded local log cap removes only the oldest DOM row', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const indicator = new hooks.StatusIndicator();

    for (let index = 0; index < 100; index += 1) {
        indicator.addLog(hooks.createRuntimeLogEntry(`entry-${index}`));
    }
    const list = document.getElementById('goodjobs-runtime-status')
        .querySelector('[data-goodjobs-log-list]');
    assert.equal(list.children.length, 0);
    indicator.toggleLogs(true);
    const secondRow = list.children[1];

    indicator.addLog(hooks.createRuntimeLogEntry('entry-100'));
    assert.equal(list.children.length, 100);
    assert.strictEqual(list.children[0], secondRow);
    assert.equal(list.children[0].children[1].textContent, 'entry-1');
    assert.equal(list.children.at(-1).children[1].textContent, 'entry-100');
});

test('clearing visible local logs leaves the pending upload entry intact', () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    agent.queueLog('等待上传', {
        loggedAt: '2026-07-19T12:34:56.000Z',
        level: 'info',
    });

    const root = document.getElementById('goodjobs-runtime-status');
    const list = root.querySelector('[data-goodjobs-log-list]');
    const clearButton = root.querySelector('[data-goodjobs-log-clear]');
    assert.equal(agent.logs.length, 1);
    assert.equal(agent.statusIndicator.logEntries.length, 1);
    assert.strictEqual(agent.statusIndicator.logEntries[0], agent.logs[0]);
    assert.equal(list.children.length, 0);

    agent.statusIndicator.toggleLogs(true);
    assert.equal(list.children.length, 1);
    clearButton.click();
    assert.equal(agent.statusIndicator.logEntries.length, 0);
    assert.equal(list.children.length, 0);
    assert.equal(agent.logs.length, 1);
    assert.equal(agent.logs[0].message, '等待上传');

    agent.queueLog('第二条待上传', { level: 'warning' });
    assert.equal(list.children.length, 1);
    agent.statusIndicator.toggleLogs(false);
    clearButton.click();
    assert.equal(agent.statusIndicator.logEntries.length, 0);
    assert.equal(list.children.length, 0);
    assert.equal(agent.logs.length, 2);
});

test('floating settings validate and persist identity plus backend without exposing the old token', () => {
    const document = createFakeDocument();
    const writes = [];
    let reloads = 0;
    const oldToken = 'a'.repeat(32);
    const { hooks, localStorage } = loadHooks({
        document,
        URL,
        GM_getValue: (_key, fallback) => ({ baseUrl: 'http://127.0.0.1:47999', token: oldToken }) || fallback,
        GM_setValue: (key, value) => writes.push([key, value]),
        window: {
            location: { reload() { reloads += 1; } },
            setTimeout(callback) { callback(); return 1; },
        },
    });
    localStorage.setItem(hooks.deliveryIdentity.accountKey, 'account-old');
    const workerId = hooks.deliveryIdentity.get().workerId;
    const indicator = new hooks.StatusIndicator();
    indicator.update('connected', 'stopped');
    indicator.toggleSettings(true);
    const inputs = indicator.settingsInputs;
    assert.equal(inputs.account.value, 'account-old');
    assert.equal(inputs.backend.value, 'http://127.0.0.1:47999');
    assert.equal(inputs.token.type, 'password');

    inputs.account.value = 'account-new';
    inputs.backend.value = 'http://10.0.0.8:48000';
    assert.equal(indicator.saveSettings(), true);
    assert.equal(localStorage.getItem(hooks.deliveryIdentity.accountKey), 'account-new');
    assert.equal(hooks.deliveryIdentity.get().workerId, workerId);
    assert.equal(writes.at(-1)[1].baseUrl, 'http://10.0.0.8:48000');
    assert.equal(writes.at(-1)[1].token, '');
    assert.equal(reloads, 1);

    indicator.toggleSettings(true);
    inputs.backend.value = 'https://example.com/path';
    const writesBeforeInvalid = writes.length;
    assert.equal(indicator.saveSettings(), false);
    assert.equal(writes.length, writesBeforeInvalid);
    assert.match(indicator.settingsError.textContent, /origin/);

    indicator.update('connected', 'running');
    inputs.backend.value = 'http://127.0.0.1:47999';
    assert.equal(indicator.saveSettings(), false);
    assert.match(indicator.settingsError.textContent, /先暂停/);
});

test('first stopped control is acknowledged without invoking the executor', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const calls = [];
    agent.pulse = async () => undefined;
    agent.attachRunner({
        applyDesiredState(desiredState) {
            calls.push(desiredState);
            return Promise.resolve(desiredState);
        },
    });

    agent.applyControl({
        epoch: 'epoch-initial', revision: 0, operationId: '', desiredState: 'stopped',
    });
    await new Promise(setImmediate);

    assert.deepEqual(calls, []);
    assert.equal(agent.controlAck.status, 'applied');
    assert.equal(agent.executionState, 'stopped');
});

test('backend disconnect does not pause a running executor without backend control', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const calls = [];
    agent.executionState = 'running';
    agent.api.heartbeat = async () => ({ ok: false, httpStatus: 0 });
    agent.attachRunner({
        applyDesiredState(desiredState) {
            calls.push(desiredState);
            return Promise.resolve(desiredState);
        },
    });

    await agent.pulse();
    await new Promise(setImmediate);

    assert.deepEqual(calls, []);
    assert.equal(agent.executionState, 'running');
    assert.equal(agent.connectionState, 'disconnected');
});

test('control agent applies revisions idempotently across running paused and stopped', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const calls = [];
    agent.pulse = async () => undefined;
    agent.attachRunner({
        async applyDesiredState(desiredState) {
            calls.push(desiredState);
            return desiredState;
        },
    });
    const control = (revision, desiredState) => ({
        epoch: 'epoch-1', revision, operationId: `operation-${revision}`, desiredState,
    });

    agent.applyControl(control(1, 'running'));
    await new Promise(setImmediate);
    assert.deepEqual(calls, ['running']);
    assert.equal(agent.controlAck.status, 'applied');
    assert.equal(agent.executionState, 'running');

    agent.applyControl(control(2, 'running'));
    await new Promise(setImmediate);
    assert.deepEqual(calls, ['running']);
    assert.equal(agent.controlAck.revision, 2);

    agent.applyControl(control(3, 'paused'));
    await new Promise(setImmediate);
    agent.applyControl(control(4, 'stopped'));
    await new Promise(setImmediate);
    assert.deepEqual(calls, ['running', 'paused', 'stopped']);
    assert.equal(agent.executionState, 'stopped');
});

test('floating controls submit once and never bypass the backend state machine', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        document,
        URL,
        window: {
            setTimeout(callback) { queueMicrotask(callback); return 1; },
            clearTimeout() {},
        },
    });
    const agent = new hooks.ControlAgent();
    let resolveRequest;
    const requests = [];
    const runnerCalls = [];
    agent.connectionState = 'connected';
    agent.statusIndicator.update('connected', 'stopped');
    agent.api.setDesiredState = (workerId, desiredState) => {
        requests.push({ workerId, desiredState });
        return new Promise((resolve) => { resolveRequest = resolve; });
    };
    agent.attachRunner({ applyDesiredState: (state) => runnerCalls.push(state) });

    const first = agent.requestDesiredState('running');
    const duplicate = await agent.requestDesiredState('paused');
    assert.equal(duplicate, null);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].desiredState, 'running');
    assert.equal(runnerCalls.length, 0);
    assert.equal(agent.executionState, 'stopped');

    resolveRequest({ operationId: 'operation-ui', revision: 22, desiredState: 'running', targetCount: 1 });
    await first;
    assert.equal(agent.controlRequestBusy, false);
    assert.equal(runnerCalls.length, 0);
    assert.equal(agent.executionState, 'stopped');
});

test('control agent uploads applying immediately and retains applied ack through throttling', async () => {
    let now = 0;
    let nextTimerId = 1;
    const timers = new Map();
    class FakeDate extends Date {
        static now() { return now; }
    }
    const runDueTimers = () => {
        for (const [id, timer] of [...timers.entries()]) {
            if (timer.due > now) continue;
            timers.delete(id);
            timer.callback();
        }
    };
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        Date: FakeDate,
        document,
        URL,
        window: {
            setTimeout(callback, delay) {
                const id = nextTimerId++;
                timers.set(id, { callback, due: now + delay });
                return id;
            },
            clearTimeout(id) { timers.delete(id); },
        },
    });
    const agent = new hooks.ControlAgent();
    const heartbeatStatuses = [];
    let finishRunning;
    agent.pulse = async () => {
        heartbeatStatuses.push(agent.controlAck?.status || 'none');
    };
    agent.attachRunner({
        applyDesiredState() {
            return new Promise((resolve) => { finishRunning = resolve; });
        },
    });

    agent.applyControl({
        epoch: 'epoch-fast', revision: 9, operationId: 'operation-fast', desiredState: 'running',
    });
    await new Promise(setImmediate);

    assert.equal(heartbeatStatuses[0], 'applying');
    assert.equal(agent.controlAck.status, 'applying');
    finishRunning('running');
    await new Promise(setImmediate);
    assert.equal(agent.controlAck.status, 'applied');
    assert.equal(heartbeatStatuses.at(-1), 'applying');
    assert.equal(timers.size, 1);

    now = 2000;
    runDueTimers();
    assert.equal(heartbeatStatuses.at(-1), 'applied');
});

test('control API requests an immediate snapshot with a five-second transport timeout', async () => {
    let requestOptions = null;
    const { hooks } = loadHooks({
        GM: {
            xmlHttpRequest(options) {
                requestOptions = options;
                queueMicrotask(() => options.onload({ status: 200, response: { control: null } }));
                return { abort() {} };
            },
        },
    });
    const api = new hooks.Api();

    const result = await api.pollDesiredControl({
        workerId: 'worker-short-poll',
        sessionId: 'session-short-poll',
        sessionEpoch: 7,
    }, {
        epoch: 'epoch-short-poll',
        revision: 12,
    });

    assert.deepEqual(JSON.parse(JSON.stringify(result)), { control: null });
    assert.match(requestOptions.url, /timeoutMs=0/);
    assert.doesNotMatch(requestOptions.url, /timeoutMs=20000/);
    assert.equal(requestOptions.timeout, 5000);
});

test('control agent receives desired state through the lightweight short poll', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const received = [];
    agent.api.pollDesiredControl = async (identity, cursor) => {
        assert.equal(identity.workerId, agent.identity.workerId);
        assert.equal(cursor, null);
        return {
            control: { epoch: 'epoch-poll', revision: 12, operationId: 'operation-12', desiredState: 'paused' },
        };
    };
    agent.applyControl = (control) => received.push(control);

    assert.equal(await agent.pollControl(), 2000);
    assert.equal(received.length, 1);
    assert.equal(received[0].revision, 12);
    assert.equal(agent.controlPollCursor.revision, 12);
    assert.equal(agent.connectionState, 'connected');
});

test('control polling starts immediately, waits two seconds, and stays stopped', async () => {
    let now = 0;
    let nextTimerId = 1;
    const timers = new Map();
    const runDueTimers = () => {
        const due = [...timers.entries()]
            .filter(([, timer]) => timer.due <= now)
            .sort((left, right) => left[1].due - right[1].due);
        for (const [id, timer] of due) {
            timers.delete(id);
            timer.callback();
        }
    };
    const document = createFakeDocument();
    const { hooks } = loadHooks({
        document,
        URL,
        window: {
            setTimeout(callback, delay) {
                const id = nextTimerId++;
                timers.set(id, { callback, due: now + delay });
                return id;
            },
            clearTimeout(id) { timers.delete(id); },
        },
    });
    const agent = new hooks.ControlAgent();
    let polls = 0;
    agent.pollControl = async () => {
        polls += 1;
        return 2000;
    };

    agent.startControlPolling();
    await new Promise(setImmediate);
    assert.equal(polls, 1);
    assert.equal(timers.size, 1);

    now = 1999;
    runDueTimers();
    await new Promise(setImmediate);
    assert.equal(polls, 1);

    now = 2000;
    runDueTimers();
    await new Promise(setImmediate);
    assert.equal(polls, 2);

    agent.stop();
    now = 4000;
    runDueTimers();
    await new Promise(setImmediate);
    assert.equal(polls, 2);
});

test('stopped control preempts an in-flight transition and ignores its late result', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const calls = [];
    let finishRunning;
    agent.pulse = async () => undefined;
    agent.attachRunner({
        applyDesiredState(desiredState) {
            calls.push(desiredState);
            if (desiredState === 'running') {
                return new Promise((resolve) => { finishRunning = resolve; });
            }
            return Promise.resolve(desiredState);
        },
    });
    const control = (revision, desiredState) => ({
        epoch: 'epoch-preempt', revision, operationId: `operation-${revision}`, desiredState,
    });

    agent.applyControl(control(1, 'running'));
    await new Promise(setImmediate);
    agent.applyControl(control(2, 'stopped'));
    await new Promise(setImmediate);

    assert.deepEqual(calls, ['running', 'stopped']);
    assert.equal(agent.executionState, 'stopped');
    assert.equal(agent.controlAck.revision, 2);
    assert.equal(agent.controlAck.status, 'applied');

    finishRunning('running');
    await new Promise(setImmediate);
    assert.equal(agent.executionState, 'stopped');
    assert.equal(agent.controlAck.revision, 2);
});

test('stopped control cancels a queued running transition before runner side effects', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const calls = [];
    agent.pulse = async () => undefined;
    agent.attachRunner({
        applyDesiredState(desiredState) {
            calls.push(desiredState);
            return Promise.resolve(desiredState);
        },
    });

    agent.applyControl({ epoch: 'epoch-queued', revision: 1, operationId: 'op-1', desiredState: 'running' });
    agent.applyControl({ epoch: 'epoch-queued', revision: 2, operationId: 'op-2', desiredState: 'stopped' });
    await new Promise(setImmediate);

    assert.deepEqual(calls, ['stopped']);
    assert.equal(agent.executionState, 'stopped');
    assert.equal(agent.controlAck.revision, 2);
});

test('remote stop preempts a stale-session safety pause without a late paused state', async () => {
    const document = createFakeDocument();
    const { hooks } = loadHooks({ document, URL });
    const agent = new hooks.ControlAgent();
    const calls = [];
    let finishSafetyPause;
    agent.pulse = async () => undefined;
    agent.executionState = 'running';
    agent.attachRunner({
        applyDesiredState(desiredState, transition) {
            calls.push([desiredState, Boolean(transition.safety)]);
            if (transition.safety) {
                return new Promise((resolve) => { finishSafetyPause = resolve; });
            }
            return Promise.resolve(desiredState);
        },
    });

    agent.requestSafetyPause('stale_session');
    await new Promise(setImmediate);
    agent.applyControl({
        epoch: 'epoch-safety', revision: 3, operationId: 'operation-3', desiredState: 'stopped',
    });
    await new Promise(setImmediate);

    assert.deepEqual(calls, [['paused', true], ['stopped', false]]);
    assert.equal(agent.executionState, 'stopped');
    assert.equal(agent.controlAck.status, 'applied');

    finishSafetyPause('paused');
    await new Promise(setImmediate);
    assert.equal(agent.executionState, 'stopped');
    assert.equal(agent.controlAck.revision, 3);
});

test('automation lifecycle can restart without reloading the resident control agent', async () => {
    const { hooks } = loadHooks();
    const firstSignal = hooks.runtimeLifecycle.signal;
    await hooks.runtimeLifecycle.stop('test_stop');
    assert.equal(hooks.runtimeLifecycle.state, 'stopped');
    assert.equal(firstSignal.aborted, true);

    const cancelledRestart = new AbortController();
    const cancelledRestartPromise = hooks.runtimeLifecycle.restart(cancelledRestart.signal);
    cancelledRestart.abort();
    await assert.rejects(cancelledRestartPromise, /control_superseded/);
    assert.equal(hooks.runtimeLifecycle.state, 'stopped');

    await hooks.runtimeLifecycle.restart();
    assert.equal(hooks.runtimeLifecycle.state, 'running');
    assert.notEqual(hooks.runtimeLifecycle.signal, firstSignal);
    assert.equal(hooks.runtimeLifecycle.signal.aborted, false);
    assert.match(scriptSource, /await runtimeLifecycle\.restart\(transition\.signal\)/);
    assert.match(scriptSource, /await teardownExecutor\('initialization_failed'\)/);
    assert.doesNotMatch(scriptSource, /await startRound\(\);\s*started = true;/);
    assert.match(scriptSource, /updateTransitionState\('running', transition\);\s*scheduleStartRound\(transition\);\s*return 'running';/);
    assert.match(scriptSource, /const scheduleStartRound = \(transition = \{\}\) => \{[\s\S]*?queueMicrotask\(/);
    assert.doesNotMatch(scriptSource, /runtimeLifecycle\.isStopping\(\)[\s\S]{0,180}window\.location\.reload/);
});

test('session handoff survives the old page trailing heartbeat', async () => {
    const document = createFakeDocument();
    const sessionStorage = createStorage();
    const { hooks } = loadHooks({
        document,
        URL,
        sessionStorage,
        window: { location: { pathname: '/web/geek/job' } },
    });
    const agent = new hooks.ControlAgent();
    const control = {
        epoch: 'epoch-handoff',
        revision: 7,
        operationId: 'operation-handoff',
        desiredState: 'running',
    };
    assert.equal(agent.prepareSessionHandoff(control), true);
    assert.ok(sessionStorage.getItem('__goodjobs_session_handoff'));
    agent.receiveControl = () => {};
    agent.api.heartbeat = async () => ({ ok: true, heartbeatAccepted: true });

    await agent.pulse();

    assert.ok(
        sessionStorage.getItem('__goodjobs_session_handoff'),
        'the page that writes the handoff must not consume it on its trailing heartbeat',
    );
});

test('successor page consumes the session handoff on its first accepted heartbeat', async () => {
    const document = createFakeDocument();
    const sessionStorage = createStorage();
    const { hooks } = loadHooks({
        document,
        URL,
        sessionStorage,
        window: { location: { pathname: '/web/geek/job' } },
    });
    const writer = new hooks.ControlAgent();
    assert.equal(writer.prepareSessionHandoff({
        epoch: 'epoch-handoff',
        revision: 7,
        operationId: 'operation-handoff',
        desiredState: 'running',
    }), true);
    const successor = new hooks.ControlAgent();
    assert.ok(successor.sessionHandoff);
    successor.receiveControl = () => {};
    successor.api.heartbeat = async () => ({ ok: true, heartbeatAccepted: true });

    await successor.pulse();

    assert.equal(sessionStorage.getItem('__goodjobs_session_handoff'), null);
    assert.equal(successor.sessionHandoff, null);
});

test('successor does not send an expired handoff after a delayed heartbeat', async () => {
    let now = 1_000_000;
    class FakeDate extends Date {
        static now() { return now; }
    }
    const document = createFakeDocument();
    const sessionStorage = createStorage();
    const { hooks } = loadHooks({
        Date: FakeDate,
        document,
        URL,
        sessionStorage,
        window: { location: { pathname: '/web/geek/job' } },
    });
    const writer = new hooks.ControlAgent();
    assert.equal(writer.prepareSessionHandoff({
        epoch: 'epoch-handoff',
        revision: 7,
        operationId: 'operation-handoff',
        desiredState: 'running',
    }), true);
    const successor = new hooks.ControlAgent();
    now += 10001;
    successor.receiveControl = () => {};
    let heartbeatPayload = null;
    successor.api.heartbeat = async (payload) => {
        heartbeatPayload = payload;
        return { ok: true, heartbeatAccepted: true };
    };

    await successor.pulse();

    assert.equal(heartbeatPayload.controlHandoff, undefined);
    assert.equal(sessionStorage.getItem('__goodjobs_session_handoff'), null);
});

test('stopped transition cancels a pending search-page navigation', async () => {
    let navigationCount = 0;
    const transition = new AbortController();
    const { hooks } = loadHooks({
        window: {
            location: {
                pathname: '/web/geek/recommend',
                origin: 'https://www.zhipin.com',
                replace() { navigationCount += 1; },
            },
        },
    });
    const runner = new hooks.Zhipin();

    const result = await runner.applyDesiredState('running', {
        generation: 1,
        signal: transition.signal,
    });
    assert.equal(result.state, 'starting');
    transition.abort();
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(navigationCount, 0);
});

test('running navigation hands the active control to the next page session', async () => {
    let navigationCount = 0;
    const { hooks, localStorage } = loadHooks({
        window: {
            location: {
                pathname: '/web/geek/recommend',
                origin: 'https://www.zhipin.com',
                replace() { navigationCount += 1; },
            },
        },
    });
    const controlAgent = new hooks.ControlAgent();
    controlAgent.transitionGeneration = 1;
    const runner = new hooks.Zhipin(controlAgent);
    const control = {
        epoch: 'control-epoch',
        revision: 7,
        operationId: 'operation-7',
        desiredState: 'running',
    };

    const result = await runner.applyDesiredState('running', {
        generation: 1,
        signal: new AbortController().signal,
        control,
    });
    assert.equal(result.state, 'starting');
    await new Promise((resolve) => setTimeout(resolve, 300));
    assert.equal(navigationCount, 1);
    const handoff = JSON.parse(localStorage.getItem('__goodjobs_session_handoff'));
    assert.equal(handoff.desiredState, 'running');
    assert.equal(handoff.operationId, control.operationId);
    assert.equal(handoff.revision, control.revision);
    assert.equal(handoff.sessionId, controlAgent.sessionId);

    const nextAgent = new hooks.ControlAgent();
    let heartbeatPayload = null;
    nextAgent.api.heartbeat = async (payload) => {
        heartbeatPayload ??= payload;
        return { ok: true, heartbeatAccepted: true };
    };
    await nextAgent.pulse();
    assert.deepEqual(JSON.parse(JSON.stringify(heartbeatPayload.controlHandoff)), handoff);
    assert.equal(localStorage.getItem('__goodjobs_session_handoff'), null);
});

test('queued running control replaces an older pending navigation handoff', async () => {
    let navigationCount = 0;
    const { hooks, localStorage } = loadHooks({
        window: {
            location: {
                pathname: '/web/geek/recommend',
                origin: 'https://www.zhipin.com',
                replace() { navigationCount += 1; },
            },
        },
    });
    const agent = new hooks.ControlAgent();
    const runner = new hooks.Zhipin(agent);
    agent.attachRunner(runner);
    const control = (revision) => ({
        epoch: 'control-epoch',
        revision,
        operationId: `operation-${revision}`,
        desiredState: 'running',
    });

    agent.applyControl(control(7));
    agent.applyControl(control(8));
    await new Promise((resolve) => setTimeout(resolve, 350));

    assert.equal(navigationCount, 1);
    assert.equal(JSON.parse(localStorage.getItem('__goodjobs_session_handoff')).revision, 8);
});

test('a later running revision invalidates a scheduled older navigation', async () => {
    let navigationCount = 0;
    const { hooks, localStorage } = loadHooks({
        window: {
            location: {
                pathname: '/web/geek/recommend',
                origin: 'https://www.zhipin.com',
                replace() { navigationCount += 1; },
            },
        },
    });
    const agent = new hooks.ControlAgent();
    const runner = new hooks.Zhipin(agent);
    agent.attachRunner(runner);
    const control = (revision) => ({
        epoch: 'control-epoch', revision, operationId: `operation-${revision}`, desiredState: 'running',
    });

    agent.applyControl(control(7));
    await new Promise(setImmediate);
    agent.applyControl(control(8));
    await new Promise((resolve) => setTimeout(resolve, 350));

    assert.equal(navigationCount, 1);
    assert.equal(JSON.parse(localStorage.getItem('__goodjobs_session_handoff')).revision, 8);
});

test('managed child execution permission is persistent, expiring, and fail closed', () => {
    const { hooks } = loadHooks();
    assert.equal(hooks.childExecutionPermitted(), false);

    hooks.writeChildExecutionPermission('running');
    assert.equal(hooks.childExecutionPermitted(), true);
    assert.equal(hooks.childExecutionPermitted(Date.now() + 16000), false);

    hooks.writeChildExecutionPermission('stopped');
    assert.equal(hooks.childExecutionPermitted(), false);
    assert.match(scriptSource, /if \(childExecutionPermitted\(\)\) \{\s*new Zhipin\(\)\.run\(\)/);
    assert.match(scriptSource, /writeChildExecutionPermission\('stopped'\);\s*const controlAgent = new ControlAgent\(\)/);
});

test('runtime heartbeat and chat status use the V2 protocol', () => {
    const metadataVersion = scriptSource.match(/\/\/ @version\s+(\S+)/)?.[1];
    const runtimeVersion = scriptSource.match(/const SCRIPT_VERSION = '([^']+)'/)?.[1];
    assert.equal(metadataVersion, '2026-07-20-delivery-gate-v2.1');
    assert.equal(runtimeVersion, metadataVersion);
    assert.match(scriptSource, /const SCRIPT_API_VERSION = 2/);
    assert.match(scriptSource, /const CONTROL_PROTOCOL_VERSION = 2/);
    assert.match(scriptSource, /protocolVersion: CONTROL_PROTOCOL_VERSION/);
    assert.match(scriptSource, /controlAck: this\.controlAck/);
    assert.match(scriptSource, /\/api\/control\/workers\/\$\{encodeURIComponent\(identity\.workerId\)\}\/desired-state\?/);
    assert.match(scriptSource, /afterRevision=\$\{encodeURIComponent\(cursor\.revision\)\}/);
    assert.match(scriptSource, /timeoutMs=0/);
    assert.doesNotMatch(scriptSource, /timeoutMs=20000/);
    assert.match(scriptSource, /Authorization: `Bearer \$\{token\}`/);
    assert.match(scriptSource, /@connect\s+\*/);
    assert.match(scriptSource, /runtime\.logs\.push\(createRuntimeLogEntry\(message, \{/);
    assert.match(scriptSource, /if \(data\.message\) logger\.add\(data\.message, data\)/);
    assert.match(scriptSource, /logger\.add\(data, \{ sender: 'delivery' \}\)/);
    assert.match(scriptSource, /this\.bcTypes\.STATUS,\s*entry/);
    assert.match(scriptSource, /sender: 'claim', verbosity: 'concise'/);
    assert.match(scriptSource, /sender: 'queue', verbosity: 'detailed'/);
    assert.doesNotMatch(scriptSource, /退出并关闭 goodJobs|重启 goodJobs|data-goodjobs-(?:exit|restart)/);
    assert.doesNotMatch(scriptSource, /manualFilterWaitMs|手动选择地区、薪资/);
});

test('both search and chat decisions expose the backend scoring switch state', () => {
    const assignments = scriptSource.match(/scoringEnabled: decision\.scoringEnabled !== false/g) || [];
    assert.equal(assignments.length, 2);
});

test('job detail selector supports the visible BOSS online badge', () => {
    assert.match(scriptSource, /\.job-boss-info h2\.name \.boss-online-tag/);
    assert.match(scriptSource, /hrActiveLevel === 'online' \? '当前在线'/);
});

test('removed job cache userscript surface stays absent', () => {
    assert.doesNotMatch(scriptSource, /\/cache\/job|cacheControl|cacheMode|缓存系统设置/);
});

test('client configuration rejects V1 before any automation can start', () => {
    const { hooks } = loadHooks();
    assert.equal(typeof hooks.requireV2ClientConfig, 'function');
    assert.throws(
        () => hooks.requireV2ClientConfig({ scriptApiVersion: 1 }),
        /V2|版本|scriptApiVersion/,
    );
    assert.equal(hooks.requireV2ClientConfig({
        scriptApiVersion: 2,
        qualificationRequired: true,
    }).scriptApiVersion, 2);
    assert.match(scriptSource, /const clientConfig = requireV2ClientConfig\(await api\.getClientConfig\(\)\)/);
    assert.doesNotMatch(scriptSource, /获取统一配置失败，将回退旧接口|获取标签成功\(旧接口\)|获取自我介绍成功\(旧接口\)/);
});

test('Api.getJobScore sends the V2 request envelope', async () => {
    const { hooks } = loadHooks();
    const api = Object.create(hooks.Api.prototype);
    let request = null;
    api.__http = async (path, method, body) => {
        request = { path, method, body: JSON.parse(body) };
        return { success: true };
    };

    await api.getJobScore('SRE', '20K', 'Linux');

    assert.deepEqual(request, {
        path: '/get-job-score',
        method: 'POST',
        body: {
            scriptApiVersion: 2,
            job: '# 职位名称\nSRE\n\n# 薪资范围\n20K\n\n# 职位描述\nLinux',
        },
    });
});

test('startup preflight sends worker identity and fails closed on transport errors', async () => {
    const { hooks } = loadHooks();
    const api = Object.create(hooks.Api.prototype);
    let request = null;
    api.__http = async (path, method, body) => {
        request = { path, method, body: JSON.parse(body) };
        return { success: true };
    };
    await api.checkDailyLimit('account-a', 'worker-a');
    assert.deepEqual(request, {
        path: '/check-daily-limit',
        method: 'POST',
        body: { accountId: 'account-a', workerId: 'worker-a' },
    });
    assert.match(scriptSource, /api\.checkDailyLimit\(identity\.accountId,\s*identity\.workerId\)/);
    assert.match(scriptSource, /每日限制检查失败[\s\S]*?this\.pause\s*=\s*true[\s\S]*?runtime\.state\s*=\s*'paused'[\s\S]*?started\s*=\s*true[\s\S]*?return/);
});

test('runtime policy trusts server safety and keeps scan AI fail closed', () => {
    const { hooks } = loadHooks();
    hooks.OPTIONS.scanAiEnabled = true;
    const policy = hooks.applyRuntimePolicy({
        safety: {
            scanOnly: true,
            scanAiEnabled: false,
            sendingDisabled: true,
            openingDisabled: true,
            resumeSendingDisabled: true,
        },
        plan: { dailyTarget: 12, hourlyLimit: 4, minDelayMs: 300, maxDelayMs: 900 },
        account: { accountId: 'account-a', dailyLimit: 20 },
        policy: { aiFilterEnabled: true },
    });

    assert.equal(policy.scanOnly, true);
    assert.equal(policy.scanAiEnabled, false);
    assert.equal(policy.sendingDisabled, true);
    assert.equal(policy.openingDisabled, true);
    assert.equal(policy.resumeSendingDisabled, true);
    assert.equal(policy.plan.dailyTarget, 12);
    assert.equal(policy.account.accountId, 'account-a');
    assert.equal(policy.policy.aiFilterEnabled, true);
});

test('server plan delay range overrides local rhythm only while the range is enabled', async () => {
    const { hooks } = loadHooks();
    const waits = [];
    hooks.tools.asyncSleep = async (delay) => { waits.push(delay); };
    hooks.OPTIONS.antiDetectionEnabled = true;
    hooks.OPTIONS.randomDelayMinMs = 9000;
    hooks.OPTIONS.randomDelayMaxMs = 9000;

    hooks.applyRuntimePolicy({
        safety: {},
        plan: { minDelayMs: 25, maxDelayMs: 25 },
        account: {},
        policy: {},
    });
    assert.equal(await hooks.antiDetection.delay(), true);
    assert.deepEqual(waits, [25]);

    waits.length = 0;
    hooks.applyRuntimePolicy({ safety: {}, plan: { minDelayMs: 0, maxDelayMs: 0 } });
    assert.equal(await hooks.antiDetection.delay(), true);
    assert.equal(waits.reduce((total, delay) => total + delay, 0), 9000);
    assert.equal(waits.every((delay) => delay > 0 && delay <= 200), true);
});

test('openingDisabled blocks detail waits and rechecks before every DOM or window open', async () => {
    const { hooks } = loadHooks();
    let delayCalls = 0;
    hooks.antiDetection.detailDelay = async () => {
        delayCalls += 1;
        return true;
    };

    hooks.applyRuntimePolicy({ safety: { openingDisabled: true } });
    const blockedImmediately = await hooks.deliveryFlow.beforeDetailOpen();
    assert.equal(blockedImmediately.allowed, false);
    assert.equal(blockedImmediately.reason, 'opening_disabled');
    assert.equal(delayCalls, 0);

    hooks.applyRuntimePolicy({ safety: { openingDisabled: false } });
    hooks.antiDetection.detailDelay = async () => {
        delayCalls += 1;
        hooks.applyRuntimePolicy({ safety: { openingDisabled: true } });
        return true;
    };
    const blockedAfterDelay = await hooks.deliveryFlow.beforeDetailOpen();
    assert.equal(blockedAfterDelay.allowed, false);
    assert.equal(blockedAfterDelay.reason, 'opening_disabled');

    assert.match(scriptSource, /const getJobInfo = async \(href\) => \{[\s\S]*?beforeDetailOpen\([\s\S]*?openTabNSetTimestamp\(href/);
    assert.match(scriptSource, /if \(!chatInfo\.talked\) \{[\s\S]*?beforeDetailOpen\([\s\S]*?chatInfo\.jobEl\.click\(\)/);
    assert.match(scriptSource, /if \(!chatInfo\.resumeSended\) \{[\s\S]*?beforeDetailOpen\([\s\S]*?chatInfo\.jobEl\.click\(\)/);
});

test('normal delivery claims before starting AI and scan mode never claims', async () => {
    const { hooks } = loadHooks();
    hooks.tools.asyncSleep = async () => {};
    const calls = [];
    const api = {
        qualifyDelivery: async (_identity, _job, mode) => {
            calls.push(`qualify:${mode}`);
            return {
                success: true,
                allowed: true,
                qualificationToken: 'qualification-token',
                resumeIndex: 2,
                score: 83,
                aiFilterEnabled: true,
            };
        },
        claimDelivery: async (_identity, _job, token) => {
            calls.push(`claim:${token}`);
            return { success: true, allowed: true, accepted: true, claimToken: 'claim-token' };
        },
        startJobFilter: async (payload) => {
            assert.equal(payload.score, 83);
            calls.push(`ai:${payload.mode}:${payload.claimToken || 'no-claim'}`);
            return { success: true, allowed: true, status: 'completed', passed: true, reliable: true };
        },
        releaseDelivery: async () => ({ success: true }),
    };
    const identity = { accountId: 'account-a', workerId: 'worker-a' };
    const job = { company: 'Acme', title: 'Engineer', detail: 'Build systems' };

    const delivery = await hooks.deliveryFlow.prepare(api, identity, job, {
        mode: 'delivery',
        scanAiEnabled: true,
    });
    assert.equal(delivery.ready, true);
    assert.equal(delivery.claimToken, 'claim-token');
    assert.deepEqual(calls, [
        'qualify:delivery',
        'claim:qualification-token',
        'ai:delivery:claim-token',
    ]);

    calls.length = 0;
    const scan = await hooks.deliveryFlow.prepare(api, identity, job, {
        mode: 'scan',
        scanAiEnabled: true,
    });
    assert.equal(scan.ready, true);
    assert.equal(scan.claimToken, '');
    assert.deepEqual(calls, [
        'qualify:scan',
        'ai:scan:no-claim',
    ]);
});

test('claim retries transport loss idempotently but never retries a business rejection', async () => {
    const { hooks } = loadHooks();
    hooks.tools.asyncSleep = async () => {};
    let transportAttempts = 0;
    const base = {
        qualifyDelivery: async () => ({
            success: true, allowed: true, qualificationToken: 'q', aiFilterEnabled: false,
        }),
        renewDelivery: async () => ({ success: true }),
        releaseDelivery: async () => ({ success: true }),
    };
    const recovered = await hooks.deliveryFlow.prepare({
        ...base,
        claimDelivery: async () => {
            transportAttempts += 1;
            if (transportAttempts === 1) throw new Error('claim response lost');
            return { success: true, allowed: true, accepted: true, claimToken: 'claim-token' };
        },
    }, { accountId: 'account-a', workerId: 'worker-a' }, {
        company: 'Acme', title: 'Engineer', detail: 'Build systems',
    });
    assert.equal(recovered.ready, true);
    assert.equal(transportAttempts, 2);
    recovered.leaseKeeper.stop();

    let businessAttempts = 0;
    const rejected = await hooks.deliveryFlow.prepare({
        ...base,
        claimDelivery: async () => {
            businessAttempts += 1;
            return { success: false, allowed: false, accepted: false, reason: 'daily_limit' };
        },
    }, { accountId: 'account-a', workerId: 'worker-a' }, {
        company: 'Other', title: 'Engineer', detail: 'Build systems',
    });
    assert.equal(rejected.ready, false);
    assert.equal(rejected.reason, 'daily_limit');
    assert.equal(businessAttempts, 1);

    const unavailable = await hooks.deliveryFlow.prepare({
        ...base,
        claimDelivery: async () => { throw new Error('network offline'); },
    }, { accountId: 'account-a', workerId: 'worker-a' }, {
        company: 'Offline', title: 'Engineer', detail: 'Build systems',
    });
    assert.equal(unavailable.ready, false);
    assert.equal(unavailable.stage, 'claim');
    assert.equal(unavailable.reason, 'claim_transport_failed');
});

test('qualify rejection short-circuits claim and every AI call', async () => {
    const { hooks } = loadHooks();
    let downstreamCalls = 0;
    const result = await hooks.deliveryFlow.prepare({
        qualifyDelivery: async () => ({ success: false, allowed: false, reason: 'duplicate_job' }),
        claimDelivery: async () => { downstreamCalls += 1; },
        startJobFilter: async () => { downstreamCalls += 1; },
    }, { accountId: 'account-a', workerId: 'worker-a' }, {
        company: 'Acme', title: 'Engineer', detail: 'Build systems',
    }, { mode: 'delivery', scanAiEnabled: true });

    assert.equal(result.ready, false);
    assert.equal(result.reason, 'duplicate_job');
    assert.equal(downstreamCalls, 0);
});

test('pre-AI gate rejections keep their own stage, state, and audit action', async () => {
    const { hooks } = loadHooks();
    const cases = [
        ['duplicate_job', 'duplicate', 'duplicate', 'company_duplicate_skipped'],
        ['score_below_threshold', 'rules', 'below_threshold', 'job_below_threshold'],
        ['hr_inactive', 'hr', 'hr_filtered', 'job_hr_filtered'],
        ['daily_limit', 'quota', 'quota_rejected', 'job_quota_rejected'],
        ['sending_disabled', 'policy', 'policy_blocked', 'job_policy_blocked'],
        ['missing_company', 'fields', 'invalid_fields', 'job_invalid_fields'],
    ];

    for (const [reason, stage, decisionState, action] of cases) {
        let aiCalls = 0;
        const prepared = await hooks.deliveryFlow.prepare({
            qualifyDelivery: async () => ({ success: false, allowed: false, reason }),
            claimDelivery: async () => { throw new Error('claim must not run'); },
            startJobFilter: async () => { aiCalls += 1; },
        }, { accountId: 'account-a', workerId: 'worker-a' }, {
            company: 'Acme', title: 'Engineer', detail: 'Build systems',
        }, { mode: 'delivery', scanAiEnabled: true });
        const rejection = hooks.classifyDeliveryRejection(prepared);
        assert.equal(prepared.stage, stage, reason);
        assert.equal(rejection.decisionState, decisionState, reason);
        assert.equal(rejection.action, action, reason);
        assert.equal(aiCalls, 0, reason);
    }
});

test('reserved claim lease renews through post-claim work and stops once queued', async () => {
    const { hooks } = loadHooks();
    const scheduled = [];
    const cleared = [];
    const calls = [];
    const scheduler = {
        setInterval(callback, delay) {
            const handle = { callback, delay };
            scheduled.push(handle);
            return handle;
        },
        clearInterval(handle) {
            cleared.push(handle);
        },
    };
    const api = {
        qualifyDelivery: async () => ({
            success: true, allowed: true, qualificationToken: 'q', aiFilterEnabled: false,
        }),
        claimDelivery: async () => ({
            success: true, allowed: true, accepted: true, claimToken: 'claim-token',
        }),
        renewDelivery: async (token, workerId) => {
            calls.push(`renew:${token}:${workerId}`);
            return { success: true };
        },
        markDelivery: async (_token, status) => {
            calls.push(`mark:${status}`);
            return { success: true };
        },
    };

    const prepared = await hooks.deliveryFlow.prepare(api, {
        accountId: 'account-a', workerId: 'worker-a',
    }, {
        company: 'Acme', title: 'Engineer', detail: 'Build systems',
    }, {
        mode: 'delivery', leaseScheduler: scheduler, leaseIntervalMs: 10,
    });
    assert.equal(prepared.ready, true);
    assert.equal(typeof prepared.leaseKeeper?.assertHealthy, 'function');
    assert.equal(scheduled.length, 1);

    await scheduled[0].callback();
    assert.deepEqual(calls, ['renew:claim-token:worker-a']);
    await hooks.deliveryEntrypoints.search(api, 'claim-token', async () => {
        calls.push('platform-effect');
    }, { leaseKeeper: prepared.leaseKeeper });
    assert.deepEqual(calls, [
        'renew:claim-token:worker-a',
        'renew:claim-token:worker-a',
        'mark:queued',
        'platform-effect',
    ]);
    assert.deepEqual(cleared, [scheduled[0]]);
});

test('search and chat register claim cleanup ownership before post-prepare awaits', () => {
    const prepareNeedle = 'const prepared = await deliveryFlow.prepare(api, identity';
    const searchPrepare = scriptSource.indexOf(prepareNeedle);
    const searchReject = scriptSource.indexOf('if (!prepared.ready)', searchPrepare);
    const searchSlice = scriptSource.slice(searchPrepare, searchReject);
    assert.ok(searchPrepare >= 0 && searchReject > searchPrepare);
    assert.match(searchSlice, /activeReservedClaimToken\s*=\s*prepared\.claimToken/);
    assert.match(searchSlice, /activeClaimLeaseKeeper\s*=\s*prepared\.leaseKeeper/);
    assert.ok(searchSlice.indexOf('activeReservedClaimToken') < searchSlice.indexOf('await logAction'));

    const chatPrepare = scriptSource.indexOf(prepareNeedle, searchReject);
    const chatReject = scriptSource.indexOf('if (!prepared.ready)', chatPrepare);
    const chatSlice = scriptSource.slice(chatPrepare, chatReject);
    assert.ok(chatPrepare >= 0 && chatReject > chatPrepare);
    assert.match(chatSlice, /activeChatClaimToken\s*=\s*prepared\.claimToken/);
    assert.match(chatSlice, /activeChatLeaseKeeper\s*=\s*prepared\.leaseKeeper/);
    assert.ok(chatSlice.indexOf('activeChatClaimToken') < chatSlice.indexOf('await logAction'));
});

test('lease renewal failure blocks queued and all platform effects', async () => {
    const { hooks } = loadHooks();
    let scheduledCallback;
    let marks = 0;
    let effects = 0;
    const api = {
        qualifyDelivery: async () => ({
            success: true, allowed: true, qualificationToken: 'q', aiFilterEnabled: false,
        }),
        claimDelivery: async () => ({
            success: true, allowed: true, accepted: true, claimToken: 'claim-token',
        }),
        renewDelivery: async () => ({ success: false, reason: 'claim_expired' }),
        markDelivery: async () => { marks += 1; return { success: true }; },
    };
    const prepared = await hooks.deliveryFlow.prepare(api, {
        accountId: 'account-a', workerId: 'worker-a',
    }, {
        company: 'Acme', title: 'Engineer', detail: 'Build systems',
    }, {
        mode: 'delivery',
        leaseScheduler: {
            setInterval(callback) { scheduledCallback = callback; return 1; },
            clearInterval() {},
        },
        leaseIntervalMs: 10,
    });
    await scheduledCallback();

    await assert.rejects(
        hooks.deliveryEntrypoints.search(api, 'claim-token', async () => { effects += 1; }, {
            leaseKeeper: prepared.leaseKeeper,
        }),
        /claim_expired|lease|续租/,
    );
    assert.equal(marks, 0);
    assert.equal(effects, 0);
});

test('AI rejection and transport failure release a normal reserved claim', async () => {
    const { hooks } = loadHooks();
    const released = [];
    const baseApi = {
        qualifyDelivery: async () => ({
            success: true, allowed: true, qualificationToken: 'q', aiFilterEnabled: true,
        }),
        claimDelivery: async () => ({
            success: true, allowed: true, accepted: true, claimToken: 'c',
        }),
        releaseDelivery: async (token, reason) => {
            released.push([token, reason]);
            return { success: true };
        },
    };
    const args = [
        { accountId: 'account-a', workerId: 'worker-a' },
        { company: 'Acme', title: 'Engineer', detail: 'Build systems' },
        { mode: 'delivery', scanAiEnabled: true },
    ];
    const rejected = await hooks.deliveryFlow.prepare({
        ...baseApi,
        startJobFilter: async () => ({
            success: true, status: 'completed', allowed: false, passed: false, reliable: true, reason: 'ai_rejected',
        }),
    }, ...args);
    assert.equal(rejected.ready, false);
    assert.equal(released.length, 1);

    const failed = await hooks.deliveryFlow.prepare({
        ...baseApi,
        startJobFilter: async () => { throw new Error('transport failed'); },
    }, ...args);
    assert.equal(failed.ready, false);
    assert.equal(released.length, 2);
    assert.match(released[1][1], /ai|filter|transport/);
});

test('scan AI is allowed only by the server safety policy', async () => {
    const { hooks } = loadHooks();
    let aiCalls = 0;
    const api = {
        qualifyDelivery: async () => ({
            success: true, allowed: true, qualificationToken: 'q', aiFilterEnabled: true,
        }),
        startJobFilter: async (payload) => {
            aiCalls += 1;
            assert.equal(payload.scanAiEnabled, true);
            assert.equal(payload.claimToken, undefined);
            return { success: true, status: 'completed', allowed: true, passed: true, reliable: true };
        },
    };
    const identity = { accountId: 'account-a', workerId: 'worker-a' };
    const job = { company: 'Acme', title: 'Engineer', detail: 'Build systems' };

    hooks.applyRuntimePolicy({ safety: { scanOnly: true, scanAiEnabled: false } });
    const skipped = await hooks.deliveryFlow.prepare(api, identity, job, {
        mode: 'scan', scanAiEnabled: hooks.runtimePolicy.scanAiEnabled,
    });
    assert.equal(skipped.aiSkipped, true);
    assert.equal(aiCalls, 0);

    hooks.applyRuntimePolicy({ safety: { scanOnly: true, scanAiEnabled: true } });
    await hooks.deliveryFlow.prepare(api, identity, job, {
        mode: 'scan', scanAiEnabled: hooks.runtimePolicy.scanAiEnabled,
    });
    assert.equal(aiCalls, 1);
});

test('queued blocks platform effects on business failure and final marks replay idempotently', async () => {
    const { hooks, localStorage } = loadHooks();
    hooks.tools.asyncSleep = async () => {};
    let businessAttempts = 0;
    await assert.rejects(
        hooks.deliveryFlow.markQueued({
            markDelivery: async () => {
                businessAttempts += 1;
                return { success: false, reason: 'invalid_transition' };
            },
        }, 'claim-token'),
        /queued|落库|invalid_transition/,
    );
    assert.equal(businessAttempts, 1, 'business rejection must not be retried');

    let transportAttempts = 0;
    const queued = await hooks.deliveryFlow.markQueued({
        markDelivery: async () => {
            transportAttempts += 1;
            if (transportAttempts === 1) throw new Error('response lost');
            return { success: true, idempotent: true };
        },
    }, 'claim-token');
    assert.equal(queued.success, true);
    assert.equal(transportAttempts, 2, 'a lost queued response must retry idempotently');

    let attempts = 0;
    const failed = await hooks.deliveryFlow.markFinal({
        markDelivery: async () => {
            attempts += 1;
            return { success: false, reason: 'database_busy' };
        },
    }, 'claim-token', 'sent');
    assert.equal(attempts, 3);
    assert.equal(failed.pendingReplay, true);
    assert.equal(hooks.readPendingFinalMarks().some((item) => item.claimToken === 'claim-token'), true);

    let replayCalls = 0;
    const replay = await hooks.deliveryFlow.replayPendingMarks({
        markDelivery: async () => {
            replayCalls += 1;
            return { success: true };
        },
    });
    assert.equal(replayCalls, 1);
    assert.equal(replay.remaining, 0);
    assert.equal(hooks.readPendingFinalMarks().length, 0);
    assert.match(scriptSource, /replayPendingMarks\(this\.api\)/);
});

test('final mark replay cannot erase a mark enqueued concurrently by another tab', async () => {
    const sharedStorage = createStorage();
    const first = loadHooks({ localStorage: sharedStorage }).hooks;
    const second = loadHooks({ localStorage: sharedStorage }).hooks;
    await first.deliveryFlow.markFinal({
        markDelivery: async () => ({ success: false, reason: 'offline' }),
    }, 'claim-a', 'sent');

    const replay = await first.deliveryFlow.replayPendingMarks({
        markDelivery: async () => {
            await second.deliveryFlow.markFinal({
                markDelivery: async () => ({ success: false, reason: 'offline' }),
            }, 'claim-b', 'failed_unknown', 'platform uncertain');
            return { success: true };
        },
    });

    assert.equal(replay.replayed, 1);
    assert.deepEqual(
        Array.from(second.readPendingFinalMarks(), (item) => [item.claimToken, item.status]),
        [['claim-b', 'failed_unknown']],
    );
});

test('all greetings stay non-empty and random empty greeting configuration is removed', async () => {
    const { hooks } = loadHooks();
    const greeting = await hooks.deliveryFlow.resolveGreeting({
        generateIntroduce: async () => { throw new Error('LLM unavailable'); },
    }, 'claim-token', { company: 'Acme', title: 'Engineer', detail: 'Build systems' }, '   ', true);
    assert.ok(greeting.introduce.trim().length > 0);
    assert.equal(greeting.mode, 'fixed');
    assert.doesNotMatch(scriptSource, /randomNoIntroduceRatio|shouldSkipIntroduce|omitIntroduce|随机空招呼|随机省略招呼|随机策略省略招呼语文本|空手打招呼/);
    assert.match(scriptSource, /const introduce = String\(greetDecision\.introduce\s*\|\|\s*DEFAULT_FIXED_INTRODUCE\)\.trim\(\)\s*\|\|\s*DEFAULT_FIXED_INTRODUCE;[\s\S]*?await sendMsg\(introduce,/);
});

test('search chat first-contact and auto-resume use the V2 qualification orchestrator', () => {
    assert.doesNotMatch(scriptSource, /await api\.getJobScore\(/);
    assert.match(scriptSource, /scene:\s*'search'/);
    assert.match(scriptSource, /scene:\s*'chat_first_contact'/);
    assert.match(scriptSource, /scene:\s*'chat_auto_resume'/);
    assert.match(scriptSource, /deliveryFlow\.prepare\(/);
    assert.match(scriptSource, /deliveryFlow\.markQueued\(/);
    assert.match(scriptSource, /deliveryEntrypoints\.search\(/);
    assert.match(scriptSource, /deliveryEntrypoints\.chatFirstContact\(/);
    assert.match(scriptSource, /deliveryEntrypoints\.chatAutoResume\(/);
    assert.match(scriptSource, /resumeIndex:\s*OPTIONS\.resumeIndex|sendResume\(OPTIONS\.resumeIndex\)/);
});

test('each executable entrypoint blocks its platform effect until queued succeeds', async () => {
    const { hooks } = loadHooks();
    const entryNames = ['search', 'chatFirstContact', 'chatAutoResume'];
    for (const name of entryNames) {
        assert.equal(typeof hooks.deliveryEntrypoints?.[name], 'function', `${name} entrypoint missing`);
        const calls = [];
        const api = {
            markDelivery: async (_token, status) => {
                calls.push(`mark:${status}`);
                return { success: true };
            },
        };
        const result = await hooks.deliveryEntrypoints[name](api, 'claim-token', async () => {
            calls.push('platform-effect');
            return { sent: true };
        }, { finalize: true });
        assert.equal(result.success, true);
        assert.deepEqual(calls, ['mark:queued', 'platform-effect', 'mark:sent']);

        let effects = 0;
        await assert.rejects(
            hooks.deliveryEntrypoints[name]({
                markDelivery: async () => ({ success: false, reason: 'queue_rejected' }),
            }, 'claim-token', async () => { effects += 1; }),
            /queue_rejected|queued/,
        );
        assert.equal(effects, 0, `${name} must not run its platform effect after queued rejection`);
    }
});

test('all platform entrypoints and peer heartbeats share consecutive-failure health', () => {
    assert.match(scriptSource, /deliveryEntrypoints\.search\([\s\S]*?\{\s*finalize:\s*false,\s*health:\s*runtime,\s*leaseKeeper:/);
    assert.match(scriptSource, /deliveryEntrypoints\.chatFirstContact\([\s\S]*?\{\s*finalize:\s*true,\s*health:\s*deliveryHealth,\s*leaseKeeper:/);
    assert.match(scriptSource, /deliveryEntrypoints\.chatAutoResume\([\s\S]*?\{\s*finalize:\s*true,\s*health:\s*deliveryHealth,\s*leaseKeeper:/);
    assert.match(scriptSource, /runPeerHeartbeat\([\s\S]*?consecutiveFailures:\s*deliveryHealth\.consecutiveFailures[\s\S]*?lastError:\s*deliveryHealth\.lastError/);
    assert.match(scriptSource, /healthUpdatedAt:\s*deliveryHealth\.healthUpdatedAt/);
    assert.match(scriptSource, /from\s*===\s*this\.targets\.chat[\s\S]*?syncDeliveryHealth\(runtime,\s*data\)/);
    assert.match(scriptSource, /syncDeliveryHealth\(deliveryHealth,\s*response\)/);
    assert.match(scriptSource, /recordDeliveryOutcome\(runtime,\s*true\)[\s\S]*?action:\s*'greet_sent'/);
    assert.match(scriptSource, /recordDeliveryOutcome\(runtime,\s*false,\s*data\.reason\s*\|\|\s*'chat_greet_failed'\)/);
    assert.match(scriptSource, /recordDeliveryOutcome\(runtime,\s*false,\s*'greet_timeout'\)/);
    assert.match(scriptSource, /recordDeliveryOutcome\(runtime,\s*false,\s*err\)/);
});

test('platform failures increment consecutive failures and a sent effect resets them', async () => {
    const { hooks } = loadHooks();
    assert.equal(typeof hooks.recordDeliveryOutcome, 'function');
    assert.equal(typeof hooks.syncDeliveryHealth, 'function');
    assert.equal(hooks.deliveryHealth.consecutiveFailures, 0);
    assert.equal(hooks.deliveryHealth.lastError, '');
    const health = { consecutiveFailures: 0, lastError: '' };
    hooks.recordDeliveryOutcome(health, false, 'first failure', 100);
    hooks.recordDeliveryOutcome(health, false, 'second failure', 100);
    assert.equal(health.consecutiveFailures, 2);
    assert.equal(health.lastError, 'second failure');
    assert.equal(health.healthUpdatedAt, 101);
    hooks.recordDeliveryOutcome(health, true, '', 102);
    assert.equal(health.consecutiveFailures, 0);
    assert.equal(health.lastError, '');
    hooks.syncDeliveryHealth(health, {
        consecutiveFailures: 3,
        lastError: 'stale child failure',
        healthUpdatedAt: 101,
    });
    assert.equal(health.consecutiveFailures, 0);
    assert.equal(health.lastError, '');
    hooks.syncDeliveryHealth(health, {
        consecutiveFailures: 3,
        lastError: 'new child failure',
        healthUpdatedAt: 103,
    });
    assert.equal(health.consecutiveFailures, 3);
    assert.equal(health.lastError, 'new child failure');
    assert.equal(health.healthUpdatedAt, 103);
    hooks.syncDeliveryHealth(health, {
        consecutiveFailures: 0,
        lastError: 'stale error',
        healthUpdatedAt: 104,
    });
    assert.equal(health.consecutiveFailures, 0);
    assert.equal(health.lastError, '');

    await assert.rejects(hooks.deliveryEntrypoints.chatFirstContact({
        markDelivery: async (_token, status) => ({ success: status === 'queued' }),
    }, 'claim-token', async () => { throw new Error('platform failed'); }, { finalize: true, health }));
    assert.equal(health.consecutiveFailures, 1);

    await hooks.deliveryEntrypoints.chatFirstContact({
        markDelivery: async () => ({ success: true }),
    }, 'claim-token', async () => ({ sent: true }), { finalize: true, health });
    assert.equal(health.consecutiveFailures, 0);
});

test('detail DOM readiness waits for every pre-AI field and fails closed on timeout', async () => {
    const { hooks } = loadHooks();
    let now = 0;
    let reads = 0;
    const ready = await hooks.waitForJobDetailReady(
        () => {
            reads += 1;
            return reads < 3
                ? { title: 'Engineer', company: 'Acme', detail: '', chatUrl: '', addUrl: '' }
                : { title: 'Engineer', company: 'Acme', detail: 'Build', chatUrl: '/chat', addUrl: '/add' };
        },
        100,
        () => now,
        async (delay) => { now += delay; },
    );
    assert.equal(ready.skip, undefined);
    assert.equal(reads, 3);

    now = 0;
    const timedOut = await hooks.waitForJobDetailReady(
        () => ({ title: 'Engineer', company: '', detail: '', chatUrl: '', addUrl: '' }),
        40,
        () => now,
        async (delay) => { now += delay; },
    );
    assert.equal(timedOut.skip, true);
    assert.match(timedOut.skipReason, /超时|缺少/);
});

test('userscript does not own or invoke automatic dashboard opening', () => {
    const { hooks } = loadHooks();
    assert.doesNotMatch(scriptSource, /@grant\s+GM_openInTab/);
    assert.doesNotMatch(scriptSource, /\bGM_openInTab\b/);
    assert.doesNotMatch(scriptSource, /\b(?:dashboardUrl|openDashboard)\b/);
    assert.doesNotMatch(scriptSource, /window\.open\([^)]*\/dashboard/);
    assert.equal(Object.hasOwn(hooks, 'dashboardUrl'), false);
    assert.equal(Object.hasOwn(hooks, 'openDashboard'), false);
});
