const state = {
    payload: null,
    records: [],
    range: 'today',
    trendRange: 'all',
    trendStart: '',
    trendEnd: '',
    trendView: { zoom: 1, offset: 0, dragging: false, startX: 0, startOffset: 0, visibleCount: 0, autoWindow: true },
    search: '',
    status: 'all',
    city: 'all',
    experience: 'all',
    education: 'all',
    minSalary: null,
    maxSalary: null,
    salaryBucket: '',
    keyword: '',
    mapProvince: '',
    mapCity: '',
    mapLevel: 'province',
    mapView: { scale: 1, x: 0, y: 0, suppressClick: false },
    mapHeatBreaks: [],
    mapRenderVersion: 0,
    mapLabelTier: 0,
    industry: '',
    role: '',
    exactDate: '',
    page: 1,
    pageSize: 10,
    sort: { key: 'loggedAt', direction: 'desc' },
    selectedIds: new Set(),
    visibleColumns: new Set(['company', 'salary', 'city', 'industry', 'experience', 'education', 'hrActive', 'loggedAt', 'score', 'status']),
    columnOrder: ['company', 'salary', 'city', 'industry', 'experience', 'education', 'hrActive', 'loggedAt', 'score', 'status'],
    columnWidths: {},
    density: 'default',
    chinaGeo: null,
    cityGeo: null,
    cityNameIndex: null,
    runtime: null,
    runtimeCursor: 0,
    liveEvents: [],
    logsPaused: false,
    logsExpanded: false,
    logAccount: 'all',
    logSender: 'all',
    logVerbosity: 'normal',
    logClearedCursor: 0,
    adminConfig: null,
    prompts: [],
    currentPrompt: '',
    currentResume: '',
    control: null,
    controlOnline: false,
    lifecycleRequests: {},
    llm: null,
    llmDirty: false,
    configDirty: false,
};

const COLORS = ['var(--cyan)', 'var(--violet)', 'var(--orange)', 'var(--green)', 'var(--red)', 'var(--blue)'];
const STATUS_LABELS = { sent: '已投递', queued: '进行中', reserved: '待发送', duplicate: '重复投递', failed_unknown: '异常' };
const HR_ACTIVE_LABELS = { online: '当前在线', just_now: '刚刚活跃', today: '今日活跃', within_3_days: '3 日内活跃', this_week: '本周活跃', this_month: '本月活跃', unknown: '未知' };
const DECISION_STATE_LABELS = { evaluating: '正在评估', hr_filtered: 'HR 活跃未达标', below_threshold: '低于投递阈值', ai_rejected: 'AI 未通过', random_skipped: '随机跳过', claiming: '正在领取投递权', queued: '等待投递', sent: '已投递', failed: '处理失败' };
const GREETING_MODE_LABELS = { none: '仅立即沟通', fixed: '固定招呼语', llm: 'AI 招呼语' };
const LOG_SENDER_LABELS = { system: '系统', delivery: '投递', claim: '领取投递', queue: '投递等待' };
const LOG_VERBOSITY_RANK = { concise: 0, normal: 1, detailed: 2 };
const DESIRED_STATE_LABELS = { running: '运行', paused: '暂停', stopped: '结束' };
const EXECUTION_STATE_LABELS = { starting: '启动中', running: '运行中', pausing: '暂停中', paused: '已暂停', stopping: '结束中', stopped: '已结束', error: '异常' };
const SYNC_STATE_LABELS = { pending: '等待同步', applying: '正在应用', synced: '已同步', failed: '同步失败' };
const ACCOUNT_DAILY_LIMIT_MIN = 0;
const ACCOUNT_DAILY_LIMIT_MAX = 150;
const CONTROL_DELIVERY_POLL_INTERVAL_MS = 150;
const CONTROL_DELIVERY_TIMEOUT_MS = 6000;
const TODAY_TARGET_FALLBACK = 20;
const GEOMETRY_PATH_CACHE = new WeakMap();
const MAP_MAX_SCALE = 20;
const CITY_DETAIL_ENTER_SCALE = 2.7;
const CITY_DETAIL_EXIT_SCALE = 2.3;

function dailyTarget() {
    const quotas = state.control?.quotas;
    if (quotas && typeof quotas === 'object' && !Array.isArray(quotas)) {
        const total = Object.values(quotas).reduce((sum, quota) => sum + Number(quota?.limit || 0), 0);
        if (total > 0) return total;
    }
    const backendLimit = Number(state.adminConfig?.backend?.daily_greet_limit);
    if (Number.isFinite(backendLimit) && backendLimit > 0) return backendLimit;
    return TODAY_TARGET_FALLBACK;
}

function renderDailyGoal(today = state.todayDelivered || 0) {
    state.todayDelivered = today;
    const target = dailyTarget();
    const goal = Math.min(100, Math.round(today / target * 100));
    $('#todayHint').textContent = `今日目标完成度 ${goal}%`;
    $('#goalText').textContent = `${today} / ${target}`;
    $('#goalBar').style.width = `${goal}%`;
    $('#goalHint').textContent = today >= target ? '今日目标已完成，注意及时复盘' : `还差 ${Math.max(0, target - today)} 份完成今日目标`;
}
const REPORT_LAYOUT_KEY = 'goodjobs.dashboard.report-layout.v1';
const TABLE_PREFS_KEY = 'goodjobs.dashboard.table-prefs.v1';
const AUTH_TOKEN_KEY = 'goodjobs.dashboard.shared-token';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const numberFormat = new Intl.NumberFormat('zh-CN');
let pendingDeleteConfirmation = null;
let pendingStopAllConfirmation = null;
let pendingAuthPrompt = null;
let controlStateLoadPromise = null;
let authPromptResolver = null;
let authPromptDismissed = false;
let authPromptCloseTimer = null;

const LOCATION_ALIASES = {
    北京: ['北京'], 上海: ['上海'], 天津: ['天津'], 重庆: ['重庆'],
    广东: ['广东', '广州', '深圳', '佛山', '东莞', '珠海', '惠州', '中山', '汕头'],
    江苏: ['江苏', '南京', '苏州', '无锡', '常州', '南通', '徐州', '扬州', '昆山'],
    浙江: ['浙江', '杭州', '宁波', '温州', '嘉兴', '绍兴', '金华', '台州'],
    山东: ['山东', '济南', '青岛', '烟台', '潍坊', '临沂', '威海'],
    四川: ['四川', '成都', '绵阳', '德阳', '宜宾'], 湖北: ['湖北', '武汉', '宜昌', '襄阳'],
    湖南: ['湖南', '长沙', '株洲', '湘潭'], 河南: ['河南', '郑州', '洛阳', '开封'],
    河北: ['河北', '石家庄', '保定', '廊坊', '唐山', '雄安', '承德'], 福建: ['福建', '福州', '厦门', '泉州', '漳州'],
    安徽: ['安徽', '合肥', '芜湖'], 陕西: ['陕西', '西安', '咸阳'], 辽宁: ['辽宁', '沈阳', '大连'],
    吉林: ['吉林', '长春'], 黑龙江: ['黑龙江', '哈尔滨'], 江西: ['江西', '南昌', '赣州'],
    广西: ['广西', '南宁', '桂林'], 云南: ['云南', '昆明', '大理'], 贵州: ['贵州', '贵阳'],
    山西: ['山西', '太原'], 甘肃: ['甘肃', '兰州'], 青海: ['青海', '西宁'], 海南: ['海南', '海口', '三亚'],
    内蒙古: ['内蒙古', '呼和浩特', '包头'], 宁夏: ['宁夏', '银川'], 新疆: ['新疆', '乌鲁木齐'],
    西藏: ['西藏', '拉萨'], 台湾: ['台湾', '台北'], 香港: ['香港'], 澳门: ['澳门'],
};

const CITY_COORDINATES = {
    北京: [116.41, 39.90], 上海: [121.47, 31.23], 天津: [117.20, 39.13], 重庆: [106.55, 29.56],
    深圳: [114.06, 22.55], 广州: [113.27, 23.13], 佛山: [113.12, 23.02], 东莞: [113.75, 23.02], 珠海: [113.58, 22.27], 惠州: [114.42, 23.11], 中山: [113.39, 22.52], 汕头: [116.68, 23.35],
    南京: [118.80, 32.06], 苏州: [120.59, 31.30], 无锡: [120.31, 31.49], 常州: [119.97, 31.81], 南通: [120.89, 31.98], 徐州: [117.28, 34.21], 扬州: [119.42, 32.39], 昆山: [120.98, 31.38],
    杭州: [120.16, 30.27], 宁波: [121.55, 29.87], 温州: [120.70, 28.00], 嘉兴: [120.76, 30.75], 绍兴: [120.58, 30.03], 金华: [119.65, 29.08], 台州: [121.42, 28.66],
    济南: [117.12, 36.65], 青岛: [120.38, 36.07], 烟台: [121.45, 37.46], 潍坊: [119.16, 36.71], 临沂: [118.36, 35.10], 威海: [122.12, 37.51],
    成都: [104.07, 30.57], 绵阳: [104.68, 31.47], 德阳: [104.40, 31.13], 宜宾: [104.64, 28.75],
    武汉: [114.31, 30.59], 宜昌: [111.29, 30.69], 襄阳: [112.12, 32.01],
    长沙: [112.94, 28.23], 株洲: [113.13, 27.83], 湘潭: [112.94, 27.83],
    郑州: [113.63, 34.75], 洛阳: [112.45, 34.62], 开封: [114.31, 34.80],
    石家庄: [114.51, 38.04], 保定: [115.47, 38.87], 廊坊: [116.68, 39.54], 唐山: [118.18, 39.63], 雄安: [115.93, 39.05], 承德: [117.96, 40.95],
    福州: [119.30, 26.08], 厦门: [118.09, 24.48], 泉州: [118.68, 24.87], 漳州: [117.65, 24.51],
    合肥: [117.23, 31.82], 芜湖: [118.38, 31.33], 西安: [108.94, 34.34], 咸阳: [108.71, 34.33],
    沈阳: [123.43, 41.80], 大连: [121.61, 38.91], 长春: [125.32, 43.82], 哈尔滨: [126.53, 45.80],
    南昌: [115.86, 28.68], 赣州: [114.94, 25.83], 南宁: [108.37, 22.82], 桂林: [110.29, 25.27],
    昆明: [102.83, 25.04], 大理: [100.23, 25.60], 贵阳: [106.63, 26.65], 太原: [112.55, 37.87],
    兰州: [103.84, 36.06], 西宁: [101.78, 36.62], 海口: [110.20, 20.04], 三亚: [109.51, 18.25],
    呼和浩特: [111.75, 40.84], 包头: [109.84, 40.66], 银川: [106.23, 38.49], 乌鲁木齐: [87.62, 43.83], 拉萨: [91.13, 29.65],
    香港: [114.17, 22.32], 澳门: [113.55, 22.20], 台北: [121.57, 25.04],
};

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function readAuthToken() {
    try { return sessionStorage.getItem(AUTH_TOKEN_KEY) || ''; } catch (_) { return ''; }
}

function writeAuthToken(token) {
    try {
        if (token) sessionStorage.setItem(AUTH_TOKEN_KEY, token);
        else sessionStorage.removeItem(AUTH_TOKEN_KEY);
    } catch (_) { /* 无存储权限时仍允许当前请求继续 */ }
}

function settleAuthPrompt(token = '') {
    if (!pendingAuthPrompt) return;
    const overlay = $('#authPrompt');
    overlay.classList.remove('open');
    clearTimeout(authPromptCloseTimer);
    authPromptCloseTimer = setTimeout(() => { overlay.hidden = true; authPromptCloseTimer = null; }, 180);
    const resolve = authPromptResolver;
    pendingAuthPrompt = null;
    authPromptResolver = null;
    if (resolve) resolve(token);
}

function cancelAuthPrompt() {
    authPromptDismissed = true;
    settleAuthPrompt('');
}

function requestAuthToken(message = '请输入后端配置的共享令牌。') {
    if (pendingAuthPrompt) return pendingAuthPrompt;
    if (authPromptDismissed) return Promise.resolve('');
    const overlay = $('#authPrompt');
    const input = $('#authTokenInput');
    const error = $('#authPromptError');
    clearTimeout(authPromptCloseTimer);
    authPromptCloseTimer = null;
    input.value = '';
    error.textContent = message;
    error.hidden = !message;
    overlay.hidden = false;
    requestAnimationFrame(() => { overlay.classList.add('open'); input.focus(); });
    pendingAuthPrompt = new Promise((resolve) => { authPromptResolver = resolve; });
    return pendingAuthPrompt;
}

function requestHeaders(headers) {
    const next = new Headers(headers || {});
    const token = readAuthToken();
    if (token && !next.has('Authorization')) next.set('Authorization', `Bearer ${token}`);
    return next;
}

async function authorizedFetch(input, options = {}) {
    const { headers, ...requestOptions } = options;
    let lastResponse = null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
        lastResponse = await fetch(input, { ...requestOptions, headers: requestHeaders(headers) });
        if (lastResponse.status !== 401) return lastResponse;
        writeAuthToken('');
        const token = await requestAuthToken(attempt ? '令牌无效，请重新输入。' : '当前后端需要共享令牌。');
        if (!token) return lastResponse;
    }
    return fetch(input, { ...requestOptions, headers: requestHeaders(headers) });
}

const INDUSTRY_ICON_RULES = [
    { pattern: /互联网|软件|信息技术|计算机|人工智能|大数据|云计算|通信|电子商务|游戏|网络/, icon: '<path d="M4 5h16v11H4z"/><path d="M8 20h8M12 16v4M8 9l2 2 3-3 3 3"/>' },
    { pattern: /金融|银行|证券|保险|基金|投资|信托|会计/, icon: '<path d="M3 10h18M5 10v8m4-8v8m6-8v8m4-8v8M3 18h18M12 3l9 5H3z"/>' },
    { pattern: /制造|工业|机械|汽车|设备|电子|半导体|能源|化工|材料/, icon: '<path d="M3 21V9l6 4V9l6 4V5h6v16z"/><path d="M7 17h2m3 0h2m3 0h2"/>' },
    { pattern: /教育|培训|学校|院校|科研|学术/, icon: '<path d="m2 9 10-5 10 5-10 5z"/><path d="M6 11v5c3 2 9 2 12 0v-5M21 10v6"/>' },
    { pattern: /医疗|医药|健康|生物|医院|护理/, icon: '<path d="M9 3h6v6h6v6h-6v6H9v-6H3V9h6z"/>' },
    { pattern: /建筑|房地产|工程|装修|物业/, icon: '<path d="M4 21V7l8-4 8 4v14M8 10h2m4 0h2m-8 4h2m4 0h2m-6 7v-4h4v4"/>' },
    { pattern: /零售|批发|消费|餐饮|食品|酒店|旅游|生活服务/, icon: '<path d="M4 10h16l-1-5H5zM5 10v10h14V10M9 20v-6h6v6"/>' },
    { pattern: /物流|运输|仓储|快递|供应链/, icon: '<path d="M3 6h11v11H3zM14 10h4l3 3v4h-7z"/><circle cx="7" cy="19" r="2"/><circle cx="18" cy="19" r="2"/>' },
];

function industryIcon(industry) {
    const text = String(industry || '');
    const match = INDUSTRY_ICON_RULES.find((rule) => rule.pattern.test(text));
    const paths = match?.icon || '<path d="M4 21V5h10v16M14 9h6v12M8 9h2m-2 4h2m-2 4h2m8-4h-2m2 4h-2M2 21h20"/>';
    return `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
}

function parseDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
}

function localDateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function normalizeProvince(value) {
    const text = String(value || '').replace(/\s+/g, '');
    if (!text) return '未知地区';
    for (const [province, aliases] of Object.entries(LOCATION_ALIASES)) {
        if (aliases.some((alias) => text.includes(alias))) return province;
    }
    return text.replace(/省|市|壮族自治区|回族自治区|维吾尔自治区|自治区|特别行政区/g, '') || '未知地区';
}

function recordCity(record) {
    if (record.city) return record.city.replace(/市$/, '');
    const location = String(record.location || '');
    if (!location) return '未知城市';
    return location.split(/[·\s]/)[0].replace(/市$/, '') || '未知城市';
}

function normalizeCityName(value) {
    const text = String(value || '').replace(/\s+/g, '');
    const direct = { 北京市: '北京', 上海市: '上海', 天津市: '天津', 重庆市: '重庆', 香港特别行政区: '香港', 澳门特别行政区: '澳门', 台湾省: '台湾' };
    return direct[text] || text.replace(/特别行政区$|(?:市|地区|盟|自治州)$/g, '') || '未知城市';
}

function buildCityNameIndex(features) {
    const byAlias = new Map();
    const addAlias = (alias, city) => { const clean = String(alias || '').replace(/\s+/g, ''); if (clean && clean !== '未知城市') byAlias.set(clean, city); };
    features.forEach((feature) => {
        const raw = feature.properties?.name || '';
        const city = normalizeCityName(raw);
        addAlias(raw, city); addAlias(city, city);
        Object.keys(CITY_COORDINATES).filter((name) => raw.startsWith(name)).forEach((name) => addAlias(name, city));
    });
    return {
        byAlias,
        aliases: [...byAlias.entries()].sort((a, b) => b[0].length - a[0].length || a[0].localeCompare(b[0], 'zh-CN')),
    };
}

function mapCityName(record) {
    const values = [record.city, record.location, recordCity(record)].filter(Boolean).map((value) => String(value).replace(/\s+/g, ''));
    const index = state.cityNameIndex;
    if (index) {
        for (const value of values) {
            const normalized = normalizeCityName(value);
            if (index.byAlias.has(value)) return index.byAlias.get(value);
            if (index.byAlias.has(normalized)) return index.byAlias.get(normalized);
            const match = index.aliases.find(([alias]) => value.includes(alias));
            if (match) return match[1];
        }
    }
    const city = recordCity(record).replace(/市$/, '');
    if (CITY_COORDINATES[city]) return city;
    return Object.keys(CITY_COORDINATES).find((name) => city.startsWith(name) || String(record.location || '').includes(name)) || '未知城市';
}

function rangeStartDate(range, now = new Date()) {
    if (range === 'all') return null;
    const days = range === 'today' ? 1 : Number(range);
    if (!Number.isFinite(days) || days < 1) return null;
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() - days + 1);
}

function evaluatedJobsForRange(summary, range, now = new Date()) {
    const total = Number(summary?.evaluatedJobs || 0);
    if (range === 'all') return total;
    if (!Array.isArray(summary?.evaluatedJobsByDate)) return null;
    const cutoff = rangeStartDate(range, now);
    if (!cutoff) return total;
    const startKey = localDateKey(cutoff);
    const endKey = localDateKey(now);
    return summary.evaluatedJobsByDate.reduce((sum, item) => {
        const date = String(item?.date || '');
        const count = Number(item?.count || 0);
        return date >= startKey && date <= endKey && Number.isFinite(count) ? sum + count : sum;
    }, 0);
}

function matchesSalaryBucket(record, range) {
    if (!range) return true;
    const [minimumText, maximumText = ''] = String(range).split(':');
    const minimum = Number(minimumText);
    const maximum = maximumText === '' ? null : Number(maximumText);
    const value = Number(record?.salaryK);
    return Number.isFinite(value) && Number.isFinite(minimum) && value >= minimum && (maximum === null || value < maximum);
}

function getRangeRecords() {
    const cutoff = rangeStartDate(state.range);
    if (!cutoff) return [...state.records];
    return state.records.filter((record) => {
        const date = parseDate(record.loggedAt);
        return date && date >= cutoff;
    });
}

function getFilteredRecords(ignoreRange = false, ignoreMap = false) {
    const search = state.search.trim().toLowerCase();
    const keyword = state.keyword.trim().toLowerCase();
    return (ignoreRange ? state.records : getRangeRecords()).filter((record) => {
        const haystack = `${record.company} ${record.title}`.toLowerCase();
        const keywordHaystack = `${record.company} ${record.title} ${record.industry} ${record.keyword}`.toLowerCase();
        if (search && !haystack.includes(search)) return false;
        if (keyword && !keywordHaystack.includes(keyword)) return false;
        if (state.industry && record.industry !== state.industry) return false;
        if (state.role && roleType(record.title) !== state.role) return false;
        if (state.exactDate && !record.loggedAt?.startsWith(state.exactDate)) return false;
        if (state.status !== 'all' && record.status !== state.status) return false;
        if (state.city !== 'all' && recordCity(record) !== state.city) return false;
        if (state.experience !== 'all' && (record.experience || '未知经验') !== state.experience) return false;
        if (state.education !== 'all' && (record.education || '未知学历') !== state.education) return false;
        if (state.minSalary !== null && (!Number.isFinite(record.salaryMinK) || record.salaryMinK < state.minSalary)) return false;
        if (state.maxSalary !== null && (!Number.isFinite(record.salaryMaxK) || record.salaryMaxK > state.maxSalary)) return false;
        if (!matchesSalaryBucket(record, state.salaryBucket)) return false;
        if (!ignoreMap && state.mapProvince && normalizeProvince(record.city || record.location) !== state.mapProvince) return false;
        if (!ignoreMap && state.mapCity && mapCityName(record) !== state.mapCity) return false;
        return true;
    });
}

function groupDaily(records) {
    const counts = new Map();
    records.forEach((record) => {
        const date = parseDate(record.loggedAt);
        if (!date) return;
        const key = localDateKey(date);
        counts.set(key, (counts.get(key) || 0) + 1);
    });
    if (!counts.size) return [];
    let start;
    let end = new Date();
    if (state.range === 'all') {
        const keys = [...counts.keys()].sort();
        start = new Date(`${keys[0]}T00:00:00`);
        end = new Date(`${keys.at(-1)}T00:00:00`);
    } else {
        start = rangeStartDate(state.range) || new Date();
    }
    const result = [];
    for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) {
        const key = localDateKey(cursor);
        result.push({ date: key, count: counts.get(key) || 0 });
    }
    return result;
}

function trendRangeRecords(records) {
    if (!state.trendStart && !state.trendEnd) return records;
    return records.filter((record) => {
        const key = String(record.loggedAt || '').slice(0, 10);
        return key && (!state.trendStart || key >= state.trendStart) && (!state.trendEnd || key <= state.trendEnd);
    });
}

function groupTrendDaily(records) {
    const counts = new Map();
    records.forEach((record) => { const date = parseDate(record.loggedAt); if (date) { const key = localDateKey(date); counts.set(key, (counts.get(key) || 0) + 1); } });
    if (!counts.size) return [];
    const keys = [...counts.keys()].sort();
    const start = state.trendStart ? new Date(`${state.trendStart}T00:00:00`) : new Date(`${keys[0]}T00:00:00`);
    const end = state.trendEnd ? new Date(`${state.trendEnd}T00:00:00`) : new Date(`${keys.at(-1)}T00:00:00`);
    const result = [];
    for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) { const key = localDateKey(cursor); result.push({ date: key, count: counts.get(key) || 0 }); }
    return result;
}

function setTrendRange(range) {
    const today = new Date(); let start = null;
    state.trendRange = range;
    if (range === 'today') start = today;
    else if (range === 'month') start = new Date(today.getFullYear(), today.getMonth(), 1);
    else if (range !== 'all') start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - Number(range) + 1);
    state.trendStart = start ? localDateKey(start) : '';
    state.trendEnd = range === 'all' ? '' : localDateKey(today);
    state.trendView = { ...state.trendView, zoom: 1, offset: 0, autoWindow: true };
    $('#trendStartDate').value = state.trendStart; $('#trendEndDate').value = state.trendEnd;
    $$('#trendQuickRange button').forEach((button) => button.classList.toggle('active', button.dataset.trendRange === range));
    renderTrend(getFilteredRecords());
}

function renderSparkline(target, values, color) {
    const points = values.length ? values : [0, 0, 0, 0, 0, 0, 0];
    const max = Math.max(...points, 1), width = 88, height = 29;
    const coords = points.map((value, index) => `${index / Math.max(points.length - 1, 1) * width},${height - value / max * (height - 4) - 2}`).join(' ');
    $(target).innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><polyline points="${coords}" fill="none" stroke="${color}" stroke-width="1.6"/><polyline points="0,${height} ${coords} ${width},${height}" fill="${color}" opacity=".08" stroke="none"/></svg>`;
}

function renderMetrics(records) {
    const todayKey = localDateKey(new Date());
    const today = records.filter((record) => record.loggedAt?.slice(0, 10) === todayKey).length;
    const companies = new Set(records.map((record) => record.company).filter(Boolean));
    const salaries = records.map((record) => record.salaryK).filter(Number.isFinite);
    const average = salaries.length ? salaries.reduce((sum, value) => sum + value, 0) / salaries.length : null;
    const daily = groupDaily(records), dailyValues = daily.map((item) => item.count);
    $('#totalApplications').textContent = numberFormat.format(records.length);
    $('#uniqueCompanies').textContent = numberFormat.format(companies.size);
    $('#averageSalary').textContent = average === null ? '—' : average.toFixed(1);
    $('#todayApplications').textContent = today;
    $('#navRecordCount').textContent = state.records.length > 999 ? '999+' : state.records.length;
    $('#totalDelta').textContent = `当前筛选覆盖 ${new Set(records.map((r) => r.loggedAt?.slice(0, 10))).size} 个日期`;
    $('#companyDelta').textContent = records.length ? `平均每家公司 ${(records.length / Math.max(companies.size, 1)).toFixed(1)} 份` : '暂无公司数据';
    $('#salaryHint').textContent = salaries.length ? `已识别 ${Math.round(salaries.length / Math.max(records.length, 1) * 100)}% 的薪资` : '暂无可识别薪资';
    renderDailyGoal(today);
    renderSparkline('#totalSparkline', dailyValues.slice(-8), COLORS[0]);
    renderSparkline('#companySparkline', daily.slice(-8).map((item) => new Set(records.filter((r) => r.loggedAt?.startsWith(item.date)).map((r) => r.company)).size), COLORS[1]);
    renderSparkline('#salarySparkline', daily.slice(-8).map((item) => {
        const values = records.filter((r) => r.loggedAt?.startsWith(item.date) && Number.isFinite(r.salaryK)).map((r) => r.salaryK);
        return values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
    }), COLORS[2]);
    renderSparkline('#todaySparkline', dailyValues.slice(-8), COLORS[3]);
}

function renderTrend(records) {
    const rangedRecords = trendRangeRecords(records), fullData = groupTrendDaily(rangedRecords), container = $('#trendChart');
    $('#trendTotal').textContent = numberFormat.format(rangedRecords.length);
    if (state.trendView.autoWindow && fullData.length > 14) {
        state.trendView.zoom = fullData.length / 14;
        state.trendView.offset = fullData.length - 14;
        state.trendView.autoWindow = false;
    }
    const visibleCount = Math.max(2, Math.ceil(fullData.length / state.trendView.zoom));
    const maxOffset = Math.max(0, fullData.length - visibleCount);
    state.trendView.visibleCount = visibleCount;
    state.trendView.offset = Math.max(0, Math.min(maxOffset, Math.round(state.trendView.offset)));
    const data = fullData.slice(state.trendView.offset, state.trendView.offset + visibleCount);
    const scrollbar = $('#trendScroll'); scrollbar.max = String(maxOffset); scrollbar.value = String(state.trendView.offset); scrollbar.disabled = maxOffset === 0;
    container.dataset.draggable = maxOffset > 0 ? 'true' : 'false';
    const peak = fullData.reduce((best, item) => item.count > (best?.count ?? -1) ? item : best, null);
    $('#trendPeak').textContent = peak ? `峰值 ${peak.date.slice(5).replace('-', '/')} · ${peak.count} 份` : '峰值 —';
    if (!data.length) { container.innerHTML = '<div class="empty-state"><strong>当前筛选暂无趋势数据</strong></div>'; return; }
    const width = 760, height = 205, left = 34, right = 10, top = 12, bottom = 27;
    const chartW = width - left - right, chartH = height - top - bottom;
    const max = Math.max(...data.map((item) => item.count), 4), niceMax = Math.ceil(max / 5) * 5;
    const coords = data.map((item, index) => ({ ...item, x: left + index / Math.max(data.length - 1, 1) * chartW, y: top + chartH - item.count / niceMax * chartH }));
    const line = coords.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ');
    const area = `${left},${top + chartH} ${line} ${left + chartW},${top + chartH}`;
    const average = data.reduce((sum, item) => sum + item.count, 0) / data.length;
    const avgY = top + chartH - average / niceMax * chartH;
    const grid = Array.from({ length: 5 }, (_, index) => {
        const value = Math.round(niceMax - niceMax / 4 * index), y = top + chartH / 4 * index;
        return `<line class="grid-line" x1="${left}" y1="${y}" x2="${left + chartW}" y2="${y}"/><text class="axis-label" x="0" y="${y + 3}">${value}</text>`;
    }).join('');
    const step = Math.max(1, Math.ceil(data.length / 7));
    const labels = coords.filter((_, index) => index % step === 0 || index === coords.length - 1).map((point) => `<text class="axis-label" text-anchor="middle" x="${point.x}" y="${height - 5}">${point.date.slice(5).replace('-', '/')}</text>`).join('');
    const dots = coords.map((point) => `<circle class="trend-dot ${state.exactDate === point.date ? 'is-active' : ''}" data-date="${point.date}" cx="${point.x}" cy="${point.y}" r="2.8"><title>${point.date}：${point.count} 份，点击筛选</title></circle>`).join('');
    const visibleRange = data.length ? `${data[0].date.slice(5).replace('-', '/')} — ${data.at(-1).date.slice(5).replace('-', '/')}` : '暂无日期';
    const dragHint = maxOffset > 0
        ? `<div class="trend-drag-indicator"><span>⇆</span><b>${visibleRange}</b><small>按住图表左右拖动日期</small></div>`
        : `<div class="trend-drag-indicator static"><b>${visibleRange}</b><small>当前日期已全部显示</small></div>`;
    container.innerHTML = `${dragHint}<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><defs><linearGradient id="trendGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--cyan)" stop-opacity=".23"/><stop offset="1" stop-color="var(--cyan)" stop-opacity="0"/></linearGradient></defs>${grid}<line class="average-line" x1="${left}" y1="${avgY}" x2="${left + chartW}" y2="${avgY}"/><polygon class="trend-area" points="${area}"/><polyline class="trend-line" points="${line}"/>${dots}${labels}</svg>`;
}

function renderFunnel(records) {
    const summary = state.payload?.summary || {}, evaluated = Number(summary.evaluatedJobs || 0), delivered = records.length;
    const rate = evaluated ? Math.min(100, Math.round(delivered / evaluated * 100)) : 0;
    $('#conversionRate').textContent = `${rate}%`;
    $('#conversionRing').style.setProperty('--ring', `${rate * 3.6}deg`);
    $('#evaluatedJobs').textContent = numberFormat.format(evaluated);
    $('#funnelDelivered').textContent = numberFormat.format(delivered);
    $('#belowThreshold').textContent = numberFormat.format(summary.belowThreshold || 0);
    $('#queueFailures').textContent = numberFormat.format(summary.queueFailures || 0);
    $('#funnelInsight').textContent = '投递数量按当前筛选计算；岗位评估、阈值和异常来自全量审计日志。';
}

function provinceStats(records) {
    const stats = new Map(); let unknown = 0;
    records.forEach((record) => {
        const province = normalizeProvince(record.city || record.location);
        if (province === '未知地区') { unknown += 1; return; }
        const item = stats.get(province) || { count: 0, salaries: [] };
        item.count += 1;
        if (Number.isFinite(record.salaryK)) item.salaries.push(record.salaryK);
        stats.set(province, item);
    });
    return { stats, unknown };
}

function cityStats(records) {
    const stats = new Map(); let unknown = 0;
    records.forEach((record) => {
        const city = mapCityName(record);
        const mappedBoundary = state.cityNameIndex?.byAlias.has(city);
        if (city === '未知城市' || (!mappedBoundary && !CITY_COORDINATES[city])) { unknown += 1; return; }
        const item = stats.get(city) || { count: 0, salaries: [] };
        item.count += 1;
        if (Number.isFinite(record.salaryK)) item.salaries.push(record.salaryK);
        stats.set(city, item);
    });
    return { stats, unknown };
}

function mapMetricValue(item, metric) {
    if (!item) return 0;
    return metric === 'salary'
        ? (item.salaries.length ? item.salaries.reduce((sum, value) => sum + value, 0) / item.salaries.length : 0)
        : item.count;
}

function mapColor(value, max) {
    if (!value || !max) return 'var(--map-empty)';
    const breaks = state.mapHeatBreaks;
    if (breaks.length === 4) {
        if (value > breaks[3]) return 'var(--map-5)';
        if (value > breaks[2]) return 'var(--map-4)';
        if (value > breaks[1]) return 'var(--map-3)';
        if (value > breaks[0]) return 'var(--map-2)';
        return 'var(--map-1)';
    }
    const ratio = value / max;
    if (ratio > .8) return 'var(--map-5)'; if (ratio > .6) return 'var(--map-4)'; if (ratio > .35) return 'var(--map-3)'; if (ratio > .15) return 'var(--map-2)'; return 'var(--map-1)';
}

function heatBreaks(values) {
    const sorted = values.filter((value) => value > 0).sort((a, b) => a - b);
    if (sorted.length < 5) return [];
    return [.2, .4, .6, .8].map((ratio) => sorted[Math.min(sorted.length - 1, Math.floor((sorted.length - 1) * ratio))]);
}

function chooseCityLabels(features, stats) {
    const scale = state.mapView.scale;
    const occupied = [];
    const candidates = features.map((feature) => {
        const center = feature.properties.centroid || feature.properties.center;
        const city = normalizeCityName(feature.properties.name);
        const item = stats.get(city);
        return { feature, center, city, count: item?.count || 0, selected: state.mapCity === city };
    }).filter((item) => item.center && (item.count || item.selected || scale >= 6));
    candidates.sort((a, b) => Number(b.selected) - Number(a.selected) || b.count - a.count || a.feature.properties.name.length - b.feature.properties.name.length);
    const limit = scale < 3.6 ? 22 : scale < 6 ? 45 : scale < 10 ? 90 : 150;
    return candidates.filter((item) => {
        if (occupied.length >= limit && !item.selected) return false;
        const [baseX, baseY] = projectCoordinate(item.center);
        const x = baseX * scale + state.mapView.x;
        const y = baseY * scale + state.mapView.y;
        const width = Math.max(28, item.feature.properties.name.length * 10);
        const collision = occupied.some((box) => Math.abs(box.x - x) < (box.width + width) / 2 + 8 && Math.abs(box.y - y) < 17);
        if (collision && !item.selected) return false;
        occupied.push({ x, y, width });
        return true;
    });
}

function mapLabelTier(scale) {
    if (scale >= 10) return 3;
    if (scale >= 6) return 2;
    if (scale >= 3.6) return 1;
    return 0;
}

function projectCoordinate([longitude, latitude]) {
    return [(longitude - 73) / 62 * 670, (54 - latitude) / 36 * 400];
}

function geometryPath(geometry) {
    if (GEOMETRY_PATH_CACHE.has(geometry)) return GEOMETRY_PATH_CACHE.get(geometry);
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
    const path = polygons.map((polygon) => polygon.map((ring) => ring.map((coordinate, index) => {
        const [x, y] = projectCoordinate(coordinate);
        return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ') + ' Z').join(' ')).join(' ');
    GEOMETRY_PATH_CACHE.set(geometry, path);
    return path;
}

async function ensureChinaGeo() {
    if (state.chinaGeo) return state.chinaGeo;
    const response = await fetch('/dashboard/china.json', { cache: 'force-cache' });
    if (!response.ok) throw new Error('中国地图数据加载失败');
    state.chinaGeo = await response.json();
    return state.chinaGeo;
}

async function ensureCityGeo() {
    if (state.cityGeo) return state.cityGeo;
    const response = await fetch('/dashboard/china-cities.json', { cache: 'force-cache' });
    if (!response.ok) throw new Error('地级市地图数据加载失败');
    state.cityGeo = await response.json();
    state.cityNameIndex = buildCityNameIndex(state.cityGeo.features || []);
    return state.cityGeo;
}

function selectMapCity(city) {
    if (!city) return;
    state.mapCity = state.mapCity === city ? '' : city;
    state.mapProvince = '';
    state.page = 1;
    updateDashboard();
    showToast(state.mapCity ? `已筛选地级市 ${state.mapCity}` : '已取消城市筛选');
}

function selectMapProvince(province) {
    if (!province) return;
    state.mapProvince = state.mapProvince === province ? '' : province;
    state.mapCity = '';
    state.page = 1;
    updateDashboard();
    showToast(state.mapProvince ? `已筛选 ${state.mapProvince}` : '已取消地图筛选');
}

function renderProvinceCallouts(features, stats, metric, max) {
    const targets = { 香港: [466, 345], 澳门: [460, 367] };
    return Object.entries(targets).map(([name, [targetX, targetY]]) => {
        const feature = features.find((item) => normalizeProvince(item.properties?.name) === name);
        const center = feature?.properties?.centroid || feature?.properties?.center;
        if (!center) return '';
        const [x, y] = projectCoordinate(center);
        const value = mapMetricValue(stats.get(name), metric);
        return `<g class="geo-region-callout ${state.mapProvince === name ? 'is-active' : ''}" data-province="${name}" tabindex="0" role="button" aria-label="${name}热力区域，点击筛选"><line x1="${x.toFixed(2)}" y1="${y.toFixed(2)}" x2="${(targetX - 5).toFixed(2)}" y2="${targetY.toFixed(2)}"></line><circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="2.2" fill="${mapColor(value, max)}"></circle><text x="${targetX}" y="${targetY + 3}">${name}</text></g>`;
    }).join('');
}

async function renderMap(records) {
    const container = $('#chinaMap');
    const renderVersion = ++state.mapRenderVersion;
    try {
        const metric = $('#mapMetric').value;
        const level = state.mapLevel === 'city' ? 'city' : 'province';
        const geo = await ensureChinaGeo();
        if (renderVersion !== state.mapRenderVersion) return;
        const cityGeo = level === 'city' ? await ensureCityGeo() : null;
        if (renderVersion !== state.mapRenderVersion) return;
        const { stats, unknown } = level === 'city' ? cityStats(records) : provinceStats(records);
        const values = [...stats.values()].map((item) => mapMetricValue(item, metric));
        const max = Math.max(...values, 0);
        state.mapHeatBreaks = heatBreaks(values);
        const features = geo.features.filter((feature) => feature.properties?.name);
        container.dataset.mapLevel = level;
        let paths = '';
        let labels = '';
        let callouts = '';
        if (level === 'province') {
            paths = features.map((feature) => {
                const name = normalizeProvince(feature.properties.name);
                const item = stats.get(name) || { count: 0, salaries: [] };
                const value = mapMetricValue(item, metric);
                const title = metric === 'salary' ? `${name}：${value ? value.toFixed(1) + 'K' : '暂无薪资'}` : `${name}：${value} 份`;
                return `<path class="geo-province ${state.mapProvince === name ? 'is-active' : ''}" data-province="${escapeHtml(name)}" data-map-label="${escapeHtml(name)}" data-map-value="${escapeHtml(title.split('：')[1] || '')}" tabindex="0" role="button" aria-label="${title}，点击筛选" d="${geometryPath(feature.geometry)}" fill="${mapColor(value, max)}" fill-rule="evenodd"></path>`;
            }).join('');
            labels = features.map((feature) => {
                const name = normalizeProvince(feature.properties.name);
                const center = feature.properties.centroid || feature.properties.center;
                if (!center || name === '香港' || name === '澳门') return '';
                const [x, y] = projectCoordinate(center);
                return `<text class="geo-label ${stats.get(name)?.count ? 'hot' : ''}" x="${x}" y="${y}">${name.length > 3 ? name.slice(0, 3) : name}</text>`;
            }).join('');
            callouts = renderProvinceCallouts(features, stats, metric, max);
        } else {
            const inverseScale = (1 / state.mapView.scale).toFixed(4);
            const cityFeatures = (cityGeo?.features || []).filter((feature) => feature.properties?.name);
            const cityBoundaries = cityFeatures.map((feature) => {
                const city = normalizeCityName(feature.properties.name);
                const item = stats.get(city) || { count: 0, salaries: [] };
                const value = mapMetricValue(item, metric);
                const hasData = item.count > 0;
                const title = metric === 'salary'
                    ? `${feature.properties.name}：${value ? `平均薪资 ${value.toFixed(1)}K` : '暂无薪资数据'}`
                    : `${feature.properties.name}热力区域${hasData ? '已着色' : '暂无数据'}`;
                return `<path class="geo-city-boundary ${hasData ? 'has-data' : ''}" ${hasData ? `data-city="${escapeHtml(city)}" data-map-label="${escapeHtml(feature.properties.name)}" data-map-value="${escapeHtml(metric === 'salary' ? (value ? `平均 ${value.toFixed(1)}K` : '暂无薪资') : `${item.count} 份投递`)}" tabindex="0" role="button" aria-label="${title}，点击筛选"` : ''} d="${geometryPath(feature.geometry)}" fill="${mapColor(value, max)}" fill-rule="evenodd"></path>`;
            }).join('');
            const provinceOutlines = features.map((feature) => `<path class="geo-province-outline" d="${geometryPath(feature.geometry)}" fill-rule="evenodd"></path>`).join('');
            paths = `<g class="city-boundary-layer">${cityBoundaries}</g><g class="province-outline-layer">${provinceOutlines}</g>`;
            labels = chooseCityLabels(cityFeatures, stats).map(({ feature, city }) => {
                const center = feature.properties.centroid || feature.properties.center;
                if (!center) return '';
                const [x, y] = projectCoordinate(center);
                const length = feature.properties.name.length;
                const fontSize = length > 10 ? 8.5 : (length > 6 ? 9.2 : 10.5);
                return `<g class="geo-city-label ${stats.get(city)?.count ? 'has-data' : ''}" transform="translate(${x.toFixed(2)} ${y.toFixed(2)})"><g class="map-fixed-visual" transform="scale(${inverseScale})"><text data-base-font-size="${fontSize}">${escapeHtml(feature.properties.name)}</text></g></g>`;
            }).join('');
        }
        const { scale, x, y } = state.mapView;
        container.innerHTML = `<svg viewBox="0 0 670 400" preserveAspectRatio="xMidYMid meet"><g class="map-viewport" transform="translate(${x} ${y}) scale(${scale})">${paths}${labels}${callouts}</g></svg>`;
        updateCityLabelSizes();
        updateMapZoomLabel();
        if (renderVersion !== state.mapRenderVersion) return;
        const known = Math.max(0, records.length - unknown);
        $('#mapKnownCount').textContent = `${values.filter((value) => value > 0).length} 个`;
        $('#mapLevelLabel').textContent = level === 'city' ? '城市级' : '省级';
        $('#mapActiveFilter').textContent = state.mapCity || state.mapProvince || '全国';
        $('#knownLocationRate').textContent = `已识别 ${records.length ? Math.round(known / records.length * 100) : 0}%`;
        $('#locationRankingTitle').textContent = level === 'city' ? '热门地级市' : '热门省份';
        $('#mapInteractionHint').textContent = level === 'city'
            ? `地级市边界与名称 · 缩至 ${Math.round(CITY_DETAIL_EXIT_SCALE * 100)}% 自动返回省份`
            : `省份热力 · 放大至 ${Math.round(CITY_DETAIL_ENTER_SCALE * 100)}% 自动显示地级市 · 港澳连线`;
        const nonzeroValues = values.filter((value) => value > 0);
        const minimum = nonzeroValues.length ? Math.min(...nonzeroValues) : 0;
        const hideCityCount = level === 'city' && metric === 'count';
        $('#mapScaleMin').textContent = hideCityCount ? '低' : (metric === 'salary' ? `${minimum ? minimum.toFixed(0) : 0}K` : `${minimum}份`);
        $('#mapScaleMax').textContent = hideCityCount ? '高' : (metric === 'salary' ? `${max ? max.toFixed(0) : 0}K` : `${max}份`);
        const ranked = [...stats.entries()].filter(([, item]) => mapMetricValue(item, metric) > 0).sort((a, b) => mapMetricValue(b[1], metric) - mapMetricValue(a[1], metric)).slice(0, 7);
        const rankingMax = ranked.length ? Math.max(...ranked.map(([, item]) => mapMetricValue(item, metric)), 1) : 1;
        $('#locationList').innerHTML = ranked.length ? ranked.map(([name, item], index) => {
            const value = mapMetricValue(item, metric);
            const display = metric === 'salary' ? `${value.toFixed(1)}K` : item.count;
            const selectedLocation = level === 'city' ? state.mapCity : state.mapProvince;
            return `<button type="button" class="ranking-item ${selectedLocation === name ? 'is-active' : ''}" data-map-location="${escapeHtml(name)}" aria-pressed="${selectedLocation === name}"><span>${index + 1}</span><span class="ranking-name">${escapeHtml(name)}</span>${hideCityCount ? '' : `<strong class="ranking-count">${display}</strong>`}<span class="ranking-bar"><i style="width:${value / rankingMax * 100}%"></i></span></button>`;
        }).join('') : '<div class="empty-state" style="padding:38px 0"><strong>历史地区尚未采集</strong><p>新投递会自动进入热力地图</p></div>';
    } catch (error) {
        if (renderVersion !== state.mapRenderVersion) return;
        console.error(error);
        container.innerHTML = '<div class="empty-state"><strong>中国地图加载失败</strong><p>请刷新页面重试</p></div>';
    }
}

function mapLevelForScale(scale, currentLevel = state.mapLevel) {
    if (currentLevel === 'city') return scale <= CITY_DETAIL_EXIT_SCALE ? 'province' : 'city';
    return scale >= CITY_DETAIL_ENTER_SCALE ? 'city' : 'province';
}

function syncMapLevelWithScale() {
    const nextLevel = mapLevelForScale(state.mapView.scale);
    if (nextLevel === state.mapLevel) return false;
    state.mapLevel = nextLevel;
    state.mapCity = '';
    state.page = 1;
    const focused = document.activeElement;
    if (focused?.closest?.('#chinaMap')) focused.blur();
    return true;
}

function clampMapView() {
    const view = state.mapView;
    view.scale = Math.max(1, Math.min(MAP_MAX_SCALE, view.scale));
    const minX = -670 * (view.scale - 1), minY = -400 * (view.scale - 1);
    view.x = Math.max(minX, Math.min(0, view.x));
    view.y = Math.max(minY, Math.min(0, view.y));
}

function applyMapTransform() {
    clampMapView();
    const viewport = $('#chinaMap .map-viewport');
    if (viewport) viewport.setAttribute('transform', `translate(${state.mapView.x} ${state.mapView.y}) scale(${state.mapView.scale})`);
    const inverseScale = String(1 / state.mapView.scale);
    $$('#chinaMap .map-fixed-visual').forEach((element) => element.setAttribute('transform', `scale(${inverseScale})`));
    updateCityLabelSizes();
    updateMapZoomLabel();
}

function updateCityLabelSizes() {
    $$('#chinaMap .geo-city-label text').forEach((label) => {
        const base = Number(label.dataset.baseFontSize || 10);
        label.style.fontSize = `${base}px`;
    });
}

function updateMapZoomLabel() {
    const label = $('#mapZoomValue');
    if (!label) return;
    label.textContent = `${Math.round(state.mapView.scale * 100)}%`;
}

function zoomMap(nextScale, centerX = 335, centerY = 200) {
    const view = state.mapView, previousScale = view.scale, previousLabelTier = mapLabelTier(view.scale);
    const scale = Math.max(1, Math.min(MAP_MAX_SCALE, nextScale));
    if (scale === previousScale) return;
    view.x = centerX - ((centerX - view.x) / previousScale) * scale;
    view.y = centerY - ((centerY - view.y) / previousScale) * scale;
    view.scale = scale;
    const levelChanged = syncMapLevelWithScale();
    applyMapTransform();
    const labelTierChanged = state.mapLevel === 'city' && previousLabelTier !== mapLabelTier(scale);
    if (levelChanged) updateDashboard();
    else if (labelTierChanged) updateDashboard();
}

function resetMapView() {
    const levelChanged = state.mapLevel !== 'province';
    state.mapLevel = 'province';
    state.mapCity = '';
    Object.assign(state.mapView, { scale: 1, x: 0, y: 0, suppressClick: false });
    applyMapTransform();
    if (levelChanged) updateDashboard();
}

function clientToSvgPoint(svg, clientX, clientY) {
    const matrix = svg.getScreenCTM();
    if (matrix) {
        const point = svg.createSVGPoint();
        point.x = clientX;
        point.y = clientY;
        return point.matrixTransform(matrix.inverse());
    }
    const rect = svg.getBoundingClientRect();
    return { x: (clientX - rect.left) / rect.width * 670, y: (clientY - rect.top) / rect.height * 400 };
}

function initMapNavigation() {
    const map = $('#chinaMap');
    const stage = $('#mapStage');
    const tooltip = $('#mapTooltip');
    let pointer = null;
    const showMapTooltip = (target, clientX, clientY) => {
        if (!target?.dataset.mapLabel) { tooltip.hidden = true; return; }
        tooltip.innerHTML = `<strong>${escapeHtml(target.dataset.mapLabel)}</strong><span>${escapeHtml(target.dataset.mapValue || '点击筛选')}</span>`;
        tooltip.hidden = false;
        const stageRect = stage.getBoundingClientRect();
        const left = Math.min(stageRect.width - tooltip.offsetWidth - 18, Math.max(8, clientX - stageRect.left));
        const top = Math.min(stageRect.height - tooltip.offsetHeight - 18, Math.max(8, clientY - stageRect.top));
        tooltip.style.left = `${left}px`; tooltip.style.top = `${top}px`;
    };
    map.addEventListener('pointermove', (event) => {
        if (pointer) return;
        showMapTooltip(event.target.closest('[data-map-label]'), event.clientX, event.clientY);
    });
    map.addEventListener('pointerleave', () => { tooltip.hidden = true; });
    map.addEventListener('focusin', (event) => {
        const target = event.target.closest('[data-map-label]'); if (!target) return;
        const rect = target.getBoundingClientRect(); showMapTooltip(target, rect.left + rect.width / 2, rect.top + rect.height / 2);
    });
    map.addEventListener('focusout', () => { tooltip.hidden = true; });
    map.addEventListener('wheel', (event) => {
        const zoomingIn = event.deltaY < 0;
        if ((!zoomingIn && state.mapView.scale <= 1) || (zoomingIn && state.mapView.scale >= MAP_MAX_SCALE)) return;
        event.preventDefault();
        const svg = map.querySelector('svg'); if (!svg) return;
        const center = clientToSvgPoint(svg, event.clientX, event.clientY);
        zoomMap(state.mapView.scale * Math.exp(-event.deltaY * .0015), center.x, center.y);
    }, { passive: false });
    map.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        const svg = map.querySelector('svg'); if (!svg) return;
        pointer = { id: event.pointerId, start: clientToSvgPoint(svg, event.clientX, event.clientY), x: state.mapView.x, y: state.mapView.y, moved: false, svg };
        map.setPointerCapture(event.pointerId); map.classList.add('is-panning');
    });
    map.addEventListener('pointermove', (event) => {
        if (!pointer || pointer.id !== event.pointerId || state.mapView.scale <= 1) return;
        const current = clientToSvgPoint(pointer.svg, event.clientX, event.clientY);
        const dx = current.x - pointer.start.x, dy = current.y - pointer.start.y;
        if (Math.hypot(dx, dy) > 5) pointer.moved = true;
        state.mapView.x = pointer.x + dx;
        state.mapView.y = pointer.y + dy;
        applyMapTransform();
    });
    const finishPan = (event) => {
        if (!pointer || pointer.id !== event.pointerId) return;
        state.mapView.suppressClick = pointer.moved;
        pointer = null; map.classList.remove('is-panning');
        window.setTimeout(() => { state.mapView.suppressClick = false; }, 80);
    };
    map.addEventListener('pointerup', finishPan); map.addEventListener('pointercancel', finishPan);
    map.addEventListener('click', (event) => {
        const city = event.target.closest('[data-city]');
        if (city && !state.mapView.suppressClick) {
            const name = city.dataset.city;
            city.blur?.();
            selectMapCity(name); return;
        }
        const province = event.target.closest('[data-province]');
        if (!province?.dataset.province || state.mapView.suppressClick) return;
        const name = province.dataset.province;
        province.blur?.();
        selectMapProvince(name);
    });
    map.addEventListener('keydown', (event) => {
        if (!['Enter', ' '].includes(event.key)) return;
        const city = event.target.closest('[data-city]');
        const province = event.target.closest('[data-province]');
        if (city) { event.preventDefault(); selectMapCity(city.dataset.city); }
        else if (province) { event.preventDefault(); selectMapProvince(province.dataset.province); }
    });
    stage.addEventListener('keydown', (event) => {
        if (event.target.closest('[data-city], [data-province], button, select')) return;
        const step = 34;
        if (event.key === '+' || event.key === '=') { event.preventDefault(); zoomMap(state.mapView.scale * 1.35); }
        else if (event.key === '-') { event.preventDefault(); zoomMap(state.mapView.scale / 1.35); }
        else if (event.key === '0') { event.preventDefault(); resetMapView(); }
        else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) && state.mapView.scale > 1) {
            event.preventDefault();
            if (event.key === 'ArrowLeft') state.mapView.x += step;
            if (event.key === 'ArrowRight') state.mapView.x -= step;
            if (event.key === 'ArrowUp') state.mapView.y += step;
            if (event.key === 'ArrowDown') state.mapView.y -= step;
            applyMapTransform();
        }
    });
    $('#mapZoomIn').addEventListener('click', () => zoomMap(state.mapView.scale * 1.45));
    $('#mapZoomOut').addEventListener('click', () => zoomMap(state.mapView.scale / 1.45));
    $('#mapResetView').addEventListener('click', () => { resetMapView(); showToast('地图视图已复位'); });
    $('#locationList').addEventListener('click', (event) => {
        const item = event.target.closest('[data-map-location]'); if (!item) return;
        const name = item.dataset.mapLocation;
        if (state.mapLevel === 'city') selectMapCity(name); else selectMapProvince(name);
    });
}

function salaryBucket(value) {
    if (value < 5) return '0–5K'; if (value < 8) return '5–8K'; if (value < 12) return '8–12K'; if (value < 20) return '12–20K'; return '20K 以上';
}

function renderSalary(records) {
    const labels = ['0–5K', '5–8K', '8–12K', '12–20K', '20K 以上'];
    const counts = Object.fromEntries(labels.map((label) => [label, 0])), valid = records.filter((record) => Number.isFinite(record.salaryK));
    valid.forEach((record) => { counts[salaryBucket(record.salaryK)] += 1; });
    const max = Math.max(...Object.values(counts), 1);
    $('#salaryCoverage').textContent = `识别率 ${records.length ? Math.round(valid.length / records.length * 100) : 0}%`;
    const ranges = [[0, 5], [5, 8], [8, 12], [12, 20], [20, null]];
    $('#salaryChart').innerHTML = labels.map((label, index) => {
        const [minimum, maximum] = ranges[index];
        const active = state.salaryBucket === `${minimum}:${maximum ?? ''}`;
        return `<div class="salary-row ${active ? 'is-active' : ''}" data-salary-filter="${minimum}:${maximum ?? ''}"><span>${label}</span><div class="salary-track"><i style="width:${counts[label] / max * 100}%"></i></div><strong>${counts[label]}</strong></div>`;
    }).join('');
}

function roleType(title) {
    const value = String(title || '').toLowerCase();
    if (/ai|人工智能|智能体|算法|大模型|prompt/.test(value)) return 'AI / 智能体';
    if (/运维|sre|devops|网络|实施|技术支持/.test(value)) return '运维 / SRE';
    if (/前端|web|vue|react/.test(value)) return '前端开发';
    if (/后端|python|java|golang|开发工程师|软件工程师/.test(value)) return '后端开发';
    if (/产品|项目经理/.test(value)) return '产品 / 项目'; if (/测试|质量/.test(value)) return '测试 / 质量'; if (/数据|bi|分析/.test(value)) return '数据分析'; return '其他岗位';
}

function renderRoles(records) {
    const counts = new Map(); records.forEach((record) => counts.set(roleType(record.title), (counts.get(roleType(record.title)) || 0) + 1));
    const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]), total = records.length || 1;
    $('#roleKinds').textContent = ranked.length;
    if (!ranked.length) { $('#roleDonut').style.background = 'var(--chart-empty)'; $('#roleLegend').textContent = '暂无岗位数据'; return; }
    let cursor = 0;
    $('#roleDonut').style.background = `conic-gradient(${ranked.map(([, count], index) => { const start = cursor; cursor += count / total * 100; return `${COLORS[index % COLORS.length]} ${start}% ${cursor}%`; }).join(',')})`;
    $('#roleLegend').innerHTML = ranked.slice(0, 6).map(([name, count], index) => `<div class="role-legend-item ${state.role === name ? 'is-active' : ''}" data-role="${escapeHtml(name)}"><i style="background:${COLORS[index % COLORS.length]}"></i><span>${escapeHtml(name)}</span><strong>${Math.round(count / total * 100)}%</strong></div>`).join('');
}

function renderIndustry(records) {
    const known = records.filter((record) => record.industry), counts = new Map();
    known.forEach((record) => counts.set(record.industry, (counts.get(record.industry) || 0) + 1));
    const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10), max = ranked[0]?.[1] || 1;
    $('#industryCoverage').textContent = `识别率 ${records.length ? Math.round(known.length / records.length * 100) : 0}%`;
    $('#industryChart').innerHTML = ranked.length ? ranked.map(([name, count], index) => `<div class="industry-row ${state.industry === name ? 'is-active' : ''}" data-industry="${escapeHtml(name)}"><i>${index + 1}</i><span title="${escapeHtml(name)}">${escapeHtml(name)}</span><div class="industry-track"><b style="width:${count / max * 100}%"></b></div><strong>${count}</strong></div>`).join('') : '<div class="empty-state" style="grid-column:1/-1;padding:35px"><strong>历史行业尚未采集</strong><p>新版脚本会从公司信息中采集行业并生成 TOP 10</p></div>';
}

function uniqueValues(field, fallback) {
    return [...new Set(state.records.map((record) => record[field] || fallback))].sort((a, b) => a === fallback ? 1 : b === fallback ? -1 : a.localeCompare(b, 'zh-CN'));
}

function fillSelect(selector, values, allLabel, current) {
    const select = $(selector);
    select.innerHTML = `<option value="all">${allLabel}</option>` + values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('');
    if (values.includes(current)) select.value = current; else select.value = 'all';
}

function populateFilters() {
    fillSelect('#cityFilter', [...new Set(state.records.map(recordCity))].sort((a, b) => a === '未知城市' ? 1 : b === '未知城市' ? -1 : a.localeCompare(b, 'zh-CN')), '全部城市', state.city);
    fillSelect('#experienceFilter', uniqueValues('experience', '未知经验'), '全部经验', state.experience);
    fillSelect('#educationFilter', uniqueValues('education', '未知学历'), '全部学历', state.education);
    if ($('#cityFilter').value === 'all') state.city = 'all';
    if ($('#experienceFilter').value === 'all') state.experience = 'all';
    if ($('#educationFilter').value === 'all') state.education = 'all';
}

function renderFilterChips() {
    const chips = [];
    if (state.city !== 'all') chips.push(`城市：${state.city}`); if (state.experience !== 'all') chips.push(`经验：${state.experience}`); if (state.education !== 'all') chips.push(`学历：${state.education}`);
    if (state.minSalary !== null) chips.push(`最低薪资 ≥ ${state.minSalary}K`); if (state.maxSalary !== null) chips.push(`最高薪资 ≤ ${state.maxSalary}K`); if (state.keyword) chips.push(`关键词：${state.keyword}`); if (state.mapProvince) chips.push(`地图：${state.mapProvince}`);
    if (state.salaryBucket) chips.push(`薪资分布：${({ '0:5': '0–5K', '5:8': '5–8K', '8:12': '8–12K', '12:20': '12–20K', '20:': '20K 以上' })[state.salaryBucket] || state.salaryBucket}`);
    if (state.industry) chips.push(`行业：${state.industry}`); if (state.role) chips.push(`岗位类型：${state.role}`); if (state.exactDate) chips.push(`日期：${state.exactDate}`); if (state.mapCity) chips.push(`城市热力：${state.mapCity}`);
    $('#activeFilterChips').innerHTML = chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join('');
}

function formatDateTime(value) {
    const date = parseDate(value); if (!date) return { date: '未知日期', time: '' };
    return { date: `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')}`, time: `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` };
}

const TABLE_COLUMNS = {
    company: { label: '公司 / 岗位', width: 280, min: 220, max: 520, sortable: true, value: (record) => `${record.company} ${record.title}` },
    salary: { label: '薪资', width: 115, min: 90, max: 190, sortable: true, value: (record) => record.salaryMinK ?? -1 },
    city: { label: '城市', width: 105, min: 80, max: 180, sortable: true, value: recordCity },
    industry: { label: '行业', width: 140, min: 90, max: 260, sortable: true, value: (record) => record.industry || '' },
    experience: { label: '经验', width: 105, min: 80, max: 180, sortable: true, value: (record) => record.experience || '' },
    education: { label: '学历', width: 90, min: 75, max: 150, sortable: true, value: (record) => record.education || '' },
    hrActive: { label: 'HR 活跃', width: 110, min: 90, max: 180, sortable: true, value: (record) => record.hrActiveLevel || record.hrActive || 'unknown' },
    loggedAt: { label: '投递时间', width: 125, min: 105, max: 200, sortable: true, value: (record) => record.loggedAt || '' },
    score: { label: '匹配度', width: 105, min: 85, max: 160, sortable: true, value: (record) => Number(record.score ?? -1) },
    status: { label: '状态', width: 95, min: 80, max: 150, sortable: true, value: (record) => record.status || '' },
};

function orderedVisibleColumns() {
    return state.columnOrder.filter((key) => state.visibleColumns.has(key) && TABLE_COLUMNS[key]);
}

function sortRecords(records) {
    const definition = TABLE_COLUMNS[state.sort.key];
    if (!definition) return [...records];
    const direction = state.sort.direction === 'asc' ? 1 : -1;
    return [...records].sort((a, b) => {
        const left = definition.value(a), right = definition.value(b);
        if (typeof left === 'number' && typeof right === 'number') return (left - right) * direction;
        return String(left).localeCompare(String(right), 'zh-CN', { numeric: true }) * direction;
    });
}

function renderTableCell(key, record) {
    const datetime = formatDateTime(record.loggedAt);
    const score = Number.isFinite(Number(record.score)) ? Number(record.score) : null;
    if (key === 'company') return `<div class="company-cell"><span class="company-avatar" title="${escapeHtml(record.industry || '行业未采集')}">${industryIcon(record.industry)}</span><div><strong title="${escapeHtml(record.company)}">${escapeHtml(record.company)}</strong><span title="${escapeHtml(record.title)}">${escapeHtml(record.title)}</span></div></div>`;
    if (key === 'salary') return `<span class="salary-value">${escapeHtml(record.salary)}</span>`;
    if (key === 'city') return `<span class="cell-value" title="${escapeHtml(record.city || record.location || '待补充')}">${escapeHtml(record.city || record.location || '待补充')}</span>`;
    if (key === 'industry') return `<span class="cell-value ${record.industry ? '' : 'cell-muted'}" title="${escapeHtml(record.industry || '未采集')}">${escapeHtml(record.industry || '未采集')}</span>`;
    if (key === 'experience') return `<span class="cell-value ${record.experience ? '' : 'cell-muted'}">${escapeHtml(record.experience || '未采集')}</span>`;
    if (key === 'education') return `<span class="cell-value ${record.education ? '' : 'cell-muted'}">${escapeHtml(record.education || '未采集')}</span>`;
    if (key === 'hrActive') {
        const level = record.hrActiveLevel || 'unknown';
        return `<span class="hr-active-badge hr-${escapeHtml(level)}" title="${escapeHtml(record.hrActive || HR_ACTIVE_LABELS[level] || '未知')}">${escapeHtml(record.hrActive || HR_ACTIVE_LABELS[level] || '未知')}</span>`;
    }
    if (key === 'loggedAt') return `<div class="date-cell"><span>${datetime.date}</span><small>${datetime.time}</small></div>`;
    if (key === 'score') return score === null ? '<span class="cell-muted">—</span>' : `<span class="score-pill"><i><span style="width:${Math.min(100, score)}%"></span></i>${score}</span>`;
    if (key === 'status') return `<span class="status-pill status-${escapeHtml(record.status)}">${STATUS_LABELS[record.status] || '进行中'}</span>`;
    return '';
}

function renderTableStructure(pageRecords) {
    const columns = orderedVisibleColumns();
    const colgroup = [`<col data-column="select" style="width:42px">`, ...columns.map((key) => `<col data-column="${key}" style="width:${state.columnWidths[key] || TABLE_COLUMNS[key].width}px">`), '<col data-column="action" style="width:76px">'];
    $('#recordsColgroup').innerHTML = colgroup.join('');
    const currentIds = pageRecords.map((record) => record.id);
    const selectedOnPage = currentIds.filter((id) => state.selectedIds.has(id)).length;
    const checked = pageRecords.length && selectedOnPage === pageRecords.length;
    const headerColumns = columns.map((key) => {
        const definition = TABLE_COLUMNS[key];
        const indicator = state.sort.key === key ? `<span class="sort-indicator">${state.sort.direction === 'asc' ? '↑' : '↓'}</span>` : '';
        const sticky = key === 'company' ? 'records-table-company-sticky' : '';
        return `<th class="${definition.sortable ? 'sortable-header' : ''} ${sticky}" data-column="${key}" draggable="true"><div class="header-content">${definition.label}${indicator}</div><span class="col-resizer" data-resize-column="${key}"></span></th>`;
    }).join('');
    $('#recordsHead').innerHTML = `<tr><th data-column="select"><input id="selectAllRows" type="checkbox" ${checked ? 'checked' : ''} aria-label="选择当前页"></th>${headerColumns}<th data-column="action"><div class="header-content"></div></th></tr>`;
    const selectAll = $('#selectAllRows');
    if (selectAll) selectAll.indeterminate = selectedOnPage > 0 && selectedOnPage < pageRecords.length;
}

function renderRecords(records) {
    const sortedRecords = sortRecords(records);
    const pages = Math.max(1, Math.ceil(sortedRecords.length / state.pageSize)); state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * state.pageSize, pageRecords = sortedRecords.slice(start, start + state.pageSize);
    $('#recordSubtitle').textContent = `共 ${numberFormat.format(records.length)} 条记录`;
    renderTableStructure(pageRecords);
    const columns = orderedVisibleColumns();
    $('#recordsBody').innerHTML = pageRecords.map((record) => {
        const selected = state.selectedIds.has(record.id);
        const cells = columns.map((key) => `<td data-column="${key}" class="${key === 'company' ? 'records-table-company-sticky' : ''}">${renderTableCell(key, record)}</td>`).join('');
        const deleteDisabled = record.canDelete === false ? 'disabled' : '';
        const deleteTitle = record.canDelete === false ? '进行中的记录不能删除' : '删除记录';
        return `<tr class="${selected ? 'selected' : ''}" data-record-id="${escapeHtml(record.id)}"><td data-column="select"><input class="row-checkbox" type="checkbox" data-select-record="${escapeHtml(record.id)}" ${selected ? 'checked' : ''} aria-label="选择该记录"></td>${cells}<td data-column="action"><div class="row-actions"><button class="row-action" data-open-record="${escapeHtml(record.id)}" title="查看详情">›</button><button class="row-action row-delete" data-delete-record="${escapeHtml(record.id)}" title="${deleteTitle}" ${deleteDisabled}>×</button></div></td></tr>`;
    }).join('');
    $('#emptyState').hidden = records.length > 0;
    $('#pageInfo').textContent = records.length ? `显示 ${start + 1}–${Math.min(start + state.pageSize, records.length)} / ${records.length}` : '暂无记录';
    $('#prevPage').disabled = state.page <= 1; $('#nextPage').disabled = state.page >= pages;
    const visible = []; for (let page = Math.max(1, state.page - 2); page <= Math.min(pages, state.page + 2); page += 1) visible.push(page);
    $('#pageNumbers').innerHTML = visible.map((page) => `<button class="${page === state.page ? 'active' : ''}" data-page="${page}">${page}</button>`).join('');
    updateSelectionToolbar();
    initColumnResize();
}

function saveTablePreferences() {
    localStorage.setItem(TABLE_PREFS_KEY, JSON.stringify({
        version: 2,
        visibleColumns: [...state.visibleColumns],
        columnOrder: state.columnOrder,
        columnWidths: state.columnWidths,
        density: state.density,
    }));
}

function restoreTablePreferences() {
    try {
        const saved = JSON.parse(localStorage.getItem(TABLE_PREFS_KEY) || '{}');
        const validKeys = Object.keys(TABLE_COLUMNS);
        if (Array.isArray(saved.visibleColumns)) {
            state.visibleColumns = new Set(saved.visibleColumns.filter((key) => validKeys.includes(key)));
            state.visibleColumns.add('hrActive');
        }
        if (Array.isArray(saved.columnOrder)) {
            const order = saved.columnOrder.filter((key) => validKeys.includes(key));
            state.columnOrder = [...order, ...validKeys.filter((key) => !order.includes(key))];
        }
        if (saved.columnWidths && typeof saved.columnWidths === 'object') state.columnWidths = saved.columnWidths;
        if (['compact', 'default', 'comfortable'].includes(saved.density)) state.density = saved.density;
    } catch (_) { /* 使用默认表格设置 */ }
    $('#applications').dataset.density = state.density;
    $$('#densitySwitch button').forEach((button) => button.classList.toggle('active', button.dataset.density === state.density));
    renderColumnManager();
}

function renderColumnManager() {
    const menu = $('#columnManagerMenu');
    menu.replaceChildren();
    state.columnOrder.forEach((key) => {
        const label = document.createElement('label'), input = document.createElement('input'), text = document.createElement('span');
        input.type = 'checkbox'; input.checked = state.visibleColumns.has(key); input.dataset.toggleColumn = key; text.textContent = TABLE_COLUMNS[key].label;
        label.append(input, text); menu.appendChild(label);
    });
}

function updateSelectionToolbar() {
    const count = state.selectedIds.size;
    const deletableCount = state.records.filter((record) => state.selectedIds.has(record.id) && record.canDelete !== false).length;
    $('#selectedCount').textContent = `已选择 ${count} 项`;
    $('#exportSelected').disabled = count === 0;
    $('#deleteSelected').disabled = deletableCount === 0;
    $('#clearSelection').disabled = count === 0;
}

function initColumnResize() {
    $$('[data-resize-column]').forEach((resizer) => {
        resizer.addEventListener('pointerdown', (event) => {
            event.preventDefault(); event.stopPropagation();
            const key = resizer.dataset.resizeColumn, definition = TABLE_COLUMNS[key];
            const column = $(`#recordsColgroup col[data-column="${key}"]`);
            const startX = event.clientX, startWidth = column?.getBoundingClientRect().width || definition.width;
            document.body.classList.add('resizing-column');
            const move = (moveEvent) => {
                const width = Math.max(definition.min, Math.min(definition.max, startWidth + moveEvent.clientX - startX));
                state.columnWidths[key] = Math.round(width);
                if (column) column.style.width = `${width}px`;
            };
            const up = () => {
                document.body.classList.remove('resizing-column');
                document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up); saveTablePreferences();
            };
            document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
        });
        resizer.addEventListener('dblclick', (event) => {
            event.stopPropagation(); delete state.columnWidths[resizer.dataset.resizeColumn]; saveTablePreferences(); renderRecords(getFilteredRecords());
        });
    });
}

function bindTableInteractions() {
    const table = $('#recordsTable');
    table.addEventListener('click', (event) => {
        const deleteButton = event.target.closest('[data-delete-record]');
        if (deleteButton && !deleteButton.disabled) { deleteDeliveryRecords([deleteButton.dataset.deleteRecord]); return; }
        const openButton = event.target.closest('[data-open-record]');
        if (openButton) return openDrawer(openButton.dataset.openRecord);
        const checkbox = event.target.closest('[data-select-record]');
        if (checkbox) {
            checkbox.checked ? state.selectedIds.add(checkbox.dataset.selectRecord) : state.selectedIds.delete(checkbox.dataset.selectRecord);
            renderRecords(getFilteredRecords()); return;
        }
        if (event.target.id === 'selectAllRows') {
            const rows = [...$('#recordsBody').querySelectorAll('[data-record-id]')].map((row) => row.dataset.recordId);
            event.target.checked ? rows.forEach((id) => state.selectedIds.add(id)) : rows.forEach((id) => state.selectedIds.delete(id));
            renderRecords(getFilteredRecords()); return;
        }
        const header = event.target.closest('th.sortable-header');
        if (header && !event.target.closest('.col-resizer')) {
            const key = header.dataset.column;
            state.sort = { key, direction: state.sort.key === key && state.sort.direction === 'asc' ? 'desc' : 'asc' };
            state.page = 1; renderRecords(getFilteredRecords());
        }
    });
    table.addEventListener('dragstart', (event) => {
        const header = event.target.closest('th[data-column]');
        if (!header || !TABLE_COLUMNS[header.dataset.column]) return event.preventDefault();
        event.dataTransfer.setData('text/table-column', header.dataset.column); header.classList.add('drag-column');
    });
    table.addEventListener('dragend', () => $$('th.drag-column, th.column-drop-target').forEach((element) => element.classList.remove('drag-column', 'column-drop-target')));
    table.addEventListener('dragover', (event) => {
        const header = event.target.closest('th[data-column]'); if (!header || !TABLE_COLUMNS[header.dataset.column]) return;
        event.preventDefault(); $$('th.column-drop-target').forEach((element) => element.classList.remove('column-drop-target')); header.classList.add('column-drop-target');
    });
    table.addEventListener('drop', (event) => {
        const target = event.target.closest('th[data-column]'); const sourceKey = event.dataTransfer.getData('text/table-column');
        if (!target || !sourceKey || sourceKey === target.dataset.column) return;
        event.preventDefault(); const order = state.columnOrder.filter((key) => key !== sourceKey); order.splice(order.indexOf(target.dataset.column), 0, sourceKey); state.columnOrder = order; saveTablePreferences(); renderColumnManager(); renderRecords(getFilteredRecords());
    });
    $('#pageNumbers').addEventListener('click', (event) => { const button = event.target.closest('[data-page]'); if (!button) return; state.page = Number(button.dataset.page); renderRecords(getFilteredRecords()); });
}

function initReportDrag() {
    const canvas = $('#reportCanvas');
    const defaultOrder = [...canvas.querySelectorAll('[data-widget-id]')].map((card) => card.dataset.widgetId);
    const restore = () => {
        try {
            const order = JSON.parse(localStorage.getItem(REPORT_LAYOUT_KEY) || '[]');
            if (Array.isArray(order)) order.forEach((id) => { const card = canvas.querySelector(`[data-widget-id="${id}"]`); if (card) canvas.appendChild(card); });
        } catch (_) { /* 默认顺序 */ }
    };
    restore();
    canvas.querySelectorAll('.report-card').forEach((card) => {
        const head = card.querySelector('.panel-head');
        const handle = document.createElement('span'); handle.className = 'drag-handle'; handle.textContent = '⠿'; handle.title = '拖动调整报表位置';
        head.appendChild(handle);
        handle.addEventListener('mousedown', () => { card.draggable = true; });
        handle.addEventListener('mouseup', () => { card.draggable = false; });
        card.addEventListener('dragstart', (event) => { card.classList.add('dragging'); event.dataTransfer.setData('text/report-widget', card.dataset.widgetId); });
        card.addEventListener('dragend', () => { card.draggable = false; card.classList.remove('dragging'); canvas.querySelectorAll('.drag-over').forEach((item) => item.classList.remove('drag-over')); localStorage.setItem(REPORT_LAYOUT_KEY, JSON.stringify([...canvas.querySelectorAll('[data-widget-id]')].map((item) => item.dataset.widgetId))); });
        card.addEventListener('dragover', (event) => { event.preventDefault(); if (!card.classList.contains('dragging')) card.classList.add('drag-over'); });
        card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
        card.addEventListener('drop', (event) => {
            event.preventDefault(); card.classList.remove('drag-over'); const source = canvas.querySelector(`[data-widget-id="${event.dataTransfer.getData('text/report-widget')}"]`); if (!source || source === card) return;
            const rect = card.getBoundingClientRect(); const after = event.clientY > rect.top + rect.height / 2 || event.clientX > rect.left + rect.width / 2; canvas.insertBefore(source, after ? card.nextSibling : card);
        });
    });
    $('#resetReportLayout').addEventListener('click', () => { localStorage.removeItem(REPORT_LAYOUT_KEY); defaultOrder.forEach((id) => { const card = canvas.querySelector(`[data-widget-id="${id}"]`); if (card) canvas.appendChild(card); }); showToast('报表布局已恢复默认'); });
}

function applyChartFilter(patch) {
    Object.assign(state, patch, { page: 1 });
    $('#minSalaryFilter').value = state.minSalary ?? '';
    $('#maxSalaryFilter').value = state.maxSalary ?? '';
    updateDashboard();
}

function bindChartInteractions() {
    $('#reportCanvas').addEventListener('click', (event) => {
        const datePoint = event.target.closest('[data-date]');
        if (datePoint) return applyChartFilter({ exactDate: state.exactDate === datePoint.dataset.date ? '' : datePoint.dataset.date });
        const salary = event.target.closest('[data-salary-filter]');
        if (salary) {
            const range = salary.dataset.salaryFilter;
            return applyChartFilter({ salaryBucket: state.salaryBucket === range ? '' : range });
        }
        const industry = event.target.closest('[data-industry]');
        if (industry) return applyChartFilter({ industry: state.industry === industry.dataset.industry ? '' : industry.dataset.industry });
        const role = event.target.closest('[data-role]');
        if (role) return applyChartFilter({ role: state.role === role.dataset.role ? '' : role.dataset.role });
    });
}

function bindTrendInteractions() {
    const chart = $('#trendChart');
    const redraw = () => renderTrend(getFilteredRecords(true));
    $('#trendQuickRange').addEventListener('click', (event) => { const button = event.target.closest('[data-trend-range]'); if (button) setTrendRange(button.dataset.trendRange); });
    const applyCustomDates = () => { state.trendStart = $('#trendStartDate').value; state.trendEnd = $('#trendEndDate').value; if (state.trendStart && state.trendEnd && state.trendStart > state.trendEnd) [state.trendStart, state.trendEnd] = [state.trendEnd, state.trendStart]; state.trendRange = 'custom'; state.trendView.zoom = 1; state.trendView.offset = 0; state.trendView.autoWindow = true; $$('#trendQuickRange button').forEach((button) => button.classList.remove('active')); redraw(); };
    $('#trendStartDate').addEventListener('change', applyCustomDates); $('#trendEndDate').addEventListener('change', applyCustomDates);
    $('#trendScroll').addEventListener('input', (event) => { state.trendView.autoWindow = false; state.trendView.offset = Number(event.target.value); redraw(); });
    const zoom = (factor, anchor = .5) => { const oldZoom = state.trendView.zoom; const next = Math.max(1, Math.min(12, oldZoom * factor)); state.trendView.offset += (1 - oldZoom / next) * anchor * Math.max(0, Number($('#trendScroll').max)); state.trendView.zoom = next; state.trendView.autoWindow = false; redraw(); };
    $('#trendZoomIn').addEventListener('click', () => zoom(1.5)); $('#trendZoomOut').addEventListener('click', () => zoom(1 / 1.5));
    chart.addEventListener('wheel', (event) => { event.preventDefault(); const rect = chart.getBoundingClientRect(); zoom(event.deltaY < 0 ? 1.25 : .8, (event.clientX - rect.left) / rect.width); }, { passive: false });
    chart.addEventListener('pointerdown', (event) => { if (event.target.closest('.trend-dot') || Number($('#trendScroll').max) <= 0) return; state.trendView.autoWindow = false; state.trendView.dragging = true; state.trendView.startX = event.clientX; state.trendView.startOffset = state.trendView.offset; chart.setPointerCapture(event.pointerId); chart.classList.add('is-panning'); });
    chart.addEventListener('pointermove', (event) => { if (!state.trendView.dragging) return; const pixelsPerDay = Math.max(chart.clientWidth, 1) / Math.max(1, state.trendView.visibleCount - 1); state.trendView.offset = state.trendView.startOffset - (event.clientX - state.trendView.startX) / pixelsPerDay; redraw(); });
    const stop = () => { if (!state.trendView.dragging) return; state.trendView.dragging = false; state.trendView.offset = Math.round(state.trendView.offset); chart.classList.remove('is-panning'); redraw(); }; chart.addEventListener('pointerup', stop); chart.addEventListener('pointercancel', stop);
}

function initDatePicker() {
    const picker = document.createElement('div'); picker.className = 'gj-date-picker'; picker.hidden = true; picker.innerHTML = '<div class="gj-calendar-head"><button type="button" data-calendar-nav="-1" aria-label="上个月">‹</button><strong></strong><button type="button" data-calendar-nav="1" aria-label="下个月">›</button></div><div class="gj-calendar-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div><div class="gj-calendar-days"></div><div class="gj-calendar-foot"><button type="button" data-calendar-action="clear">清空</button><button type="button" data-calendar-action="today">今天</button></div>';
    document.body.appendChild(picker);
    let activeInput = null; let viewDate = new Date();
    const selectedDate = () => activeInput?.value ? new Date(`${activeInput.value}T00:00:00`) : null;
    const close = () => { picker.hidden = true; activeInput = null; };
    const position = () => { if (!activeInput || picker.hidden) return; const rect = activeInput.closest('.trend-date-field').getBoundingClientRect(); const width = 286; let left = Math.min(rect.left, window.innerWidth - width - 12); let top = rect.bottom + 8; if (top + 350 > window.innerHeight) top = Math.max(12, rect.top - 350); picker.style.left = `${Math.max(12, left)}px`; picker.style.top = `${top}px`; };
    const render = () => {
        const year = viewDate.getFullYear(), month = viewDate.getMonth(); picker.querySelector('.gj-calendar-head strong').textContent = `${year} 年 ${String(month + 1).padStart(2, '0')} 月`;
        const first = new Date(year, month, 1), offset = (first.getDay() + 6) % 7, start = new Date(year, month, 1 - offset), selected = selectedDate(), todayKey = localDateKey(new Date());
        picker.querySelector('.gj-calendar-days').innerHTML = Array.from({ length: 42 }, (_, index) => { const date = new Date(start); date.setDate(start.getDate() + index); const key = localDateKey(date); const outside = date.getMonth() !== month; return `<button type="button" data-calendar-date="${key}" class="${outside ? 'outside' : ''} ${key === todayKey ? 'today' : ''} ${selected && key === localDateKey(selected) ? 'selected' : ''}"><span>${date.getDate()}</span></button>`; }).join('');
    };
    const open = (input) => { activeInput = input; const parsed = selectedDate(); viewDate = parsed || new Date(); picker.hidden = false; render(); position(); };
    ['trendStartDate', 'trendEndDate'].forEach((id) => { const input = document.getElementById(id); input.addEventListener('click', () => open(input)); input.closest('.trend-date-field').addEventListener('click', () => open(input)); });
    picker.addEventListener('click', (event) => {
        const nav = event.target.closest('[data-calendar-nav]'); if (nav) { viewDate = new Date(viewDate.getFullYear(), viewDate.getMonth() + Number(nav.dataset.calendarNav), 1); render(); return; }
        const day = event.target.closest('[data-calendar-date]'); if (day && activeInput) { activeInput.value = day.dataset.calendarDate; activeInput.dispatchEvent(new Event('change', { bubbles: true })); close(); return; }
        const action = event.target.closest('[data-calendar-action]'); if (!action || !activeInput) return; activeInput.value = action.dataset.calendarAction === 'today' ? localDateKey(new Date()) : ''; activeInput.dispatchEvent(new Event('change', { bubbles: true })); close();
    });
    document.addEventListener('pointerdown', (event) => { if (!picker.hidden && !picker.contains(event.target) && !event.target.closest('.trend-date-field')) close(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') close(); }); window.addEventListener('resize', position); window.addEventListener('scroll', position, true);
}

function openDrawer(id) {
    const record = state.records.find((item) => item.id === id); if (!record) return;
    const datetime = formatDateTime(record.loggedAt);
    $('#drawerCompany').textContent = record.company; $('#drawerTitle').textContent = record.title; $('#drawerStatus').textContent = STATUS_LABELS[record.status] || '进行中';
    $('#drawerStatus').className = `drawer-status status-${record.status}`;
    const details = [['薪资范围', record.salary], ['城市 / 地区', record.city || record.location || '历史记录未采集'], ['经验要求', record.experience || '未采集'], ['学历要求', record.education || '未采集'], ['所属行业', record.industry || '未采集'], ['HR 活跃', record.hrActive || HR_ACTIVE_LABELS[record.hrActiveLevel] || '未知'], ['搜索关键词', record.keyword || '未记录'], ['投递日期', `${datetime.date} ${datetime.time}`], ['匹配度', record.score ?? '未记录'], ['投递账号', record.accountId || '默认账号']];
    $('#drawerDetails').innerHTML = details.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
    $('#detailDrawer').classList.add('open'); $('#drawerBackdrop').classList.add('open'); $('#detailDrawer').setAttribute('aria-hidden', 'false');
}

function closeDrawer() { $('#detailDrawer').classList.remove('open'); $('#drawerBackdrop').classList.remove('open'); $('#detailDrawer').setAttribute('aria-hidden', 'true'); }

function updateDashboard() {
    const records = getFilteredRecords();
    renderMetrics(records); renderTrend(getFilteredRecords(true)); renderFunnel(records); renderMap(getFilteredRecords(false, true)); renderSalary(records); renderRoles(records); renderIndustry(records); renderRecords(records); renderFilterChips(); updateMonitorMetrics();
}

function updateMonitorMetrics() {
    const today = localDateKey(new Date());
    const rangedRecords = getRangeRecords();
    const delivered = rangedRecords.length;
    const evaluated = evaluatedJobsForRange(state.payload?.summary || {}, state.range);
    $('#monitorTodayDelivered').textContent = state.records.filter((record) => record.loggedAt?.startsWith(today)).length;
    $('#filterDeliveryRate').textContent = evaluated > 0 ? `${Math.round(delivered / evaluated * 100)}%` : '—';
    $('#filterDeliveryRateHint').textContent = `已投递 ${numberFormat.format(delivered)} / 已评估 ${evaluated === null ? '—' : numberFormat.format(evaluated)}`;
}

function renderControlMonitorCounts() {
    const runtime = state.runtime || { clients: [], activeClientCount: 0, connectedClientCount: 0 };
    const control = state.control || {};
    const clients = controlArray(control.clients || control.instances).length ? controlArray(control.clients || control.instances) : controlArray(runtime.clients);
    const count = (value, fallback = 0) => { const parsed = Number(value); return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback; };
    const registered = count(control.registeredWorkerCount ?? control.registeredClientCount, count(runtime.connectedClientCount, clients.length));
    const connected = count(control.connectedClientCount ?? control.activeClientCount, count(runtime.activeClientCount, clients.filter((item) => item.online !== false).length));
    const running = count(control.runningClientCount, clients.filter((item) => item.online !== false && item.executionState === 'running').length);
    $('#activeClients').textContent = connected;
    $('#connectedClients').textContent = registered;
    $('#runningClients').textContent = running;
    $('#navControlAlerts').textContent = connected;
}

function renderRuntime() {
    const runtime = state.runtime || { clients: [], activeClientCount: 0, connectedClientCount: 0 };
    renderControlMonitorCounts();
    const ttl = Number(runtime.clientTtlSeconds || 30); $('#heartbeatWindowHint').textContent = ttl >= 60 ? `${Math.round(ttl / 60)} 分钟内有心跳` : `${ttl} 秒内有心跳`;
}

function eventPayload(event) {
    return event?.payload && typeof event.payload === 'object' ? event.payload : {};
}

function eventLogAccount(event) {
    const payload = eventPayload(event);
    return String(payload.accountId ?? event?.accountId ?? '').trim();
}

function eventLogSender(event) {
    const payload = eventPayload(event);
    const explicit = String(payload.sender ?? event?.sender ?? '').trim().toLowerCase();
    if (explicit === 'delivery_wait' || explicit === 'waiting') return 'queue';
    if (Object.prototype.hasOwnProperty.call(LOG_SENDER_LABELS, explicit)) return explicit;

    const type = String(event?.type || '').toLowerCase();
    const action = String(payload.action || '').toLowerCase();
    const runtimeState = String(payload.state || '').toLowerCase();
    if (action.includes('claim') || type.includes('claim') || runtimeState === 'claiming') return 'claim';
    if (action.includes('queue') || action.includes('queued') || type.includes('queue') || ['queued', 'reserved', 'pending', 'waiting'].includes(runtimeState)) return 'queue';
    if (type === 'job_action' || type.includes('delivery') || type.includes('greet') || type.includes('resume') || ['sent', 'delivered'].includes(runtimeState)) return 'delivery';
    return 'system';
}

function eventLogVerbosity(event) {
    const payload = eventPayload(event);
    const explicit = String(payload.verbosity ?? event?.verbosity ?? '').trim().toLowerCase();
    if (explicit === 'detail') return 'detailed';
    if (Object.prototype.hasOwnProperty.call(LOG_VERBOSITY_RANK, explicit)) return explicit;

    const type = String(event?.type || '').toLowerCase();
    const level = String(payload.level ?? event?.level ?? '').toLowerCase();
    const action = String(payload.action || '').toLowerCase();
    if (['error', 'fatal', 'warn', 'warning'].includes(level) || type === 'runtime_error' || ['client_connected', 'client_state_changed'].includes(type) || /(?:_sent|_failed|_error)$/.test(action)) return 'concise';
    return 'normal';
}

function eventMatchesLogFilters(event, filters = {}) {
    const account = filters.account || 'all';
    const sender = filters.sender || 'all';
    const verbosity = Object.prototype.hasOwnProperty.call(LOG_VERBOSITY_RANK, filters.verbosity) ? filters.verbosity : 'normal';
    if (account !== 'all' && eventLogAccount(event) !== account) return false;
    if (sender !== 'all' && eventLogSender(event) !== sender) return false;
    return LOG_VERBOSITY_RANK[eventLogVerbosity(event)] <= LOG_VERBOSITY_RANK[verbosity];
}

function eventLogLevel(event) {
    const payload = eventPayload(event);
    const level = String(payload.level ?? event?.level ?? '').toLowerCase();
    if (level === 'fatal' || level === 'error') return 'error';
    if (level === 'warn' || level === 'warning') return 'warning';
    if (event?.type === 'job_action') return String(payload.action || '').includes('fail') ? 'error' : 'action';
    return level === 'action' ? 'action' : 'info';
}

function eventMessage(event) {
    const payload = eventPayload(event);
    const account = eventLogAccount(event) || '全局';
    const sender = eventLogSender(event);
    const base = {
        account,
        level: eventLogLevel(event),
        sender,
        senderLabel: LOG_SENDER_LABELS[sender],
        source: account,
        time: payload.loggedAt || event?.loggedAt || '',
        verbosity: eventLogVerbosity(event),
    };
    if (event?.type === 'script_log') return { ...base, message: payload.message || '' };
    if (event?.type === 'job_action') return { ...base, message: `${payload.action || 'action'} ${payload.company || ''} ${payload.title || ''}`.trim() };
    return { ...base, message: payload.message || payload.phase || String(event?.type || 'system') };
}

function formatLogTime(value) {
    if (!value) return '--:--:--';
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    return String(value).match(/(?:T|\s)(\d{2}:\d{2}:\d{2})/)?.[1] || String(value).slice(0, 8);
}

function renderLiveLogs() {
    const container = $('#liveLogs'); container.replaceChildren();
    const accounts = [...new Set([...(state.runtime?.clients || []).map((client) => client.accountId), ...state.liveEvents.map(eventLogAccount)].filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    const accountFilter = $('#liveLogAccountFilter'); const currentAccount = state.logAccount;
    const accountOptions = [{ value: 'all', label: '全部账号' }, ...accounts.map((account) => ({ value: account, label: account }))].map(({ value, label }) => {
        const option = document.createElement('option'); option.value = value; option.textContent = label; return option;
    });
    accountFilter.replaceChildren(...accountOptions);
    accountFilter.value = accounts.includes(currentAccount) ? currentAccount : 'all'; state.logAccount = accountFilter.value;

    const availableEvents = state.liveEvents.filter((event) => (event.id || 0) > state.logClearedCursor);
    const matchedEvents = availableEvents.filter((event) => eventMatchesLogFilters(event, { account: state.logAccount, sender: state.logSender, verbosity: state.logVerbosity }));
    const visibleEvents = matchedEvents.slice(-160);
    $('#liveLogCount').textContent = `显示 ${visibleEvents.length} 条 · 匹配 ${matchedEvents.length} 条 · 共 ${availableEvents.length} 条`;
    if (!visibleEvents.length) {
        const empty = document.createElement('div'); empty.className = 'live-log-empty'; empty.textContent = availableEvents.length ? '暂无符合筛选条件的日志' : '暂无实时日志'; container.appendChild(empty);
    }
    visibleEvents.forEach((event) => {
        const entry = eventMessage(event), row = document.createElement('div'); row.className = `live-log ${entry.level}`; row.dataset.sender = entry.sender; row.dataset.verbosity = entry.verbosity;
        const time = document.createElement('time'); time.dateTime = entry.time; time.textContent = formatLogTime(entry.time);
        const account = document.createElement('b'); account.className = 'live-log-account'; account.textContent = entry.account; account.title = entry.account;
        const sender = document.createElement('span'); sender.className = `live-log-sender sender-${entry.sender}`; sender.textContent = entry.senderLabel;
        const message = document.createElement('span'); message.className = 'live-log-message'; message.textContent = entry.message;
        row.append(time, account, sender, message); container.appendChild(row);
    });
    if (!state.logsPaused) container.scrollTop = container.scrollHeight;
}

function setLogsPaused(paused) {
    state.logsPaused = Boolean(paused);
    const button = $('#pauseLogs');
    const label = state.logsPaused ? '继续自动滚动' : '暂停自动滚动';
    button.setAttribute('aria-pressed', String(state.logsPaused)); button.setAttribute('aria-label', label); button.title = label;
    $('#pauseLogsIcon').textContent = state.logsPaused ? '▶' : 'Ⅱ';
    if (!state.logsPaused) $('#liveLogs').scrollTop = $('#liveLogs').scrollHeight;
}

function setLogVerbosity(verbosity, shouldRender = true) {
    state.logVerbosity = Object.prototype.hasOwnProperty.call(LOG_VERBOSITY_RANK, verbosity) ? verbosity : 'normal';
    $$('[data-log-verbosity]').forEach((button) => { const active = button.dataset.logVerbosity === state.logVerbosity; button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active)); });
    if (shouldRender) renderLiveLogs();
}

function setLiveLogsExpanded(expanded) {
    state.logsExpanded = Boolean(expanded);
    const card = $('#liveLogCard');
    const panel = $('#liveLogPanel');
    const toggle = $('#liveLogToggle');
    card.classList.toggle('is-collapsed', !state.logsExpanded);
    panel.hidden = !state.logsExpanded;
    toggle.setAttribute('aria-expanded', String(state.logsExpanded));
    toggle.title = state.logsExpanded ? '收起实时日志' : '展开实时日志';
    $('#liveLogToggleText').textContent = state.logsExpanded ? '收起日志' : '展开日志';
    if (state.logsExpanded && !state.logsPaused) requestAnimationFrame(() => { $('#liveLogs').scrollTop = $('#liveLogs').scrollHeight; });
}

async function loadRuntime() {
    try {
        const response = await authorizedFetch('/api/runtime', { cache: 'no-store' }); if (!response.ok) return;
        const runtime = await response.json();
        const previousCursor = state.runtimeCursor;
        const incomingEvents = runtime.events || [];
        const newEvents = incomingEvents.filter((event) => (event.id || 0) > previousCursor);
        state.runtime = runtime;
        state.runtimeCursor = Math.max(previousCursor, runtime.cursor || 0);
        state.liveEvents = incomingEvents;
        renderRuntime();
        renderLiveLogs();
        if (newEvents.some((event) => event.type === 'job_action')) loadData({ silent: true });
    } catch (_) { /* 下一轮短轮询会自动重试 */ }
}

function connectRuntimeStream() {
    const wrapper = $('.control-sync'); if (!wrapper) return;
    wrapper.classList.add('connected');
}

function controlArray(value) { return Array.isArray(value) ? value : []; }
function controlText(value, fallback = '—') { return value === undefined || value === null || value === '' ? fallback : String(value); }
function controlTime(value) { const text = controlText(value, ''); return text.includes('T') ? text.replace('T', ' ').slice(0, 19) : text || '—'; }
function controlStars(value) { const count = Math.max(0, Math.min(5, Number(value) || 0)); return Array.from({ length: 5 }, (_, index) => `<span class="${index < count ? 'active' : ''}">★</span>`).join(''); }

function desiredStateOf(item) {
    const desiredState = String(item?.desiredState || 'stopped').toLowerCase();
    return Object.prototype.hasOwnProperty.call(DESIRED_STATE_LABELS, desiredState) ? desiredState : 'stopped';
}

function executionStateOf(item) {
    const executionState = String(item?.executionState || item?.phase || item?.state || '').toLowerCase();
    if (Object.prototype.hasOwnProperty.call(EXECUTION_STATE_LABELS, executionState)) return executionState;
    if (item?.paused === true) return 'paused';
    return desiredStateOf(item) === 'running' ? 'starting' : desiredStateOf(item);
}

function syncStateOf(item) {
    const syncState = String(item?.syncState || '').toLowerCase();
    if (Object.prototype.hasOwnProperty.call(SYNC_STATE_LABELS, syncState)) return syncState;
    if (item?.controlError || item?.lastControlError) return 'failed';
    const desiredState = desiredStateOf(item);
    const executionState = executionStateOf(item);
    const matches = executionState === desiredState;
    if (matches) return 'synced';
    return item?.online === false ? 'pending' : 'applying';
}

function lifecycleRequestKey(scope, workerId = '') { return scope === 'global' ? 'global' : `worker:${workerId}`; }

function lifecycleControlsLocked(scope, workerId = '') {
    const requests = state.lifecycleRequests || {};
    if (requests.global?.pending) return true;
    if (scope === 'global') {
        return Object.entries(requests).some(([key, request]) => key.startsWith('worker:') && request?.pending);
    }
    return Boolean(requests[lifecycleRequestKey(scope, workerId)]?.pending);
}

function lifecycleButtonsMarkup(scope, workerId, desiredState) {
    const pending = lifecycleControlsLocked(scope, workerId);
    const workerAttribute = scope === 'worker' ? ` data-worker-id="${escapeHtml(workerId)}"` : '';
    return [
        ['running', '▶', '开启', 'start'],
        ['paused', 'Ⅱ', '暂停', 'pause'],
        ['stopped', '■', '结束', 'danger'],
    ].map(([value, icon, label, className]) => `<button type="button" class="control-command ${className}${desiredState === value ? ' active' : ''}" data-control-scope="${scope}"${workerAttribute} data-desired-state="${value}" aria-pressed="${desiredState === value}"${pending ? ' disabled aria-busy="true"' : ''}><span aria-hidden="true">${icon}</span> ${label}</button>`).join('');
}

function controlAxesMarkup(item) {
    const online = item.online !== false;
    const executionState = executionStateOf(item);
    const syncState = syncStateOf(item);
    const syncLabel = !online && syncState === 'pending' ? '离线待执行' : SYNC_STATE_LABELS[syncState];
    return `<div class="control-state-axes" aria-label="实例状态"><span class="axis connection ${online ? 'connected' : 'offline'}"><i></i><small>连接</small><b>${online ? '已连接' : '离线'}</b></span><span class="axis execution ${executionState}"><i></i><small>执行</small><b>${EXECUTION_STATE_LABELS[executionState]}</b></span><span class="axis sync ${syncState}"><i></i><small>同步</small><b>${syncLabel}</b></span></div>`;
}

function accountQuotaForInstance(item, accounts) {
    const accountId = String(item?.accountId || '');
    if (!accountId) return null;
    return controlArray(accounts).find((account) => String(account?.accountId || '') === accountId) || { accountId };
}

function instanceQuotaMarkup(item, accounts) {
    const accountId = String(item?.accountId || '');
    if (!accountId) {
        return '<div class="control-instance-quota unavailable"><div><span>账号配额</span><small>设置账号标识后可配置今日上限</small></div></div>';
    }
    const quota = accountQuotaForInstance(item, accounts) || {};
    const used = Math.max(0, Number(quota.used ?? quota.count ?? 0) || 0);
    const rawLimit = Number(quota.limit ?? quota.dailyLimit ?? 90);
    const limit = Number.isInteger(rawLimit)
        ? Math.min(ACCOUNT_DAILY_LIMIT_MAX, Math.max(ACCOUNT_DAILY_LIMIT_MIN, rawLimit))
        : 90;
    const remaining = Math.max(0, limit - used);
    const reached = used >= limit;
    return `<div class="control-instance-quota${reached ? ' reached' : ''}" data-account-quota="${escapeHtml(accountId)}"><div class="control-instance-quota-summary"><span>账号配额</span><strong>${used}<small> / ${limit}</small></strong><em>${reached ? '今日已达上限' : `今日剩余 ${remaining}`}</em></div><div class="control-instance-quota-editor"><label><span>今日上限</span><input type="number" min="${ACCOUNT_DAILY_LIMIT_MIN}" max="${ACCOUNT_DAILY_LIMIT_MAX}" step="1" inputmode="numeric" value="${limit}" data-account-limit="${escapeHtml(accountId)}" aria-label="${escapeHtml(accountId)} 今日投递上限"></label><button type="button" data-control-action="save_account_limit" data-command-value="${escapeHtml(accountId)}">保存</button></div></div>`;
}

function normalizedControlState() {
    const data = state.control || {};
    const runtime = state.runtime || {};
    const global = data.global || data.safety || data.settings || {};
    const task = data.task || data.plan || data.progress || {};
    const instances = controlArray(data.instances || data.clients).length ? controlArray(data.instances || data.clients) : controlArray(runtime.clients);
    const quotaMap = data.quotas && !Array.isArray(data.quotas) ? data.quotas : {};
    const policyMap = data.accounts && !Array.isArray(data.accounts) ? data.accounts : {};
    const accountIds = [...new Set([...Object.keys(quotaMap), ...Object.keys(policyMap), ...instances.map((item) => item.accountId).filter(Boolean)])];
    const accounts = Array.isArray(data.accounts) ? data.accounts : accountIds.map((accountId) => ({ accountId, ...(quotaMap[accountId] || {}), ...(policyMap[accountId] || {}) }));
    const queue = controlArray(data.queue || data.jobs).length ? controlArray(data.queue || data.jobs) : instances.flatMap((item) => controlArray(item.queue).map((entry) => ({ workerId: item.workerId, ...entry })));
    const currentKeywords = [...new Set(instances.map((item) => item.keyword).filter(Boolean))];
    return {
        global,
        task,
        instances,
        registeredWorkerCount: Number(data.registeredWorkerCount ?? data.registeredClientCount ?? instances.length),
        connectedClientCount: Number(data.connectedClientCount ?? data.activeClientCount ?? instances.filter((item) => item.online !== false).length),
        runningClientCount: Number(data.runningClientCount ?? instances.filter((item) => item.online !== false && executionStateOf(item) === 'running').length),
        controlEpoch: data.controlEpoch || '',
        revision: Number(data.revision || 0),
        queue,
        keywords: data.keywords || { items: currentKeywords, current: currentKeywords[0] || '' },
        filters: data.filters || {},
        accounts,
        errors: controlArray(data.errors || data.incidents),
        timeline: (controlArray(data.timeline || data.events).length ? controlArray(data.timeline || data.events) : controlArray(runtime.events)).map((event) => { if (!event.payload) return event; const entry = eventMessage(event), type = entry.level === 'error' ? 'error' : (event.type?.includes('job') ? 'delivery' : event.type?.includes('deduct') ? 'deduction' : 'system'); return { ...entry, type }; }),
        audit: controlArray(data.audit || data.commands || data.commandHistory),
        health: data.health || data.services || {},
    };
}

function decisionMarkup(decision) {
    if (!decision || !Object.keys(decision).length) {
        return '<div class="instance-decision empty">暂无正在评分的岗位</div>';
    }
    const stars = Number(decision.stars ?? decision.remainingStars ?? 5);
    const deductions = controlArray(decision.deductions || decision.matches);
    const scoringEnabled = decision.scoringEnabled !== false;
    const deductionRows = deductions.length
        ? deductions.map((item) => `<div><span>${escapeHtml(item.keyword || item.name || '规则命中')}</span><b>−${Number(item.stars ?? item.deductStars ?? item.value ?? 1)} 星</b></div>`).join('')
        : `<div><span>${scoringEnabled ? '未命中扣星规则' : '扣分规则已关闭'}</span><b>0 星</b></div>`;
    const aiEnabled = decision.aiFilterEnabled === true || decision.aiFilterEnabled === 1;
    const aiKnown = decision.aiPassed !== undefined && decision.aiPassed !== null;
    const passed = decision.aiPassed === true || decision.aiPassed === 1;
    const aiLabel = !aiEnabled ? 'AI 未启用' : (aiKnown ? (passed ? 'AI 通过' : 'AI 不通过') : 'AI 评估中');
    const aiReason = decision.aiReason ? `：${escapeHtml(decision.aiReason)}` : '';
    const aiRow = `<div class="decision-ai ${!aiEnabled || !aiKnown ? 'neutral' : (passed ? 'pass' : 'fail')}"><span>${aiLabel}</span><em>${aiReason}</em></div>`;
    const activeLevel = decision.hrActiveLevel || 'unknown';
    const hrLabel = decision.hrActive || HR_ACTIVE_LABELS[activeLevel] || '未知';
    const hrPassed = decision.hrActivePassed === false ? '未达标' : (decision.hrActivePassed === true ? '已达标' : '默认放行');
    const stateLabel = decision.decisionState || decision.state || (decision.discarded ? '准备丢弃' : '评分完成');
    const verdict = DECISION_STATE_LABELS[stateLabel] || stateLabel;
    const greeting = decision.greetingMode ? `<span class="decision-greeting">${escapeHtml(GREETING_MODE_LABELS[decision.greetingMode] || decision.greetingMode)}</span>` : '';
    return `<div class="instance-decision"><div class="instance-decision-head"><span class="decision-company">${escapeHtml(decision.company || '公司未识别')}</span><span class="decision-verdict">${escapeHtml(verdict)}</span></div><div class="decision-title">${escapeHtml(decision.title || '岗位未识别')}</div><div class="decision-stars" aria-label="剩余 ${stars} 星">${controlStars(stars)}</div><div class="decision-hr"><span>HR ${escapeHtml(hrLabel)}</span><em>${hrPassed}</em></div>${aiRow}${greeting}<div class="decision-deductions">${deductionRows}</div><div class="decision-final">最终决策：<b>${escapeHtml(decision.finalPassed === false ? '不投递' : decision.finalPassed === true ? '允许投递' : verdict)}</b>${decision.decisionReason ? `<small>${escapeHtml(decision.decisionReason)}</small>` : ''}</div></div>`;
}

function renderControlInstances(instances, accounts) {
    const container = $('#controlInstanceList'); container.replaceChildren();
    const online = instances.filter((item) => item.online !== false).length;
    $('#controlInstanceSummary').textContent = instances.length ? `${online} 个在线 / ${instances.length} 个实例` : '等待脚本接入';
    if (!instances.length) { container.innerHTML = '<div class="control-empty">尚未收到浏览器实例心跳</div>'; return; }
    instances.forEach((item) => {
        const workerId = item.workerId || item.id || '';
        const desiredState = desiredStateOf(item);
        const executionState = executionStateOf(item);
        const syncState = syncStateOf(item);
        const request = state.lifecycleRequests[lifecycleRequestKey('worker', workerId)];
        const feedback = request?.pending
            ? request.submitted
                ? `“${DESIRED_STATE_LABELS[request.desiredState]}”指令已提交，等待浏览器接收…`
                : `正在提交“${DESIRED_STATE_LABELS[request.desiredState]}”指令…`
            : request?.failed
                ? `操作失败：${escapeHtml(request.message || '请求未完成')}，可直接重试`
                : request?.timedOut
                    ? '指令已保存，浏览器暂未回执'
                    : request?.delivered && syncState === 'applying'
                        ? `浏览器已接收“${DESIRED_STATE_LABELS[request.desiredState]}”指令，正在应用…`
                : `期望状态：${DESIRED_STATE_LABELS[desiredState]} · ${SYNC_STATE_LABELS[syncState]}${item.revision ? ` · r${Number(item.revision)}` : ''}`;
        const card = document.createElement('div');
        card.className = `control-instance-card ${item.online !== false ? 'online' : 'offline'} sync-${syncState}`;
        card.innerHTML = `<div class="control-instance-top"><div class="control-instance-name"><i></i><strong>${escapeHtml(item.alias || item.accountId || workerId || '未命名实例')}</strong><small>${escapeHtml(workerId || '未登记标识')}</small></div><span class="control-instance-state ${executionState}">${EXECUTION_STATE_LABELS[executionState]}</span></div>${controlAxesMarkup(item)}<div class="control-instance-detail"><span>账号标识<b>${escapeHtml(item.accountId || '—')}</b></span><span>当前关键词<b>${escapeHtml(item.keyword || '—')}</b></span><span>当前岗位<b>${escapeHtml(item.currentJob || item.title || '—')}</b></span><span>今日投递<b>${Number(item.todayDelivered ?? item.counters?.sent ?? 0)}</b></span><span>最后心跳<b>${escapeHtml(controlTime(item.lastSeen || item.lastHeartbeatAt || item.updatedAt).slice(11) || '—')}</b></span><span>操作编号<b>${escapeHtml(item.operationId || '—')}</b></span></div>${instanceQuotaMarkup(item, accounts)}<div class="control-instance-actions" role="group" aria-label="${escapeHtml(item.alias || item.accountId || workerId || '实例')}生命周期">${lifecycleButtonsMarkup('worker', workerId, desiredState)}</div><div class="control-action-feedback ${request?.failed || syncState === 'failed' ? 'failed' : ''}" aria-live="polite">${feedback}</div>${decisionMarkup(item.currentDecision)}`;
        container.appendChild(card);
    });
}

function renderGlobalLifecycle(instances) {
    const request = state.lifecycleRequests.global;
    const controlsLocked = lifecycleControlsLocked('global');
    const desiredStates = [...new Set(instances.map(desiredStateOf))];
    const desiredState = desiredStates.length === 1 ? desiredStates[0] : '';
    const syncStates = instances.map(syncStateOf);
    const aggregateSync = syncStates.includes('failed') ? 'failed' : syncStates.includes('applying') ? 'applying' : syncStates.includes('pending') ? 'pending' : 'synced';
    const status = $('#globalControlStatus');
    status.className = `control-global-status ${desiredState ? `desired-${desiredState}` : 'desired-mixed'} sync-${aggregateSync}${request?.pending ? ' pending' : ''}${request?.failed ? ' failed' : ''}`;
    $('#globalDesiredState').textContent = !instances.length
        ? '全局状态：等待实例接入'
        : desiredState
            ? `全局期望：${DESIRED_STATE_LABELS[desiredState]}`
            : '全局期望：实例状态不一致';
    const syncedCount = syncStates.filter((value) => value === 'synced').length;
    $('#globalControlFeedback').textContent = request?.pending
        ? request.submitted
            ? `“${DESIRED_STATE_LABELS[request.desiredState]}全部”指令已提交，等待浏览器接收…`
            : `正在提交“${DESIRED_STATE_LABELS[request.desiredState]}全部”指令…`
        : request?.failed
            ? `操作失败：${request.message || '请求未完成'}，可直接重试`
            : request?.timedOut
                ? '指令已保存，部分浏览器暂未回执'
            : instances.length
                ? `${syncedCount} / ${instances.length} 个实例已同步${aggregateSync === 'failed' ? '，存在失败实例' : ''}`
                : '操作会应用到当前已登记的全部实例';
    $$('.control-global-bar [data-desired-state]').forEach((button) => {
        button.disabled = controlsLocked;
        button.setAttribute('aria-busy', String(controlsLocked));
        const active = Boolean(desiredState && button.dataset.desiredState === desiredState);
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
    });
}

function renderControlCenter() {
    const data = normalizedControlState();
    renderGlobalLifecycle(data.instances); renderControlInstances(data.instances, data.accounts); renderControlMonitorCounts();
    renderDailyGoal();
}

async function loadControlState() {
    if (controlStateLoadPromise) return controlStateLoadPromise;
    controlStateLoadPromise = (async () => {
        try {
            const data = await apiJson('/api/control/state'); state.control = data; state.controlOnline = true;
            $('.control-sync').className = 'control-sync connected'; $('#controlSyncText').textContent = '控制服务已连接'; $('#controlUpdatedAt').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
        } catch (_) {
            state.controlOnline = false; $('.control-sync').className = 'control-sync disconnected'; $('#controlSyncText').textContent = '控制接口暂不可用'; $('#controlUpdatedAt').textContent = '监控数据仍会继续显示';
        }
        renderControlCenter();
        return state.control;
    })();
    try {
        return await controlStateLoadPromise;
    } finally {
        controlStateLoadPromise = null;
    }
}

async function updateControlResource(url, payload, successMessage) {
    try { const result = await apiJson(url, { method: 'PUT', body: JSON.stringify(payload) }); showToast(successMessage); await loadControlState(); return result; }
    catch (error) { showToast(`保存失败：${error.message}`); return null; }
}

function lifecycleDeliveryObservation(scope, workerId, operation) {
    const targetCount = Math.max(0, Number(operation?.targetCount || 0));
    if (targetCount === 0) return { status: 'delivered', offline: true };
    const operationId = String(operation?.operationId || '');
    const revision = Number(operation?.revision || 0);
    const instances = normalizedControlState().instances;
    const targets = scope === 'global'
        ? instances.filter((item) => item.operationId === operationId && Number(item.revision || 0) === revision)
        : instances.filter((item) => (item.workerId || item.id || '') === workerId
            && item.operationId === operationId
            && Number(item.revision || 0) === revision);
    if (targets.length < targetCount) return { status: 'waiting' };
    if (targets.some((item) => syncStateOf(item) === 'failed')) {
        return { status: 'failed', message: '浏览器应用指令失败' };
    }
    const waitingOnline = targets.some((item) => item.online !== false && syncStateOf(item) === 'pending');
    if (waitingOnline) return { status: 'waiting' };
    return {
        status: 'delivered',
        offline: targets.every((item) => item.online === false),
    };
}

async function reconcileLifecycleDelivery(scope, workerId, operation) {
    const deadline = Date.now() + CONTROL_DELIVERY_TIMEOUT_MS;
    do {
        await loadControlState();
        const observation = lifecycleDeliveryObservation(scope, workerId, operation);
        if (observation.status !== 'waiting') return observation;
        await new Promise((resolve) => setTimeout(resolve, CONTROL_DELIVERY_POLL_INTERVAL_MS));
    } while (Date.now() < deadline);
    return { status: 'timeout' };
}

async function setDesiredLifecycleState(scope, workerId, desiredState) {
    if (!Object.prototype.hasOwnProperty.call(DESIRED_STATE_LABELS, desiredState)) return null;
    const key = lifecycleRequestKey(scope, workerId);
    if (lifecycleControlsLocked(scope, workerId)) {
        showToast('控制请求进行中，请稍后再试');
        return null;
    }
    state.lifecycleRequests[key] = { pending: true, failed: false, desiredState, message: '' };
    renderControlCenter();
    const url = scope === 'global'
        ? '/api/control/desired-state/global'
        : `/api/control/desired-state/workers/${encodeURIComponent(workerId)}`;
    try {
        const result = await apiJson(url, { method: 'PUT', body: JSON.stringify({ desiredState }) });
        state.lifecycleRequests[key] = {
            pending: true,
            submitted: true,
            failed: false,
            desiredState,
            operationId: result.operationId || '',
            revision: Number(result.revision || 0),
        };
        renderControlCenter();
        showToast(`${scope === 'global' ? '全部实例' : '实例'}指令已提交，等待浏览器接收`);
        const delivery = await reconcileLifecycleDelivery(scope, workerId, result);
        const current = state.lifecycleRequests[key];
        if (!current || current.operationId !== result.operationId) return result;
        if (delivery.status === 'failed') {
            state.lifecycleRequests[key] = { ...current, pending: false, failed: true, message: delivery.message };
            showToast(`控制失败：${delivery.message}`);
        } else {
            state.lifecycleRequests[key] = {
                ...current,
                pending: false,
                failed: false,
                delivered: delivery.status === 'delivered',
                timedOut: delivery.status === 'timeout',
            };
            const message = delivery.status === 'timeout'
                ? '指令已保存，浏览器暂未回执'
                : delivery.offline
                    ? '离线实例期望状态已保存'
                    : `${scope === 'global' ? '浏览器实例' : '浏览器'}已接收“${DESIRED_STATE_LABELS[desiredState]}”指令`;
            showToast(message);
        }
        renderControlCenter();
        return result;
    } catch (error) {
        state.lifecycleRequests[key] = { pending: false, failed: true, desiredState, message: error.message };
        renderControlCenter();
        showToast(`控制失败：${error.message}`);
        return null;
    }
}

function closeStopAllConfirm(confirmed = false) {
    const overlay = $('#controlStopConfirm');
    overlay.classList.remove('open');
    setTimeout(() => { overlay.hidden = true; }, 180);
    const resolve = pendingStopAllConfirmation;
    pendingStopAllConfirmation = null;
    if (resolve) resolve(confirmed);
}

function confirmStopAll() {
    if (pendingStopAllConfirmation) return Promise.resolve(false);
    const overlay = $('#controlStopConfirm');
    overlay.hidden = false;
    requestAnimationFrame(() => { overlay.classList.add('open'); $('#controlStopCancel').focus(); });
    return new Promise((resolve) => { pendingStopAllConfirmation = resolve; });
}

function showToast(message) { const toast = $('#toast'); toast.textContent = message; toast.classList.add('show'); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.remove('show'), 2600); }

function closeDeleteConfirm(confirmed = false) {
    const overlay = $('#deleteConfirm');
    overlay.classList.remove('open');
    const resolve = pendingDeleteConfirmation;
    pendingDeleteConfirmation = null;
    setTimeout(() => { overlay.hidden = true; }, 180);
    if (resolve) resolve(confirmed);
}

function confirmDeliveryDeletion(records, skippedCount = 0) {
    $('#deleteConfirmTitle').textContent = '确认删除投递记录？';
    const ordinary = records.filter((record) => record.status !== 'duplicate').length;
    const duplicates = records.length - ordinary;
    const parts = [];
    if (ordinary) parts.push(`${ordinary} 条正式投递记录`);
    if (duplicates) parts.push(`${duplicates} 条重复投递提示`);
    $('#deleteConfirmMessage').textContent = `即将删除 ${parts.join('和')}。${skippedCount ? `另有 ${skippedCount} 条进行中的记录将保留。` : ''}`;
    $('#deleteConfirmWarning').textContent = ordinary
        ? '正式投递删除后，将解除对应公司与岗位的重复拦截；今日投递额度不会返还。此操作无法撤销。'
        : '这里只删除重复投递提示，不会删除原始投递记录，也不会解除原岗位的重复拦截。';
    const overlay = $('#deleteConfirm');
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add('open'));
    return new Promise((resolve) => { pendingDeleteConfirmation = resolve; });
}

async function deleteDeliveryRecords(ids) {
    const requested = new Set(ids);
    const selected = state.records.filter((record) => requested.has(record.id));
    const deletable = selected.filter((record) => record.canDelete !== false);
    const skippedCount = selected.length - deletable.length;
    if (!deletable.length) return showToast('进行中的投递记录不能删除');
    if (!await confirmDeliveryDeletion(deletable, skippedCount)) return;
    const button = $('#deleteSelected');
    button.disabled = true;
    try {
        const result = await apiJson('/api/admin/deliveries/delete', {
            method: 'POST',
            body: JSON.stringify({ ids: deletable.map((record) => record.id) }),
        });
        deletable.forEach((record) => state.selectedIds.delete(record.id));
        await loadData({ silent: true });
        showToast(`已删除 ${result.deleted || deletable.length} 条投递记录${skippedCount ? `，保留 ${skippedCount} 条进行中记录` : ''}`);
    } catch (error) {
        showToast(`删除失败：${error.message}`);
    } finally {
        updateSelectionToolbar();
    }
}

function applyTheme(theme, persist = true) {
    const normalized = theme === 'light' ? 'light' : 'dark';
    document.documentElement.dataset.theme = normalized;
    if (persist) localStorage.setItem('goodjobs.dashboard.theme', normalized);
    const button = $('#themeToggle');
    if (button) {
        button.textContent = normalized === 'dark' ? '☀' : '☾';
        button.title = normalized === 'dark' ? '切换白天主题' : '切换深蓝主题';
        button.setAttribute('aria-label', button.title);
    }
    if (state.payload) renderMap(getFilteredRecords());
}

function toggleTheme() {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(next);
    showToast(next === 'light' ? '已切换白天主题' : '已切换深蓝主题');
}

function exportRecords(records, filenamePrefix = '投递报表') {
    const rows = [['公司', '岗位', '行业', '薪资', '最低K', '最高K', '城市', '经验', '学历', 'HR 活跃', '关键词', '投递时间', '匹配度', '状态', '账号']];
    records.forEach((record) => rows.push([record.company, record.title, record.industry, record.salary, record.salaryMinK ?? '', record.salaryMaxK ?? '', record.city || record.location, record.experience, record.education, record.hrActive || HR_ACTIVE_LABELS[record.hrActiveLevel] || '', record.keyword, record.loggedAt, record.score ?? '', STATUS_LABELS[record.status] || record.status, record.accountId]));
    const csv = '\ufeff' + rows.map((row) => row.map((cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`).join(',')).join('\r\n');
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })), link = document.createElement('a'); link.href = url; link.download = `${filenamePrefix}_${localDateKey(new Date())}.csv`; link.click(); URL.revokeObjectURL(url); showToast(`已导出 ${records.length} 条记录`);
}

function exportCsv() { exportRecords(getFilteredRecords()); }

function markDataConnection(ok) {
    const status = $('.sidebar-status'); if (!status) return;
    if (ok) {
        state.dataFailures = 0;
        status.classList.remove('offline');
        $('#connectionText').textContent = '数据服务正常';
        return;
    }
    state.dataFailures = (state.dataFailures || 0) + 1;
    if (state.dataFailures >= 2) {
        status.classList.add('offline');
        $('#connectionText').textContent = '数据连接中断';
        $('#lastUpdated').textContent = `已重试 ${state.dataFailures} 次，展示的是最后一次数据`;
    }
}

async function loadData({ silent = false } = {}) {
    $('#refreshButton').classList.add('loading');
    try {
        const response = await authorizedFetch('/api/dashboard', { cache: 'no-store' }); if (!response.ok) throw new Error(`HTTP ${response.status}`);
        state.payload = await response.json(); state.records = Array.isArray(state.payload.deliveries) ? state.payload.deliveries : [];
        const existingIds = new Set(state.records.map((record) => record.id)); state.selectedIds = new Set([...state.selectedIds].filter((id) => existingIds.has(id)));
        const generated = parseDate(state.payload.generatedAt); $('#lastUpdated').textContent = generated ? `${String(generated.getHours()).padStart(2, '0')}:${String(generated.getMinutes()).padStart(2, '0')} 已同步` : '已同步'; $('#footerTime').textContent = `最后更新 ${state.payload.generatedAt || '—'}`;
        populateFilters(); updateDashboard(); if (!silent) showToast('数据已刷新');
        markDataConnection(true);
    } catch (error) { console.error(error); markDataConnection(false); if (!state.records.length) { state.payload = { summary: {}, deliveries: [] }; state.records = []; populateFilters(); updateDashboard(); } if (!silent) showToast('无法读取统计数据'); }
    finally { $('#refreshButton').classList.remove('loading'); }
}

async function apiJson(url, options = {}) {
    const response = await authorizedFetch(url, { cache: 'no-store', ...options, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
}

const CONFIG_LABELS = {
    llm_greeting_enabled: '使用 LLM 生成打招呼语', scoring_enabled: '启用岗位扣星规则', introduce: '固定打招呼语', character: '回复风格', tags: '搜索关键词',
    backend: '后端参数', job_score_delay_base_ms: '评分基础延迟（ms）', job_score_delay_jitter_ms: '评分随机延迟（ms）', daily_greet_limit: '每日投递上限', delivery_db_path: '投递数据库文件',
    frontend: '浏览器脚本参数', resumeIndex: 'BOSS 发送简历序号', thread: '匹配阈值', timestampTimeout: '页面通信有效期（ms）', onlyGreet: '仅自动打招呼', roundRestartDelayMs: '轮次重启等待（ms）', maxEmptyRounds: '最大连续空轮', detailTimeout: '职位详情超时（ms）', greetTimeout: '打招呼超时（ms）', preloadScrollPixels: '预加载滚动距离（px）', preloadScrollWaitMs: '预加载滚动等待（ms）', preloadStableRoundsLimit: '预加载稳定轮数', preloadMaxRounds: '预加载最大轮数', preloadActivateCardEvery: '每隔几轮激活岗位卡', preloadActivateCardWaitMs: '激活岗位卡等待（ms）',
    antiDetectionEnabled: '启用防检测随机化', shuffleJobOrder: '打乱岗位投递顺序', randomSkipRatio: '随机跳过达标岗位（%）', randomNoIntroduceRatio: '随机不带招呼语（%）', randomDelayMinMs: '投递随机延时下限（ms）', randomDelayMaxMs: '投递随机延时上限（ms）',
    hrActiveFilterEnabled: '启用 HR 活跃筛选', hrActiveLevels: 'HR 活跃状态',
    scoring: '岗位扣星规则', title_deduction_keywords: '职位名称扣星词', detail_deduction_keywords: '职位描述扣星词'
};

const CONFIG_TAG_LIMIT = 80;
const CONFIG_TAG_MAX_LENGTH = 80;
const HIDDEN_CONFIG_PATHS = new Set(['frontend.serverHost']);
const CONFIG_ENUM_OPTIONS = {};
const CONFIG_MULTI_OPTIONS = {
    hrActiveLevels: [['online', '当前在线'], ['just_now', '刚刚活跃'], ['today', '今日活跃'], ['within_3_days', '3 日内活跃'], ['this_week', '本周活跃'], ['this_month', '本月活跃']],
};

function configLabel(key) { return CONFIG_LABELS[key] || key; }

function normalizeConfigTag(value) { return String(value || '').trim().toLocaleLowerCase(); }

function refreshAdminSaveState(tab = $('#adminTabs .active')?.dataset.adminTab || 'config') {
    const badge = $('#adminSaveState');
    let text = '配置已载入'; let pending = false;
    if (tab === 'config') { pending = state.configDirty; text = pending ? '参数配置待保存' : '参数配置已载入'; }
    else if (tab === 'llm') { pending = state.llmDirty; text = pending ? '接口配置待保存' : '接口配置已载入'; }
    else if (tab === 'resume') text = state.currentResume ? `当前简历 · ${state.currentResume}` : '暂无简历';
    else if (tab === 'prompts') text = state.currentPrompt ? `当前提示词 · ${state.currentPrompt}` : '提示词已载入';
    badge.textContent = text; badge.classList.toggle('pending', pending);
}

function markConfigDirty() {
    state.configDirty = true;
    if ($('#adminTabs .active')?.dataset.adminTab === 'config') refreshAdminSaveState('config');
}

function renderTagEditor(path, values) {
    const editor = document.createElement('div'); editor.className = 'config-tags-field'; editor.dataset.configPath = path; editor.dataset.valueType = 'tag-cards';
    const heading = document.createElement('div'); heading.className = 'config-tags-heading';
    const idPrefix = `config-${path.replace(/[^a-z0-9_-]/gi, '-')}`;
    const title = document.createElement('span'); title.id = `${idPrefix}-label`; title.textContent = configLabel('tags');
    const count = document.createElement('small'); count.id = `${idPrefix}-status`; count.setAttribute('role', 'status'); count.setAttribute('aria-live', 'polite'); count.setAttribute('aria-atomic', 'true'); heading.append(title, count);
    const grid = document.createElement('div'); grid.id = `${idPrefix}-list`; grid.className = 'config-tag-grid'; grid.setAttribute('role', 'list'); grid.setAttribute('aria-label', '搜索关键词列表');
    const addButton = document.createElement('button'); addButton.type = 'button'; addButton.className = 'config-tag-add'; addButton.innerHTML = '<span aria-hidden="true">＋</span> 新增关键词';
    addButton.setAttribute('aria-controls', grid.id); addButton.setAttribute('aria-describedby', count.id);
    editor.setAttribute('role', 'group'); editor.setAttribute('aria-labelledby', title.id); editor.setAttribute('aria-describedby', count.id);
    const update = () => {
        const cards = [...grid.querySelectorAll('.config-tag-card')];
        const normalizedCounts = new Map();
        cards.forEach((card) => { const normalized = normalizeConfigTag(card.querySelector('input').value); if (normalized) normalizedCounts.set(normalized, (normalizedCounts.get(normalized) || 0) + 1); });
        let emptyCount = 0; let duplicateCount = 0; let tooLongCount = 0;
        cards.forEach((card, index) => {
            const input = card.querySelector('input'); const remove = card.querySelector('button'); const value = input.value.trim(); const normalized = normalizeConfigTag(value);
            const empty = !value; const duplicate = Boolean(normalized && normalizedCounts.get(normalized) > 1); const tooLong = [...value].length > CONFIG_TAG_MAX_LENGTH; const invalid = empty || duplicate || tooLong;
            if (empty) emptyCount += 1; if (duplicate) duplicateCount += 1; if (tooLong) tooLongCount += 1;
            card.classList.toggle('empty', empty); card.classList.toggle('duplicate', duplicate); card.classList.toggle('too-long', tooLong); card.classList.toggle('invalid', invalid);
            input.setAttribute('aria-label', `搜索关键词 ${index + 1}`); input.setAttribute('aria-describedby', count.id); input.setAttribute('aria-invalid', String(invalid));
            remove.setAttribute('aria-label', `删除搜索关键词 ${index + 1}`);
        });
        const problems = [];
        if (!cards.length) problems.push('至少保留 1 个');
        if (emptyCount) problems.push(`${emptyCount} 个空项`);
        if (duplicateCount) problems.push('存在重复');
        if (tooLongCount) problems.push(`单项不能超过 ${CONFIG_TAG_MAX_LENGTH} 字`);
        const status = `${cards.length} / ${CONFIG_TAG_LIMIT}${problems.length ? ` · ${problems.join(' · ')}` : ''}`;
        if (count.textContent !== status) count.textContent = status;
        count.classList.toggle('bad', problems.length > 0); addButton.disabled = cards.length >= CONFIG_TAG_LIMIT;
    };
    const addCard = (value = '', focus = false, before = null) => {
        if (grid.children.length >= CONFIG_TAG_LIMIT) return null;
        const card = document.createElement('div'); card.className = 'config-tag-card'; card.setAttribute('role', 'listitem');
        const input = document.createElement('input'); input.type = 'text'; input.className = 'config-tag-input'; input.maxLength = CONFIG_TAG_MAX_LENGTH; input.value = value; input.placeholder = '搜索关键词';
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×'; remove.title = '删除关键词'; remove.setAttribute('aria-label', '删除关键词');
        remove.addEventListener('click', () => { const nextInput = card.nextElementSibling?.querySelector('input') || card.previousElementSibling?.querySelector('input'); card.remove(); update(); markConfigDirty(); (nextInput || addButton).focus(); });
        input.addEventListener('input', update);
        input.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
            event.preventDefault(); if (!input.value.trim()) return;
            const emptyInput = [...grid.querySelectorAll('.config-tag-input')].find((item) => !item.value.trim());
            if (emptyInput) emptyInput.focus(); else if (addCard('', true, card.nextElementSibling)) markConfigDirty();
        });
        input.addEventListener('paste', (event) => {
            const pastedText = event.clipboardData?.getData('text') || '';
            if (!/[\r\n,，]/.test(pastedText)) return;
            event.preventDefault();
            const parts = pastedText.split(/[\r\n,，]+/).map((item) => item.trim()).filter(Boolean);
            if (!parts.length) return;
            let changed = false;
            const selectionStart = input.selectionStart ?? input.value.length; const selectionEnd = input.selectionEnd ?? selectionStart;
            if (!input.value.trim() || selectionEnd > selectionStart) {
                const replacement = parts.shift(); const nextValue = input.value.trim() ? `${input.value.slice(0, selectionStart)}${replacement}${input.value.slice(selectionEnd)}` : replacement;
                changed = nextValue !== input.value; input.value = nextValue;
            }
            const available = Math.max(0, CONFIG_TAG_LIMIT - grid.children.length); const accepted = parts.slice(0, available); const dropped = parts.length - accepted.length; const insertBefore = card.nextElementSibling;
            accepted.forEach((item) => addCard(item, false, insertBefore)); changed = changed || accepted.length > 0;
            update(); if (changed) markConfigDirty();
            if (dropped) showToast(`搜索关键词最多 ${CONFIG_TAG_LIMIT} 个，另有 ${dropped} 个未添加`);
        });
        card.append(input, remove); grid.insertBefore(card, before); update(); if (focus) input.focus(); return input;
    };
    values.slice(0, CONFIG_TAG_LIMIT).forEach((value) => addCard(value));
    update();
    addButton.addEventListener('click', () => { const emptyInput = [...grid.querySelectorAll('.config-tag-input')].find((item) => !item.value.trim()); if (emptyInput) emptyInput.focus(); else if (addCard('', true)) markConfigDirty(); });
    editor.append(heading, grid, addButton); return editor;
}

function renderConfigMultiOptions(path, key, values) {
    const selected = new Set(Array.isArray(values) ? values : []);
    const field = document.createElement('div'); field.className = 'config-field config-multi-field'; field.dataset.configPath = path; field.dataset.valueType = 'enum-multi';
    const heading = document.createElement('div'); heading.className = 'config-multi-heading';
    const caption = document.createElement('span'); caption.textContent = configLabel(key);
    const status = document.createElement('small'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const group = document.createElement('div'); group.className = 'config-multi-options'; group.setAttribute('role', 'group'); group.setAttribute('aria-label', configLabel(key));
    const refresh = () => {
        const count = group.querySelectorAll('input:checked').length;
        status.textContent = count ? `已选择 ${count} 项 · 命中任一项即可` : '至少选择 1 项';
        status.classList.toggle('bad', count === 0);
        field.classList.toggle('invalid', count === 0);
    };
    CONFIG_MULTI_OPTIONS[key].forEach(([optionValue, labelText]) => {
        const option = document.createElement('label'); option.className = 'config-multi-option';
        const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = optionValue; checkbox.checked = selected.has(optionValue);
        const copy = document.createElement('span'); copy.textContent = labelText;
        checkbox.addEventListener('change', refresh);
        option.append(checkbox, copy); group.appendChild(option);
    });
    heading.append(caption, status); field.append(heading, group); refresh(); return field;
}

function makeConfigControl(path, key, value) {
    if (key === 'tags' && Array.isArray(value)) return renderTagEditor(path, value);
    if (CONFIG_MULTI_OPTIONS[key]) return renderConfigMultiOptions(path, key, value);
    const label = document.createElement('label'); label.className = 'config-field';
    const caption = document.createElement('span'); caption.textContent = configLabel(key); label.appendChild(caption);
    let input;
    if (CONFIG_ENUM_OPTIONS[key]) {
        input = document.createElement('select'); input.className = 'compact-select';
        CONFIG_ENUM_OPTIONS[key].forEach(([optionValue, labelText]) => { const option = document.createElement('option'); option.value = optionValue; option.textContent = labelText; option.selected = optionValue === value; input.appendChild(option); });
        input.dataset.valueType = 'enum';
    } else if (typeof value === 'boolean') {
        label.classList.add('config-switch'); input = document.createElement('input'); input.type = 'checkbox'; input.checked = value; label.prepend(input);
        if (key === 'llm_greeting_enabled') label.title = '关闭后直接使用固定打招呼语，不调用大模型';
    } else if (Array.isArray(value)) {
        input = document.createElement('textarea'); input.rows = Math.min(8, Math.max(3, value.length)); input.value = value.join('\n'); input.dataset.valueType = 'array';
    } else if (typeof value === 'number') {
        input = document.createElement('input'); input.type = 'number'; input.value = value; input.dataset.valueType = 'number';
    } else if (key === 'introduce' || key === 'character') {
        input = document.createElement('textarea'); input.rows = 3; input.value = value || ''; label.classList.add('config-field-textarea');
    } else {
        input = document.createElement('input'); input.type = /Host|api_base/.test(key) ? 'url' : 'text'; input.value = value ?? '';
    }
    input.dataset.configPath = path; label.appendChild(input); return label;
}

function renderScoringGroup(container, key, values) {
    const details = document.createElement('details'); details.className = 'scoring-config';
    const summary = document.createElement('summary'); summary.textContent = `${configLabel(key)}（${Object.keys(values).length} 条）`; details.appendChild(summary);
    const hint = document.createElement('p'); hint.textContent = '关键词和分值均可直接编辑，点击右侧按钮删除。'; details.appendChild(hint);
    const grid = document.createElement('div'); grid.className = 'score-card-grid'; grid.dataset.configPath = `scoring.${key}`; grid.dataset.valueType = 'score-cards';
    const addCard = (keyword = '', score = 0) => {
        const card = document.createElement('div'); card.className = 'score-keyword-card';
        const keywordInput = document.createElement('input'); keywordInput.type = 'text'; keywordInput.className = 'score-keyword'; keywordInput.value = keyword; keywordInput.placeholder = '关键词'; keywordInput.setAttribute('aria-label', '评分关键词');
        const scoreInput = document.createElement('input'); scoreInput.type = 'hidden'; scoreInput.className = 'score-value';
        const selector = document.createElement('div'); selector.className = 'deduction-star-selector'; selector.setAttribute('role', 'radiogroup');
        const result = document.createElement('span'); result.className = 'deduction-star-result';
        const starButtons = Array.from({ length: 5 }, (_, index) => { const button = document.createElement('button'); button.type = 'button'; button.className = 'deduction-star'; button.textContent = '✕'; button.dataset.stars = String(index + 1); button.setAttribute('role', 'radio'); button.setAttribute('aria-label', `扣除 ${index + 1} 星`); selector.appendChild(button); return button; });
        const setStars = (value) => { const selected = Math.max(1, Math.min(5, Number(value) || 1)); scoreInput.value = String(selected); starButtons.forEach((button, index) => { const active = index < selected; button.classList.toggle('active', active); button.setAttribute('aria-checked', String(Number(button.dataset.stars) === selected)); button.tabIndex = Number(button.dataset.stars) === selected ? 0 : -1; }); result.textContent = `扣 ${selected} 星`; card.dataset.deduction = String(selected); };
        starButtons.forEach((button) => button.addEventListener('click', () => { setStars(button.dataset.stars); markConfigDirty(); }));
        selector.addEventListener('keydown', (event) => { const current = Number(scoreInput.value); if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') { event.preventDefault(); setStars(current - 1); } else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') { event.preventDefault(); setStars(current + 1); } else if (event.key === 'Home') { event.preventDefault(); setStars(1); } else if (event.key === 'End') { event.preventDefault(); setStars(5); } });
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'score-card-remove'; remove.textContent = '×'; remove.title = '删除关键词'; remove.addEventListener('click', () => { card.remove(); summary.textContent = `${configLabel(key)}（${grid.children.length} 条）`; markConfigDirty(); });
        card.append(keywordInput, scoreInput, remove, selector, result); setStars(score); grid.appendChild(card); summary.textContent = `${configLabel(key)}（${grid.children.length} 条）`;
    };
    Object.entries(values).forEach(([keyword, score]) => addCard(keyword, score));
    const addButton = document.createElement('button'); addButton.type = 'button'; addButton.className = 'score-card-add'; addButton.textContent = '＋ 新增关键词'; addButton.addEventListener('click', () => { addCard(); grid.lastElementChild?.querySelector('.score-keyword')?.focus(); markConfigDirty(); });
    details.append(grid, addButton); container.appendChild(details);
}

function updateScoringEditorState(fieldset, enabled) {
    const editor = fieldset.querySelector('.scoring-rule-editor');
    fieldset.classList.toggle('scoring-rules-disabled', !enabled);
    editor?.setAttribute('aria-disabled', String(!enabled));
    editor?.querySelectorAll('input, button').forEach((control) => { control.disabled = !enabled; });
    const status = fieldset.querySelector('[data-scoring-toggle-state]');
    if (status) status.textContent = enabled ? '已启用' : '已关闭';
}

function renderScoringMasterToggle(fieldset, enabled) {
    const bar = document.createElement('div'); bar.className = 'scoring-master-bar';
    const copy = document.createElement('div'); copy.className = 'scoring-master-copy';
    copy.innerHTML = '<strong>启用岗位扣分规则</strong><small>关闭后所有岗位保持 5 星，不再因关键词扣星或直接丢弃；现有规则会完整保留。</small>';
    const label = document.createElement('label'); label.className = 'scoring-master-toggle';
    const input = document.createElement('input'); input.type = 'checkbox'; input.checked = enabled; input.dataset.configPath = 'scoring_enabled'; input.setAttribute('aria-label', '启用岗位扣分规则');
    const control = document.createElement('span'); control.className = 'llm-toggle'; control.setAttribute('aria-hidden', 'true'); control.innerHTML = '<i></i>';
    const status = document.createElement('em'); status.dataset.scoringToggleState = ''; label.append(input, control, status); bar.append(copy, label); fieldset.appendChild(bar);
    input.addEventListener('change', () => updateScoringEditorState(fieldset, input.checked));
    return input;
}

function fillConfigForm(data) {
    const config = data.config; state.adminConfig = config; state.configDirty = false;
    const form = $('#visualConfigForm'); form.replaceChildren();
    const basics = document.createElement('fieldset'); basics.innerHTML = '<legend>基础资料</legend><div class="config-field-grid config-basic-grid"></div>'; const basicGrid = basics.querySelector('div');
    Object.entries(config).filter(([key]) => !['backend', 'frontend', 'scoring', 'resume_name', 'scoring_enabled'].includes(key)).forEach(([key, value]) => basicGrid.appendChild(makeConfigControl(key, key, value))); form.appendChild(basics);
    ['backend', 'frontend'].forEach((groupKey) => { const fieldset = document.createElement('fieldset'); const legend = document.createElement('legend'); legend.textContent = configLabel(groupKey); fieldset.appendChild(legend); const grid = document.createElement('div'); grid.className = 'config-field-grid'; Object.entries(config[groupKey] || {}).filter(([key]) => !HIDDEN_CONFIG_PATHS.has(`${groupKey}.${key}`)).forEach(([key, value]) => grid.appendChild(makeConfigControl(`${groupKey}.${key}`, key, value))); fieldset.appendChild(grid); form.appendChild(fieldset); });
    const scoring = document.createElement('fieldset'); scoring.className = 'scoring-rule-fieldset'; const scoringLegend = document.createElement('legend'); scoringLegend.textContent = configLabel('scoring'); scoring.appendChild(scoringLegend); renderScoringMasterToggle(scoring, Boolean(config.scoring_enabled)); const scoringHint = document.createElement('p'); scoringHint.className = 'scoring-model-hint'; scoringHint.textContent = '每个岗位初始为 5 星。命中关键词后按规则扣星；同一段文字优先匹配更长的关键词。剩余星级小于 0 时直接丢弃岗位。'; scoring.appendChild(scoringHint); const scoringEditor = document.createElement('div'); scoringEditor.className = 'scoring-rule-editor'; Object.entries(config.scoring || {}).forEach(([key, values]) => renderScoringGroup(scoringEditor, key, values)); scoring.appendChild(scoringEditor); updateScoringEditorState(scoring, Boolean(config.scoring_enabled)); form.appendChild(scoring);
    refreshAdminSaveState();
}

const LLM_KEEP_SECRET = '__KEEP__';
const LLM_STRATEGY_HINTS = {
    failover: '按列表顺序调用，失败后切换',
    round_robin: '在可用接口之间轮流调用',
};

function setLlmStrategy(value, markDirty = false) {
    const strategy = value === 'round_robin' ? 'round_robin' : 'failover';
    const control = $('#llmStrategy');
    control.dataset.value = strategy;
    $$('[data-llm-strategy]').forEach((button) => {
        const active = button.dataset.llmStrategy === strategy;
        button.classList.toggle('active', active);
        button.setAttribute('aria-checked', String(active));
    });
    $('#llmStrategyHint').textContent = LLM_STRATEGY_HINTS[strategy];
    if (markDirty) markLlmDirty();
}

function setLlmTestState(card, tone, text) {
    const badge = card.querySelector('.llm-provider-test');
    const label = badge.querySelector('span');
    const fullText = String(text || '未测试');
    badge.className = `llm-provider-test${tone ? ` ${tone}` : ''}`;
    badge.title = fullText;
    label.textContent = fullText.length > 64 ? `${fullText.slice(0, 61)}...` : fullText;
    card.dataset.testState = tone || 'idle';
}

function updateLlmSecretState(card, kind) {
    const isKey = kind === 'key';
    const fieldName = isKey ? 'api_key' : 'proxy_url';
    const configuredKey = isKey ? 'keyConfigured' : 'proxyConfigured';
    const dirtyKey = isKey ? 'keyDirty' : 'proxyDirty';
    const maskedKey = isKey ? 'keyMasked' : 'proxyMasked';
    const input = card.querySelector(`[data-llm-field="${fieldName}"]`);
    const meta = card.querySelector(`[data-llm-secret-state="${kind}"]`);
    const reveal = card.querySelector(`[data-llm-action="reveal-${kind}"]`);
    const clear = card.querySelector(`[data-llm-action="clear-${kind}"]`);
    const configured = card.dataset[configuredKey] === '1';
    const dirty = card.dataset[dirtyKey] === '1';
    const value = input.value.trim();
    if (dirty && value) meta.textContent = '待更新';
    else if (dirty && configured) meta.textContent = '保存后清除';
    else if (configured) meta.textContent = `已配置 · ${card.dataset[maskedKey] || '******'}`;
    else meta.textContent = '未配置';
    reveal.disabled = !value;
    clear.disabled = !configured && !value;
}

function refreshLlmCard(card, stale = false) {
    const field = (name) => card.querySelector(`[data-llm-field="${name}"]`);
    const name = field('name').value.trim() || '未命名接口';
    const model = field('model').value.trim() || '未选择模型';
    const enabled = field('enabled').checked;
    const proxyEnabled = field('proxy_enabled').checked;
    card.querySelector('[data-llm-summary="name"]').textContent = name;
    card.querySelector('[data-llm-summary="route"]').textContent = `${model} · ${proxyEnabled ? '代理连接' : '直接连接'}`;
    card.querySelector('.llm-card-toggle em').textContent = enabled ? '已启用' : '已停用';
    card.classList.toggle('is-disabled', !enabled);
    field('proxy_url').disabled = !proxyEnabled;
    updateLlmSecretState(card, 'key');
    updateLlmSecretState(card, 'proxy');
    if (stale && !card.classList.contains('is-testing')) setLlmTestState(card, 'stale', '配置已修改');
}

function updateLlmSummary() {
    const cards = $$('#llmProviderList .llm-provider-card');
    const enabled = cards.filter((card) => card.querySelector('[data-llm-field="enabled"]').checked).length;
    const proxyCount = cards.filter((card) => card.querySelector('[data-llm-field="proxy_enabled"]').checked).length;
    const summary = $('#llmProviderSummary');
    summary.classList.toggle('empty', cards.length === 0);
    summary.innerHTML = `<i></i>${cards.length} 个接口 · ${enabled} 个启用${proxyCount ? ` · ${proxyCount} 个代理` : ''}`;
}

function refreshLlmOrder() {
    const cards = $$('#llmProviderList .llm-provider-card');
    cards.forEach((card, index) => {
        card.querySelector('.llm-provider-order').textContent = String(index + 1).padStart(2, '0');
        card.querySelector('[data-llm-action="move-up"]').disabled = index === 0;
        card.querySelector('[data-llm-action="move-down"]').disabled = index === cards.length - 1;
    });
    updateLlmSummary();
}

function markLlmDirty(card = null) {
    state.llmDirty = true;
    if ($('#adminTabs .active')?.dataset.adminTab === 'llm') refreshAdminSaveState('llm');
    if (card) refreshLlmCard(card, true);
    updateLlmSummary();
}

function llmProviderCard(provider = {}) {
    const card = document.createElement('article'); card.className = 'llm-provider-card';
    const keyConfigured = Boolean(provider.apiKeyConfigured);
    const proxyConfigured = Boolean(provider.proxyUrlConfigured);
    const proxyEnabled = Boolean(provider.proxyEnabled);
    card.dataset.index = provider.index != null ? String(provider.index) : '';
    card.dataset.keyConfigured = keyConfigured ? '1' : '0';
    card.dataset.keyDirty = '0';
    card.dataset.keyMasked = provider.apiKeyMasked || '';
    card.dataset.proxyConfigured = proxyConfigured ? '1' : '0';
    card.dataset.proxyDirty = '0';
    card.dataset.proxyMasked = provider.proxyUrlMasked || '';
    const keyPlaceholder = keyConfigured ? '留空保留已配置的 API Key' : 'sk-...';
    const proxyPlaceholder = proxyConfigured ? '留空保留已配置的代理地址' : 'http://127.0.0.1:7890';
    card.innerHTML = `
        <div class="llm-provider-top">
            <span class="llm-provider-order">01</span>
            <div class="llm-provider-identity"><strong data-llm-summary="name"></strong><small data-llm-summary="route"></small></div>
            <span class="llm-provider-test" role="status" aria-live="polite"><i></i><span>未测试</span></span>
            <label class="llm-card-toggle" title="启用或停用这个接口">
                <input type="checkbox" data-llm-field="enabled" ${provider.enabled !== false ? 'checked' : ''}>
                <span class="llm-toggle" aria-hidden="true"><i></i></span><em>已启用</em>
            </label>
            <div class="llm-provider-tools">
                <button type="button" class="llm-icon-action" data-llm-action="move-up" title="上移接口" aria-label="上移接口">↑</button>
                <button type="button" class="llm-icon-action" data-llm-action="move-down" title="下移接口" aria-label="下移接口">↓</button>
                <button type="button" class="llm-test-action" data-llm-action="test"><span aria-hidden="true">↻</span><b>测试</b></button>
                <button type="button" class="llm-icon-action danger-action" data-llm-action="remove" title="删除接口" aria-label="删除接口">×</button>
            </div>
        </div>
        <div class="llm-provider-grid">
            <label class="llm-field llm-span-4"><span>接口名称</span><input type="text" data-llm-field="name" maxlength="60" value="${escapeHtml(provider.name || '')}" placeholder="例如 OpenAI 主接口"></label>
            <label class="llm-field llm-span-4"><span>模型名称</span><input type="text" data-llm-field="model" maxlength="120" value="${escapeHtml(provider.model || '')}" placeholder="例如 gpt-4.1-mini"></label>
            <div class="llm-field llm-span-4">
                <div class="llm-field-label"><span>API Key</span><small data-llm-secret-state="key"></small></div>
                <div class="llm-input-shell"><input type="password" data-llm-field="api_key" placeholder="${keyPlaceholder}" autocomplete="new-password"><button type="button" data-llm-action="reveal-key">显示</button><button type="button" data-llm-action="clear-key">清除</button></div>
            </div>
            <label class="llm-field llm-span-6"><span>接口地址</span><input type="url" data-llm-field="api_base" value="${escapeHtml(provider.api_base || '')}" placeholder="https://api.example.com/v1"><small>OpenAI 兼容 API 地址</small></label>
            <div class="llm-field llm-span-6 llm-proxy-field">
                <div class="llm-field-label">
                    <span>HTTP(S) 代理</span><small data-llm-secret-state="proxy"></small>
                    <label class="llm-inline-toggle"><input type="checkbox" data-llm-field="proxy_enabled" ${proxyEnabled ? 'checked' : ''}><span class="llm-toggle" aria-hidden="true"><i></i></span><em>使用代理</em></label>
                </div>
                <div class="llm-input-shell"><input type="password" data-llm-field="proxy_url" placeholder="${proxyPlaceholder}" autocomplete="new-password"><button type="button" data-llm-action="reveal-proxy">显示</button><button type="button" data-llm-action="clear-proxy">清除</button></div>
            </div>
        </div>
        <p class="llm-card-error" role="alert" hidden></p>`;
    const test = provider.__test;
    if (test) setLlmTestState(card, test.ok ? 'ok' : 'bad', test.ok ? `可用 · ${test.latencyMs ?? '—'}ms` : `失败 · ${test.error || test.status || '未知'}`);
    refreshLlmCard(card);
    return card;
}

function renderLlmProviders() {
    const list = $('#llmProviderList'); list.replaceChildren();
    const providers = state.llm.providers || [];
    if (!providers.length) { list.innerHTML = '<div class="llm-empty"><span>＋</span><strong>还没有大模型接口</strong><small>添加接口后可配置模型与连接方式</small></div>'; updateLlmSummary(); return; }
    providers.forEach((provider) => list.appendChild(llmProviderCard(provider)));
    refreshLlmOrder();
}

function collectLlmProvider(card) {
    const field = (name) => card.querySelector(`[data-llm-field="${name}"]`);
    const secretValue = (kind, fieldName) => {
        const dirty = card.dataset[`${kind}Dirty`] === '1';
        const configured = card.dataset[`${kind}Configured`] === '1';
        return dirty ? field(fieldName).value.trim() : (configured ? LLM_KEEP_SECRET : '');
    };
    const indexRaw = card.dataset.index;
    return {
        index: indexRaw === '' ? null : Number(indexRaw),
        name: field('name').value.trim(),
        api_base: field('api_base').value.trim(),
        model: field('model').value.trim(),
        enabled: field('enabled').checked,
        api_key: secretValue('key', 'api_key'),
        proxy_enabled: field('proxy_enabled').checked,
        proxy_url: secretValue('proxy', 'proxy_url'),
    };
}

function collectLlmPayload() {
    return {
        strategy: $('#llmStrategy').dataset.value || 'failover',
        timeout: Number($('#llmTimeout').value) || 180,
        jobFilter: $('#llmJobFilter').checked,
        providers: $$('#llmProviderList .llm-provider-card').map(collectLlmProvider),
    };
}

function validateLlmCard(card, testMode = false) {
    const field = (name) => card.querySelector(`[data-llm-field="${name}"]`);
    const required = testMode || field('enabled').checked;
    const missing = [];
    const invalidFields = [];
    const apiKeyAvailable = card.dataset.keyDirty === '1' ? Boolean(field('api_key').value.trim()) : card.dataset.keyConfigured === '1';
    const proxyAvailable = card.dataset.proxyDirty === '1' ? Boolean(field('proxy_url').value.trim()) : card.dataset.proxyConfigured === '1';
    card.querySelectorAll('[aria-invalid="true"]').forEach((input) => input.removeAttribute('aria-invalid'));
    if (required && !field('api_base').value.trim()) { missing.push('接口地址'); invalidFields.push(field('api_base')); }
    if (required && !field('model').value.trim()) { missing.push('模型名称'); invalidFields.push(field('model')); }
    if (required && !apiKeyAvailable) { missing.push('API Key'); invalidFields.push(field('api_key')); }
    if (required && field('proxy_enabled').checked && !proxyAvailable) { missing.push('代理地址'); invalidFields.push(field('proxy_url')); }
    const apiBase = field('api_base').value.trim();
    const proxyUrl = field('proxy_url').value.trim();
    if (apiBase && !/^https?:\/\/\S+$/i.test(apiBase)) { missing.push('有效的 HTTP(S) 接口地址'); invalidFields.push(field('api_base')); }
    if (proxyUrl && !/^https?:\/\/\S+$/i.test(proxyUrl)) { missing.push('有效的 HTTP(S) 代理地址'); invalidFields.push(field('proxy_url')); }
    const error = card.querySelector('.llm-card-error');
    const unique = [...new Set(missing)];
    error.textContent = unique.length ? `请检查：${unique.join('、')}` : '';
    error.hidden = unique.length === 0;
    card.classList.toggle('has-error', unique.length > 0);
    invalidFields.forEach((input) => input.setAttribute('aria-invalid', 'true'));
    return unique.length === 0;
}

function validateLlmCards() {
    const invalid = $$('#llmProviderList .llm-provider-card').filter((card) => !validateLlmCard(card));
    if (invalid.length) {
        invalid[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        invalid[0].querySelector('[aria-invalid="true"]')?.focus({ preventScroll: true });
        return false;
    }
    return true;
}

function applyLlmConfig(data) {
    state.llm = { providers: (data.providers || []).map((item) => ({ ...item })) };
    state.llmDirty = false;
    setLlmStrategy(data.strategy || 'failover');
    $('#llmTimeout').value = data.timeout || 180;
    $('#llmJobFilter').checked = Boolean(data.jobFilter);
    $('#llmJobFilterState').textContent = data.jobFilter ? '已启用' : '已关闭';
    renderLlmProviders();
    refreshAdminSaveState();
}

async function loadLlm() {
    try { applyLlmConfig(await apiJson('/api/admin/llm')); }
    catch (error) { $('#llmNotice').textContent = `接口配置读取失败：${error.message}`; }
}

async function saveLlm() {
    if (!validateLlmCards()) return showToast('请先补全或修正接口配置');
    const button = $('#saveLlm'); button.disabled = true; button.classList.add('loading');
    try { applyLlmConfig(await apiJson('/api/admin/llm', { method: 'PUT', body: JSON.stringify(collectLlmPayload()) })); showToast('接口配置已保存并热加载'); }
    catch (error) { showToast(`保存失败：${error.message}`); }
    finally { button.disabled = false; button.classList.remove('loading'); }
}

function addLlmProvider() {
    if ($$('#llmProviderList .llm-provider-card').length >= 20) return showToast('最多支持 20 个大模型接口');
    const list = $('#llmProviderList'); const empty = list.querySelector('.llm-empty'); if (empty) list.replaceChildren();
    const card = llmProviderCard({ enabled: true });
    list.appendChild(card); refreshLlmOrder(); markLlmDirty(card);
    card.scrollIntoView({ behavior: 'smooth', block: 'center' });
    card.querySelector('[data-llm-field="name"]').focus({ preventScroll: true });
}

async function testLlmProvider(card) {
    if (!validateLlmCard(card, true)) { setLlmTestState(card, 'bad', '配置不完整'); return false; }
    const button = card.querySelector('[data-llm-action="test"]');
    button.disabled = true; card.classList.add('is-testing'); setLlmTestState(card, 'testing', '连接中...');
    try {
        const result = await apiJson('/api/admin/llm/test', { method: 'POST', body: JSON.stringify({ provider: collectLlmProvider(card) }) });
        if (result.ok) { setLlmTestState(card, 'ok', `${result.viaProxy ? '代理' : '直连'}可用 · ${result.latencyMs ?? '—'}ms`); return true; }
        setLlmTestState(card, 'bad', `失败 · ${result.error || result.status || '未知错误'}`); return false;
    } catch (error) { setLlmTestState(card, 'bad', `失败 · ${error.message}`); return false; }
    finally { button.disabled = false; card.classList.remove('is-testing'); }
}

async function testAllLlm() {
    const cards = $$('#llmProviderList .llm-provider-card');
    if (!cards.length) return showToast('请先添加大模型接口');
    const button = $('#llmTestAll'); button.disabled = true; button.classList.add('loading');
    try {
        const results = await Promise.all(cards.map(testLlmProvider));
        const successCount = results.filter(Boolean).length;
        showToast(`测活完成：${successCount} / ${cards.length} 个接口可用`);
    } finally { button.disabled = false; button.classList.remove('loading'); }
}

async function loadAdmin() {
    try {
        const [config, resumes, promptData, llm] = await Promise.all([apiJson('/api/admin/config'), apiJson('/api/admin/resumes'), apiJson('/api/admin/prompts'), apiJson('/api/admin/llm')]);
        fillConfigForm(config); renderResumeOptions(resumes); state.prompts = promptData.items || []; renderPromptOptions(); applyLlmConfig(llm);
    } catch (error) { $('#adminSaveState').textContent = '管理接口不可用'; $('#configNotice').textContent = error.message; }
}

async function saveAdminConfig() {
    const button = $('#saveConfig'); button.disabled = true;
    try {
        const config = structuredClone(state.adminConfig);
        $$('[data-config-path]').forEach((input) => {
            let value;
            if (input.dataset.valueType === 'enum-multi') {
                value = [...input.querySelectorAll('input[type="checkbox"]:checked')].map((item) => item.value);
                if (!value.length) { input.querySelector('input')?.focus(); throw new Error(`${configLabel(input.dataset.configPath.split('.').at(-1))}至少选择 1 项`); }
            }
            else if (input.type === 'checkbox') value = input.checked;
            else if (input.dataset.valueType === 'number') value = Number(input.value);
            else if (input.dataset.valueType === 'tag-cards') {
                const tagInputs = [...input.querySelectorAll('.config-tag-input')]; const values = tagInputs.map((item) => item.value.trim());
                const fail = (message, field) => { field?.focus(); throw new Error(message); };
                if (!values.length) fail('至少需要 1 个搜索关键词');
                if (values.length > CONFIG_TAG_LIMIT) fail(`搜索关键词最多 ${CONFIG_TAG_LIMIT} 个`);
                const emptyIndex = values.findIndex((item) => !item); if (emptyIndex >= 0) fail('搜索关键词中存在空项', tagInputs[emptyIndex]);
                const tooLongIndex = values.findIndex((item) => [...item].length > CONFIG_TAG_MAX_LENGTH); if (tooLongIndex >= 0) fail(`每个搜索关键词不能超过 ${CONFIG_TAG_MAX_LENGTH} 字`, tagInputs[tooLongIndex]);
                const seen = new Map(); let duplicateIndex = -1;
                values.some((item, index) => { const normalized = normalizeConfigTag(item); if (seen.has(normalized)) { duplicateIndex = index; return true; } seen.set(normalized, index); return false; });
                if (duplicateIndex >= 0) fail(`搜索关键词不能重复：${values[duplicateIndex]}`, tagInputs[duplicateIndex]);
                value = values;
            }
            else if (input.dataset.valueType === 'array') value = input.value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean);
            else if (input.dataset.valueType === 'score-cards') { value = {}; input.querySelectorAll('.score-keyword-card').forEach((card) => { const keyword = card.querySelector('.score-keyword').value.trim(); const score = Number(card.querySelector('.score-value').value); if (!keyword) throw new Error('扣星规则中存在空关键词'); if (!Number.isInteger(score) || score < 1 || score > 5) throw new Error(`关键词「${keyword}」的扣星数必须为 1～5`); if (Object.hasOwn(value, keyword)) throw new Error(`扣星关键词重复：${keyword}`); value[keyword] = score; }); }
            else value = input.value.trim();
            const parts = input.dataset.configPath.split('.'); let target = config; parts.slice(0, -1).forEach((part) => { target = target[part]; }); target[parts.at(-1)] = value;
        });
        const result = await apiJson('/api/admin/config', { method: 'PUT', body: JSON.stringify({ config }) }); fillConfigForm(result); showToast('参数配置已保存并热加载');
    } catch (error) { showToast(`保存失败：${error.message}`); } finally { button.disabled = false; }
}

function renderResumeOptions(data) {
    const select = $('#resumeSelect'); select.replaceChildren();
    const items = data.items || [];
    if (!items.length) {
        const option = document.createElement('option'); option.textContent = '暂无简历'; option.value = ''; select.appendChild(option); select.disabled = true;
        state.currentResume = ''; $('#resumeEditor').value = ''; $('#resumeMeta').textContent = 'resumes/ 中暂无简历'; refreshAdminSaveState(); return;
    }
    select.disabled = false;
    items.forEach((item) => { const option = document.createElement('option'); option.value = item.name; option.textContent = item.name; select.appendChild(option); });
    state.currentResume = data.selected || items[0].name; select.value = state.currentResume; loadResume(state.currentResume); refreshAdminSaveState();
}

async function loadResume(name) {
    if (!name) return;
    try { const data = await apiJson(`/api/admin/resumes/${encodeURIComponent(name)}`); state.currentResume = name; $('#resumeEditor').value = data.content || ''; $('#resumeMeta').textContent = `resumes/${name} · ${data.size || 0} bytes`; refreshAdminSaveState(); } catch (error) { showToast(`读取简历失败：${error.message}`); }
}

async function selectCurrentResume(name) {
    if (!name || name === state.currentResume) return;
    const select = $('#resumeSelect'); select.disabled = true;
    try {
        const data = await apiJson('/api/admin/resumes/current', { method: 'PUT', body: JSON.stringify({ name }) });
        renderResumeOptions(data); showToast(`已将 ${name} 设为 LLM 当前简历`);
    } catch (error) { select.value = state.currentResume; showToast(`切换失败：${error.message}`); }
    finally { select.disabled = false; }
}

async function createResume() {
    let name = $('#newResumeName').value.trim(); if (!name) return showToast('请输入简历文件名');
    if (!/\.[^.]+$/.test(name)) name += '.md';
    if (!/\.(md|txt)$/i.test(name)) return showToast('简历只支持 .md 或 .txt 文件');
    const select = $('#resumeSelect'); select.disabled = false;
    let option = [...select.options].find((item) => item.value === name);
    if (!option) { option = document.createElement('option'); option.value = name; option.textContent = `${name}（新文件）`; select.appendChild(option); }
    state.currentResume = name; select.value = name; $('#newResumeName').value = name; $('#resumeEditor').value = ''; $('#resumeMeta').textContent = '新文件 · 保存后设为 LLM 当前简历'; showToast(`已新建 ${name} 编辑页`);
}

async function saveCurrentResume() {
    if (!state.currentResume) return showToast('请先选择简历');
    try { const result = await apiJson(`/api/admin/resumes/${encodeURIComponent(state.currentResume)}`, { method: 'PUT', body: JSON.stringify({ content: $('#resumeEditor').value, select: true }) }); $('#resumeMeta').textContent = `resumes/${state.currentResume} · ${result.size} bytes · 当前简历`; $('#newResumeName').value = ''; showToast('简历已保存并设为 LLM 当前简历'); const list = await apiJson('/api/admin/resumes'); renderResumeOptions(list); } catch (error) { showToast(`保存失败：${error.message}`); }
}

function renderPromptOptions() {
    const select = $('#promptSelect'); select.replaceChildren();
    state.prompts.forEach((item) => { const option = document.createElement('option'); option.value = item.key; option.textContent = `${item.label} · ${item.key}`; select.appendChild(option); });
    state.currentPrompt = state.prompts[0]?.key || ''; select.value = state.currentPrompt; showPrompt(state.currentPrompt);
}

function showPrompt(key) { const item = state.prompts.find((prompt) => prompt.key === key); state.currentPrompt = key; $('#promptEditor').value = item?.content || ''; $('#promptHint').textContent = ['CHAT', 'CUSTOM_INTRODUCE', 'JOB_FILTER'].includes(key) ? '请保留原有 {resume}、{job_info}、{character} 等占位符' : '该提示词不需要变量占位符'; }

async function saveCurrentPrompt() {
    if (!state.currentPrompt) return;
    try { const data = await apiJson('/api/admin/prompts', { method: 'PUT', body: JSON.stringify({ values: { [state.currentPrompt]: $('#promptEditor').value } }) }); state.prompts = data.items || []; showPrompt(state.currentPrompt); showToast('提示词已保存并立即生效'); } catch (error) { showToast(`保存失败：${error.message}`); }
}

function resetFilters() {
    Object.assign(state, { search: '', status: 'all', city: 'all', experience: 'all', education: 'all', minSalary: null, maxSalary: null, salaryBucket: '', keyword: '', mapProvince: '', mapCity: '', industry: '', role: '', exactDate: '', page: 1 });
    state.mapLevel = 'province'; Object.assign(state.mapView, { scale: 1, x: 0, y: 0, suppressClick: false });
    $('#searchInput').value = ''; $('#statusFilter').value = 'all'; $('#cityFilter').value = 'all'; $('#experienceFilter').value = 'all'; $('#educationFilter').value = 'all'; $('#minSalaryFilter').value = ''; $('#maxSalaryFilter').value = ''; $('#keywordFilter').value = ''; updateDashboard();
}

function bindEvents() {
    $$('#rangeSwitch button').forEach((button) => button.addEventListener('click', () => { $$('#rangeSwitch button').forEach((item) => item.classList.toggle('active', item === button)); state.range = button.dataset.range; state.page = 1; updateDashboard(); }));
    $('#searchInput').addEventListener('input', (event) => { state.search = event.target.value; state.page = 1; updateDashboard(); });
    $('#statusFilter').addEventListener('change', (event) => { state.status = event.target.value; state.page = 1; updateDashboard(); });
    $('#cityFilter').addEventListener('change', (event) => { state.city = event.target.value; state.page = 1; updateDashboard(); });
    $('#experienceFilter').addEventListener('change', (event) => { state.experience = event.target.value; state.page = 1; updateDashboard(); });
    $('#educationFilter').addEventListener('change', (event) => { state.education = event.target.value; state.page = 1; updateDashboard(); });
    $('#minSalaryFilter').addEventListener('input', (event) => { state.minSalary = event.target.value === '' ? null : Number(event.target.value); state.page = 1; updateDashboard(); });
    $('#maxSalaryFilter').addEventListener('input', (event) => { state.maxSalary = event.target.value === '' ? null : Number(event.target.value); state.page = 1; updateDashboard(); });
    $('#keywordFilter').addEventListener('input', (event) => { state.keyword = event.target.value; state.page = 1; updateDashboard(); });
    $('#advancedToggle').addEventListener('click', () => { const panel = $('#advancedFilters'); panel.hidden = !panel.hidden; $('#advancedToggle span').textContent = panel.hidden ? '⌄' : '⌃'; });
    $('#resetFilters').addEventListener('click', resetFilters); $('#mapMetric').addEventListener('change', () => renderMap(getFilteredRecords())); $('#refreshButton').addEventListener('click', () => loadData()); $('#exportButton').addEventListener('click', exportCsv);
    $('#themeToggle').addEventListener('click', toggleTheme);
    $('#prevPage').addEventListener('click', () => { state.page -= 1; renderRecords(getFilteredRecords()); }); $('#nextPage').addEventListener('click', () => { state.page += 1; renderRecords(getFilteredRecords()); });
    $('#drawerClose').addEventListener('click', closeDrawer); $('#drawerBackdrop').addEventListener('click', closeDrawer); $('#mobileMenu').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
    $('#liveLogToggle').addEventListener('click', () => setLiveLogsExpanded(!state.logsExpanded));
    $('#pauseLogs').addEventListener('click', () => setLogsPaused(!state.logsPaused)); $('#clearLogs').addEventListener('click', () => { state.logClearedCursor = state.runtimeCursor; renderLiveLogs(); });
    $('#exportSelected').addEventListener('click', () => exportRecords(state.records.filter((record) => state.selectedIds.has(record.id)), '投递报表_所选'));
    $('#deleteSelected').addEventListener('click', () => deleteDeliveryRecords([...state.selectedIds]));
    $('#clearSelection').addEventListener('click', () => { state.selectedIds.clear(); renderRecords(getFilteredRecords()); });
    $('#deleteConfirmCancel').addEventListener('click', () => closeDeleteConfirm(false));
    $('#deleteConfirmSubmit').addEventListener('click', () => closeDeleteConfirm(true));
    $('#deleteConfirm').addEventListener('click', (event) => { if (event.target.id === 'deleteConfirm') closeDeleteConfirm(false); });
    $('#controlStopCancel').addEventListener('click', () => closeStopAllConfirm(false));
    $('#controlStopSubmit').addEventListener('click', () => closeStopAllConfirm(true));
    $('#controlStopConfirm').addEventListener('click', (event) => { if (event.target.id === 'controlStopConfirm') closeStopAllConfirm(false); });
    $('#authPromptCancel').addEventListener('click', cancelAuthPrompt);
    $('#authPrompt').addEventListener('click', (event) => { if (event.target.id === 'authPrompt') cancelAuthPrompt(); });
    $('#authForm').addEventListener('submit', (event) => {
        event.preventDefault();
        const input = $('#authTokenInput');
        const error = $('#authPromptError');
        const token = input.value.trim();
        if (token.length < 32 || token.length > 256) {
            error.textContent = '共享令牌长度必须为 32–256 位。';
            error.hidden = false;
            input.focus();
            return;
        }
        authPromptDismissed = false;
        writeAuthToken(token);
        settleAuthPrompt(token);
    });
    $$('#densitySwitch button').forEach((button) => button.addEventListener('click', () => { state.density = button.dataset.density; $('#applications').dataset.density = state.density; $$('#densitySwitch button').forEach((item) => item.classList.toggle('active', item === button)); saveTablePreferences(); }));
    $('#columnManagerButton').addEventListener('click', (event) => { event.stopPropagation(); $('#columnManagerMenu').hidden = !$('#columnManagerMenu').hidden; });
    $('#columnManagerMenu').addEventListener('change', (event) => {
        const key = event.target.dataset.toggleColumn; if (!key) return;
        if (event.target.checked) state.visibleColumns.add(key); else if (state.visibleColumns.size > 1) state.visibleColumns.delete(key); else { event.target.checked = true; return showToast('至少保留一列'); }
        saveTablePreferences(); renderRecords(getFilteredRecords());
    });
    $('#resetTableView').addEventListener('click', () => {
        localStorage.removeItem(TABLE_PREFS_KEY); state.visibleColumns = new Set(Object.keys(TABLE_COLUMNS)); state.columnOrder = Object.keys(TABLE_COLUMNS); state.columnWidths = {}; state.density = 'default'; state.sort = { key: 'loggedAt', direction: 'desc' }; restoreTablePreferences(); renderRecords(getFilteredRecords()); showToast('表格视图已重置');
    });
    $$('.nav-item').forEach((button) => button.addEventListener('click', () => { $$('.nav-item').forEach((item) => item.classList.toggle('active', item === button)); document.getElementById(button.dataset.scroll)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); $('#sidebar').classList.remove('open'); }));
    $$('#adminTabs button').forEach((button) => button.addEventListener('click', () => {
        $$('#adminTabs button').forEach((item) => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-selected', String(active)); });
        $$('.admin-view').forEach((view) => view.classList.toggle('active', view.dataset.adminView === button.dataset.adminTab));
        refreshAdminSaveState(button.dataset.adminTab);
    }));
    $('#visualConfigForm').addEventListener('input', markConfigDirty); $('#visualConfigForm').addEventListener('change', markConfigDirty);
    $('#saveConfig').addEventListener('click', saveAdminConfig); $('#resumeSelect').addEventListener('change', (event) => selectCurrentResume(event.target.value)); $('#createResume').addEventListener('click', createResume); $('#saveResume').addEventListener('click', saveCurrentResume); $('#promptSelect').addEventListener('change', (event) => showPrompt(event.target.value)); $('#savePrompt').addEventListener('click', saveCurrentPrompt);
    $('#saveLlm').addEventListener('click', saveLlm); $('#llmAddProvider').addEventListener('click', addLlmProvider); $('#llmTestAll').addEventListener('click', testAllLlm);
    $('#llmStrategy').addEventListener('click', (event) => { const button = event.target.closest('[data-llm-strategy]'); if (button) setLlmStrategy(button.dataset.llmStrategy, true); });
    $('#llmTimeout').addEventListener('input', () => markLlmDirty());
    $('#llmJobFilter').addEventListener('input', (event) => { $('#llmJobFilterState').textContent = event.target.checked ? '已启用' : '已关闭'; markLlmDirty(); });
    $('#llmProviderList').addEventListener('input', (event) => {
        const card = event.target.closest('.llm-provider-card'); const fieldName = event.target.dataset.llmField; if (!card || !fieldName) return;
        if (fieldName === 'api_key') card.dataset.keyDirty = '1';
        if (fieldName === 'proxy_url') card.dataset.proxyDirty = '1';
        const error = card.querySelector('.llm-card-error'); error.hidden = true; card.classList.remove('has-error');
        markLlmDirty(card);
        if (fieldName === 'proxy_enabled' && event.target.checked && card.dataset.proxyConfigured !== '1') card.querySelector('[data-llm-field="proxy_url"]').focus();
    });
    $('#llmProviderList').addEventListener('click', (event) => {
        const button = event.target.closest('[data-llm-action]'); if (!button) return;
        const card = button.closest('.llm-provider-card'); const action = button.dataset.llmAction; const list = $('#llmProviderList');
        if (action === 'test') { testLlmProvider(card); return; }
        if (action === 'remove') {
            card.remove();
            if (!$$('#llmProviderList .llm-provider-card').length) list.innerHTML = '<div class="llm-empty"><span>＋</span><strong>还没有大模型接口</strong><small>添加接口后可配置模型与连接方式</small></div>';
            refreshLlmOrder(); markLlmDirty(); return;
        }
        if (action === 'move-up') { const previous = card.previousElementSibling; if (previous?.classList.contains('llm-provider-card')) list.insertBefore(card, previous); refreshLlmOrder(); markLlmDirty(card); return; }
        if (action === 'move-down') { const next = card.nextElementSibling; if (next?.classList.contains('llm-provider-card')) next.after(card); refreshLlmOrder(); markLlmDirty(card); return; }
        const secretMatch = action.match(/^(reveal|clear)-(key|proxy)$/); if (!secretMatch) return;
        const [, command, kind] = secretMatch; const fieldName = kind === 'key' ? 'api_key' : 'proxy_url'; const input = card.querySelector(`[data-llm-field="${fieldName}"]`);
        if (command === 'reveal') { input.type = input.type === 'password' ? 'text' : 'password'; button.textContent = input.type === 'password' ? '显示' : '隐藏'; return; }
        card.dataset[kind === 'key' ? 'keyDirty' : 'proxyDirty'] = '1'; input.value = ''; input.type = 'password'; input.placeholder = '保存后清除'; markLlmDirty(card);
    });
    $('#refreshControl').addEventListener('click', loadControlState);
    $('#controlSection').addEventListener('click', async (event) => {
        const lifecycleButton = event.target.closest('[data-desired-state]');
        if (lifecycleButton) {
            const scope = lifecycleButton.dataset.controlScope || 'worker';
            const workerId = lifecycleButton.dataset.workerId || '';
            const desiredState = lifecycleButton.dataset.desiredState || '';
            if (scope === 'worker' && !workerId) { showToast('实例标识缺失，无法下发控制指令'); return; }
            if (lifecycleControlsLocked(scope, workerId)) {
                showToast('控制请求进行中，请稍后再试');
                return;
            }
            if (scope === 'global' && desiredState === 'stopped' && !(await confirmStopAll())) return;
            setDesiredLifecycleState(scope, workerId, desiredState);
            return;
        }
        const button = event.target.closest('[data-control-action]'); if (!button) return;
        const action = button.dataset.controlAction; let payload = {};
        if (button.dataset.commandValue !== undefined) payload.value = button.dataset.commandValue;
        if (action === 'refresh_instances' || action === 'test_database') { loadControlState(); return; }
        if (action === 'save_account_limit') {
            const accountId = button.dataset.commandValue || '';
            const input = button.closest('.control-instance-card')?.querySelector('[data-account-limit]');
            const rawLimit = String(input?.value ?? '').trim();
            const dailyLimit = Number(rawLimit);
            if (!accountId || !rawLimit || !Number.isInteger(dailyLimit) || dailyLimit < ACCOUNT_DAILY_LIMIT_MIN || dailyLimit > ACCOUNT_DAILY_LIMIT_MAX) {
                input?.setCustomValidity(`请输入 ${ACCOUNT_DAILY_LIMIT_MIN} 到 ${ACCOUNT_DAILY_LIMIT_MAX} 之间的整数`);
                input?.reportValidity();
                showToast(`账号上限必须是 ${ACCOUNT_DAILY_LIMIT_MIN} 到 ${ACCOUNT_DAILY_LIMIT_MAX} 之间的整数`);
                return;
            }
            input.setCustomValidity('');
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            await updateControlResource(`/api/control/accounts/${encodeURIComponent(accountId)}`, { dailyLimit }, '账号配额已保存');
            if (button.isConnected) {
                button.disabled = false;
                button.removeAttribute('aria-busy');
            }
            return;
        }
        if (action === 'refresh_instances') { loadControlState(); return; }
    });
    $('#controlSection').addEventListener('input', (event) => {
        const input = event.target.closest('[data-account-limit]');
        if (!input) return;
        input.setCustomValidity('');
        const accountId = input.dataset.accountLimit || '';
        $$('[data-account-limit]').forEach((peer) => {
            if (peer !== input && peer.dataset.accountLimit === accountId) peer.value = input.value;
        });
    });
    $('#liveLogAccountFilter').addEventListener('change', (event) => { state.logAccount = event.target.value; renderLiveLogs(); });
    $('#liveLogSenderFilter').addEventListener('change', (event) => { state.logSender = event.target.value; renderLiveLogs(); });
    $$('.live-log-segments [data-log-verbosity]').forEach((button) => button.addEventListener('click', () => setLogVerbosity(button.dataset.logVerbosity)));
    document.addEventListener('click', (event) => { if (!event.target.closest('.column-manager')) $('#columnManagerMenu').hidden = true; });
    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (!$('#deleteConfirm').hidden) closeDeleteConfirm(false);
        if (!$('#controlStopConfirm').hidden) closeStopAllConfirm(false);
        if (!$('#authPrompt').hidden) cancelAuthPrompt();
    });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { closeDrawer(); $('#columnManagerMenu').hidden = true; } });
}

restoreTablePreferences();
applyTheme(document.documentElement.dataset.theme, false);
initReportDrag();
bindChartInteractions();
bindTrendInteractions();
initDatePicker();
bindTableInteractions();
initMapNavigation();
bindEvents();
setLiveLogsExpanded(false);
setLogsPaused(false);
setLogVerbosity(state.logVerbosity, false);
loadData({ silent: true });
loadRuntime().then(connectRuntimeStream);
loadControlState();
loadAdmin();

// 托管轮询：页面隐藏时暂停，避免切走标签页 / 锁屏后仍持续打接口。
const POLLERS = [
    { fn: loadRuntime, interval: 2000, timer: null },
    { fn: loadControlState, interval: 3000, timer: null },
];

function startPolling() {
    POLLERS.forEach((poller) => {
        if (poller.timer === null) poller.timer = setInterval(poller.fn, poller.interval);
    });
}

function stopPolling() {
    POLLERS.forEach((poller) => {
        if (poller.timer !== null) { clearInterval(poller.timer); poller.timer = null; }
    });
}

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        stopPolling();
    } else {
        // 恢复可见立即补一次，再重启定时轮询。
        loadRuntime();
        loadControlState();
        startPolling();
    }
});

startPolling();
