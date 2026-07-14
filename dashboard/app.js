const state = {
    payload: null,
    records: [],
    range: '30',
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
    keyword: '',
    mapProvince: '',
    mapCity: '',
    mapLevel: 'province',
    mapView: { scale: 1, x: 0, y: 0, suppressClick: false },
    industry: '',
    role: '',
    exactDate: '',
    page: 1,
    pageSize: 10,
    sort: { key: 'loggedAt', direction: 'desc' },
    selectedIds: new Set(),
    visibleColumns: new Set(['company', 'salary', 'city', 'industry', 'experience', 'education', 'loggedAt', 'score', 'status']),
    columnOrder: ['company', 'salary', 'city', 'industry', 'experience', 'education', 'loggedAt', 'score', 'status'],
    columnWidths: {},
    density: 'default',
    chinaGeo: null,
    runtime: null,
    runtimeCursor: 0,
    liveEvents: [],
    logsPaused: false,
    logAccount: 'all',
    logClearedCursor: 0,
    adminConfig: null,
    prompts: [],
    currentPrompt: '',
    currentResume: '',
    control: null,
    controlOnline: false,
    llm: null,
};

const COLORS = ['var(--cyan)', 'var(--violet)', 'var(--orange)', 'var(--green)', 'var(--red)', 'var(--blue)'];
const STATUS_LABELS = { sent: '已投递', queued: '进行中', reserved: '待发送', duplicate: '重复投递', failed_unknown: '异常' };
const TODAY_TARGET_FALLBACK = 20;

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
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const numberFormat = new Intl.NumberFormat('zh-CN');
let pendingDeleteConfirmation = null;

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

function mapCityName(record) {
    const city = recordCity(record).replace(/市$/, '');
    if (CITY_COORDINATES[city]) return city;
    return Object.keys(CITY_COORDINATES).find((name) => city.startsWith(name) || String(record.location || '').startsWith(name)) || '未知城市';
}

function getRangeRecords() {
    if (state.range === 'all') return [...state.records];
    const days = Number(state.range);
    const now = new Date();
    const cutoff = new Date(now.getFullYear(), now.getMonth(), now.getDate() - days + 1);
    return state.records.filter((record) => {
        const date = parseDate(record.loggedAt);
        return date && date >= cutoff;
    });
}

function getFilteredRecords(ignoreRange = false) {
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
        if (state.mapProvince && normalizeProvince(record.city || record.location) !== state.mapProvince) return false;
        if (state.mapCity && mapCityName(record) !== state.mapCity) return false;
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
        start = new Date();
        start = new Date(start.getFullYear(), start.getMonth(), start.getDate() - Number(state.range) + 1);
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
        if (city === '未知城市' || !CITY_COORDINATES[city]) { unknown += 1; return; }
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
    const ratio = value / max;
    if (ratio > .8) return 'var(--map-5)'; if (ratio > .6) return 'var(--map-4)'; if (ratio > .35) return 'var(--map-3)'; if (ratio > .15) return 'var(--map-2)'; return 'var(--map-1)';
}

function projectCoordinate([longitude, latitude]) {
    return [(longitude - 73) / 62 * 670, (54 - latitude) / 36 * 400];
}

function geometryPath(geometry) {
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
    return polygons.map((polygon) => polygon.map((ring) => ring.map((coordinate, index) => {
        const [x, y] = projectCoordinate(coordinate);
        return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ') + ' Z').join(' ')).join(' ');
}

async function ensureChinaGeo() {
    if (state.chinaGeo) return state.chinaGeo;
    const response = await fetch('/dashboard/china.json', { cache: 'force-cache' });
    if (!response.ok) throw new Error('中国地图数据加载失败');
    state.chinaGeo = await response.json();
    return state.chinaGeo;
}

async function renderMap(records) {
    const container = $('#chinaMap');
    try {
        const geo = await ensureChinaGeo();
        const metric = $('#mapMetric').value;
        const level = state.mapLevel === 'city' ? 'city' : 'province';
        const { stats, unknown } = level === 'city' ? cityStats(records) : provinceStats(records);
        const values = [...stats.values()].map((item) => mapMetricValue(item, metric));
        const max = Math.max(...values, 0);
        const features = geo.features.filter((feature) => feature.properties?.name);
        let paths = '', labels = '', heatLayer = '';
        if (level === 'province') {
            paths = features.map((feature) => {
                const name = normalizeProvince(feature.properties.name), item = stats.get(name) || { count: 0, salaries: [] };
                const value = mapMetricValue(item, metric);
                const title = metric === 'salary' ? `${name}：${value ? value.toFixed(1) + 'K' : '暂无薪资'}` : `${name}：${value} 份`;
                return `<path class="geo-province ${state.mapProvince === name ? 'is-active' : ''}" data-province="${escapeHtml(name)}" d="${geometryPath(feature.geometry)}" fill="${mapColor(value, max)}" fill-rule="evenodd"><title>${title}，点击筛选</title></path>`;
            }).join('');
            labels = features.map((feature) => {
                const name = normalizeProvince(feature.properties.name), center = feature.properties.centroid || feature.properties.center;
                if (!center) return '';
                const [x, y] = projectCoordinate(center), hot = stats.get(name)?.count;
                return `<text class="geo-label ${hot ? 'hot' : ''}" x="${x}" y="${y}">${name.length > 3 ? name.slice(0, 3) : name}</text>`;
            }).join('');
        } else {
            paths = features.map((feature) => `<path class="geo-province city-layer" d="${geometryPath(feature.geometry)}" fill-rule="evenodd"></path>`).join('');
            heatLayer = [...stats.entries()]
                .sort((a, b) => mapMetricValue(a[1], metric) - mapMetricValue(b[1], metric))
                .map(([city, item]) => {
                    const value = mapMetricValue(item, metric), ratio = max ? value / max : 0;
                    const [x, y] = projectCoordinate(CITY_COORDINATES[city]);
                    const radius = 4.2 + Math.sqrt(ratio) * 8.8;
                    const title = metric === 'salary' ? `${city}：平均 ${value.toFixed(1)}K，${item.count} 份投递` : `${city}：${item.count} 份投递`;
                    const displayValue = metric === 'salary' ? value.toFixed(0) : item.count;
                    return `<g class="city-heat-point ${state.mapCity === city ? 'is-active' : ''}" data-city="${escapeHtml(city)}"><circle class="city-heat-halo" cx="${x}" cy="${y}" r="${(radius + 5).toFixed(1)}"></circle><circle class="city-heat-core" cx="${x}" cy="${y}" r="${radius.toFixed(1)}" fill="${mapColor(value, max)}"><title>${title}，点击筛选</title></circle><text class="city-heat-value" x="${x}" y="${(y + 1.9).toFixed(1)}">${displayValue}</text><text class="city-heat-label ${ratio > .45 ? 'hot' : ''}" x="${x}" y="${(y + radius + 8).toFixed(1)}">${city}</text></g>`;
                }).join('');
            if (!heatLayer) heatLayer = '<text class="city-unknown-note" x="335" y="205" text-anchor="middle">暂无可定位的地级市投递数据</text>';
        }
        const { scale, x, y } = state.mapView;
        container.innerHTML = `<svg viewBox="0 0 670 400" preserveAspectRatio="xMidYMid meet"><g class="map-viewport" transform="translate(${x} ${y}) scale(${scale})">${paths}${labels}${heatLayer}</g></svg>`;
        updateMapZoomLabel();
        const known = Math.max(0, records.length - unknown);
        $('#knownLocationRate').textContent = `已识别 ${records.length ? Math.round(known / records.length * 100) : 0}%`;
        $('#locationRankingTitle').textContent = level === 'city' ? '热门地级市' : '热门省份';
        $('#mapInteractionHint').textContent = level === 'city' ? '地级市热力 · 点击城市筛选 · 滚轮缩放 · 按住拖动' : '省份热力 · 点击筛选 · 滚轮缩放 · 按住拖动';
        $('#mapScaleMin').textContent = metric === 'salary' ? '低' : '少';
        $('#mapScaleMax').textContent = metric === 'salary' ? '高' : '多';
        const ranked = [...stats.entries()].filter(([, item]) => item.count).sort((a, b) => mapMetricValue(b[1], metric) - mapMetricValue(a[1], metric)).slice(0, 7);
        const rankingMax = ranked.length ? Math.max(...ranked.map(([, item]) => mapMetricValue(item, metric)), 1) : 1;
        $('#locationList').innerHTML = ranked.length ? ranked.map(([name, item], index) => {
            const value = mapMetricValue(item, metric), display = metric === 'salary' ? `${value.toFixed(1)}K` : item.count;
            const selectedLocation = level === 'city' ? state.mapCity : state.mapProvince;
            return `<div class="ranking-item ${selectedLocation === name ? 'is-active' : ''}" data-map-location="${escapeHtml(name)}"><span>${index + 1}</span><span class="ranking-name">${escapeHtml(name)}</span><strong class="ranking-count">${display}</strong><div class="ranking-bar"><i style="width:${value / rankingMax * 100}%"></i></div></div>`;
        }).join('') : '<div class="empty-state" style="padding:38px 0"><strong>历史地区尚未采集</strong><p>新投递会自动进入热力地图</p></div>';
    } catch (error) {
        container.innerHTML = '<div class="empty-state"><strong>中国地图加载失败</strong><p>请刷新页面重试</p></div>';
    }
}

function clampMapView() {
    const view = state.mapView;
    view.scale = Math.max(1, Math.min(6, view.scale));
    const minX = -670 * (view.scale - 1), minY = -400 * (view.scale - 1);
    view.x = Math.max(minX, Math.min(0, view.x));
    view.y = Math.max(minY, Math.min(0, view.y));
}

function applyMapTransform() {
    clampMapView();
    const viewport = $('#chinaMap .map-viewport');
    if (viewport) viewport.setAttribute('transform', `translate(${state.mapView.x} ${state.mapView.y}) scale(${state.mapView.scale})`);
    updateMapZoomLabel();
}

function updateMapZoomLabel() {
    const label = $('#mapZoomValue');
    if (label) label.textContent = `${Math.round(state.mapView.scale * 100)}%`;
}

function zoomMap(nextScale, centerX = 335, centerY = 200) {
    const view = state.mapView, previousScale = view.scale;
    const scale = Math.max(1, Math.min(6, nextScale));
    if (scale === previousScale) return;
    view.x = centerX - ((centerX - view.x) / previousScale) * scale;
    view.y = centerY - ((centerY - view.y) / previousScale) * scale;
    view.scale = scale;
    applyMapTransform();
}

function resetMapView() {
    Object.assign(state.mapView, { scale: 1, x: 0, y: 0, suppressClick: false });
    applyMapTransform();
}

function initMapNavigation() {
    const map = $('#chinaMap');
    let pointer = null;
    map.addEventListener('wheel', (event) => {
        event.preventDefault();
        const svg = map.querySelector('svg'); if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const centerX = (event.clientX - rect.left) / rect.width * 670;
        const centerY = (event.clientY - rect.top) / rect.height * 400;
        zoomMap(state.mapView.scale * Math.exp(-event.deltaY * .0012), centerX, centerY);
    }, { passive: false });
    map.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        const svg = map.querySelector('svg'); if (!svg) return;
        pointer = { id: event.pointerId, startX: event.clientX, startY: event.clientY, x: state.mapView.x, y: state.mapView.y, moved: false, rect: svg.getBoundingClientRect() };
        map.setPointerCapture(event.pointerId); map.classList.add('is-panning');
    });
    map.addEventListener('pointermove', (event) => {
        if (!pointer || pointer.id !== event.pointerId || state.mapView.scale <= 1) return;
        const dx = event.clientX - pointer.startX, dy = event.clientY - pointer.startY;
        if (Math.hypot(dx, dy) > 5) pointer.moved = true;
        state.mapView.x = pointer.x + dx / pointer.rect.width * 670;
        state.mapView.y = pointer.y + dy / pointer.rect.height * 400;
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
        const city = event.target.closest('.city-heat-point');
        if (city && !state.mapView.suppressClick) {
            state.mapCity = state.mapCity === city.dataset.city ? '' : city.dataset.city;
            state.mapProvince = '';
            state.page = 1; updateDashboard(); showToast(state.mapCity ? `已筛选地级市 ${state.mapCity}` : '已取消城市筛选');
            return;
        }
        const province = event.target.closest('.geo-province');
        if (!province?.dataset.province || state.mapView.suppressClick) return;
        state.mapProvince = state.mapProvince === province.dataset.province ? '' : province.dataset.province;
        state.mapCity = '';
        state.page = 1; updateDashboard(); showToast(state.mapProvince ? `已筛选 ${state.mapProvince}` : '已取消地图筛选');
    });
    map.addEventListener('dblclick', (event) => { event.preventDefault(); resetMapView(); showToast('地图视图已复位'); });
    $('#mapZoomIn').addEventListener('click', () => zoomMap(state.mapView.scale * 1.3));
    $('#mapZoomOut').addEventListener('click', () => zoomMap(state.mapView.scale / 1.3));
    $('#mapResetView').addEventListener('click', () => { resetMapView(); showToast('地图视图已复位'); });
    $$('#mapLevelSwitch button').forEach((button) => button.addEventListener('click', () => {
        state.mapLevel = button.dataset.mapLevel === 'city' ? 'city' : 'province';
        state.mapProvince = '';
        state.mapCity = '';
        $$('#mapLevelSwitch button').forEach((item) => item.classList.toggle('active', item === button));
        resetMapView();
        state.page = 1;
        updateDashboard();
        showToast(state.mapLevel === 'city' ? '已切换地级市热力层' : '已切换省份热力层');
    }));
    $('#locationList').addEventListener('click', (event) => {
        const item = event.target.closest('[data-map-location]'); if (!item) return;
        const name = item.dataset.mapLocation;
        if (state.mapLevel === 'city') {
            state.mapCity = state.mapCity === name ? '' : name; state.mapProvince = '';
        } else {
            state.mapProvince = state.mapProvince === name ? '' : name; state.mapCity = '';
        }
        state.page = 1; updateDashboard();
        showToast((state.mapCity || state.mapProvince) ? `已筛选 ${name}` : '已取消地图筛选');
    });
}

function salaryBucket(value) {
    if (value < 8) return '8K 以下'; if (value < 12) return '8–12K'; if (value < 16) return '12–16K'; if (value < 20) return '16–20K'; if (value < 30) return '20–30K'; return '30K 以上';
}

function renderSalary(records) {
    const labels = ['8K 以下', '8–12K', '12–16K', '16–20K', '20–30K', '30K 以上'];
    const counts = Object.fromEntries(labels.map((label) => [label, 0])), valid = records.filter((record) => Number.isFinite(record.salaryK));
    valid.forEach((record) => { counts[salaryBucket(record.salaryK)] += 1; });
    const max = Math.max(...Object.values(counts), 1);
    $('#salaryCoverage').textContent = `识别率 ${records.length ? Math.round(valid.length / records.length * 100) : 0}%`;
    const ranges = [[0, 8], [8, 12], [12, 16], [16, 20], [20, 30], [30, null]];
    $('#salaryChart').innerHTML = labels.map((label, index) => {
        const [minimum, maximum] = ranges[index];
        const active = state.minSalary === minimum && state.maxSalary === maximum;
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
        if (Array.isArray(saved.visibleColumns)) state.visibleColumns = new Set(saved.visibleColumns.filter((key) => validKeys.includes(key)));
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
            const [minimum, maximum] = salary.dataset.salaryFilter.split(':');
            const min = Number(minimum), max = maximum === '' ? null : Number(maximum);
            const active = state.minSalary === min && state.maxSalary === max;
            return applyChartFilter({ minSalary: active ? null : min, maxSalary: active ? null : max });
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
    const details = [['薪资范围', record.salary], ['城市 / 地区', record.city || record.location || '历史记录未采集'], ['经验要求', record.experience || '未采集'], ['学历要求', record.education || '未采集'], ['所属行业', record.industry || '未采集'], ['搜索关键词', record.keyword || '未记录'], ['投递日期', `${datetime.date} ${datetime.time}`], ['匹配度', record.score ?? '未记录'], ['投递账号', record.accountId || '默认账号']];
    $('#drawerDetails').innerHTML = details.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('');
    $('#detailDrawer').classList.add('open'); $('#drawerBackdrop').classList.add('open'); $('#detailDrawer').setAttribute('aria-hidden', 'false');
}

function closeDrawer() { $('#detailDrawer').classList.remove('open'); $('#drawerBackdrop').classList.remove('open'); $('#detailDrawer').setAttribute('aria-hidden', 'true'); }

function updateDashboard() {
    const records = getFilteredRecords();
    renderMetrics(records); renderTrend(getFilteredRecords(true)); renderFunnel(records); renderMap(records); renderSalary(records); renderRoles(records); renderIndustry(records); renderRecords(records); renderFilterChips(); updateMonitorMetrics(records);
}

function updateMonitorMetrics(records = getFilteredRecords()) {
    const today = localDateKey(new Date());
    $('#monitorTodayDelivered').textContent = records.filter((record) => record.loggedAt?.startsWith(today)).length;
    $('#monitorFailures').textContent = records.filter((record) => record.status === 'failed_unknown').length + Number(state.payload?.summary?.queueFailures || 0);
}

function renderRuntime() {
    const runtime = state.runtime || { clients: [], activeClientCount: 0, connectedClientCount: 0 };
    $('#activeClients').textContent = runtime.activeClientCount || 0; $('#connectedClients').textContent = runtime.connectedClientCount || 0; $('#navControlAlerts').textContent = runtime.activeClientCount || 0;
    const ttl = Number(runtime.clientTtlSeconds || 120); $('#heartbeatWindowHint').textContent = ttl >= 60 ? `${Math.round(ttl / 60)} 分钟内有心跳` : `${ttl} 秒内有心跳`;
}

function eventMessage(event) {
    const payload = event.payload || {};
    if (event.type === 'script_log') return { level: payload.level || 'info', source: payload.accountId || payload.role || '脚本', message: payload.message || '', time: payload.loggedAt || event.loggedAt };
    if (event.type === 'job_action') return { level: payload.action?.includes('fail') ? 'error' : 'action', source: payload.accountId || '投递动作', message: `${payload.action || 'action'} ${payload.company || ''} ${payload.title || ''}`.trim(), time: payload.loggedAt || event.loggedAt };
    return { level: 'action', source: '系统', message: `${event.type}${payload.accountId ? ` · ${payload.accountId}` : ''}`, time: event.loggedAt };
}

function renderLiveLogs() {
    const container = $('#liveLogs'); container.replaceChildren();
    const accounts = [...new Set([...(state.runtime?.clients || []).map((client) => client.accountId), ...state.liveEvents.map((event) => event.payload?.accountId)].filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    const filter = $('#liveLogAccountFilter'); const current = state.logAccount;
    filter.innerHTML = '<option value="all">全部账号</option>' + accounts.map((account) => `<option value="${escapeHtml(account)}">${escapeHtml(account)}</option>`).join('');
    filter.value = accounts.includes(current) ? current : 'all'; state.logAccount = filter.value;
    state.liveEvents.filter((event) => (event.id || 0) > state.logClearedCursor && (state.logAccount === 'all' || event.payload?.accountId === state.logAccount)).slice(-160).forEach((event) => {
        const entry = eventMessage(event), row = document.createElement('div'); row.className = `live-log ${entry.level}`;
        const time = document.createElement('time'); time.textContent = (entry.time || '').slice(11, 19);
        const source = document.createElement('b'); source.textContent = entry.source;
        const message = document.createElement('span'); message.textContent = entry.message;
        row.append(time, source, message); container.appendChild(row);
    });
    if (!state.logsPaused) container.scrollTop = container.scrollHeight;
}

async function loadRuntime() {
    try {
        const response = await fetch('/api/runtime', { cache: 'no-store' }); if (!response.ok) return;
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
    const clientDecision = instances.find((item) => item.currentDecision && Object.keys(item.currentDecision).length);
    const queue = controlArray(data.queue || data.jobs).length ? controlArray(data.queue || data.jobs) : instances.flatMap((item) => controlArray(item.queue).map((entry) => ({ workerId: item.workerId, ...entry })));
    const currentKeywords = [...new Set(instances.map((item) => item.keyword).filter(Boolean))];
    return {
        global,
        task,
        instances,
        decision: data.currentDecision || data.decision || (clientDecision ? { workerId: clientDecision.workerId, ...clientDecision.currentDecision } : null),
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

function renderControlInstances(instances) {
    const container = $('#controlInstanceList'); container.replaceChildren();
    const online = instances.filter((item) => item.online !== false && (item.online || item.state !== 'offline')).length;
    $('#controlInstanceSummary').textContent = instances.length ? `${online} 个在线 / ${instances.length} 个实例` : '等待脚本接入';
    if (!instances.length) { container.innerHTML = '<div class="control-empty">尚未收到浏览器实例心跳</div>'; return; }
    instances.forEach((item) => {
        const workerId = item.workerId || item.id || '';
        const card = document.createElement('div'); card.className = `control-instance-card ${item.online ? 'online' : ''}`;
        card.innerHTML = `<div class="control-instance-top"><div class="control-instance-name"><i></i><strong>${escapeHtml(item.alias || item.accountId || workerId || '未命名实例')}</strong></div><span class="control-instance-state">${escapeHtml(item.phase || item.state || '等待')}</span></div><div class="control-instance-detail"><span>账号标识<b>${escapeHtml(item.accountId || '—')}</b></span><span>当前关键词<b>${escapeHtml(item.keyword || '—')}</b></span><span>当前岗位<b>${escapeHtml(item.currentJob || item.title || '—')}</b></span><span>今日投递<b>${Number(item.todayDelivered ?? item.counters?.sent ?? 0)}</b></span><span>运行状态<b>${item.paused ? '本地暂停' : '运行中'}</b></span><span>最后心跳<b>${escapeHtml(controlTime(item.lastSeen).slice(11) || '—')}</b></span></div>`;
        container.appendChild(card);
    });
}

function renderCurrentDecision(decision) {
    const container = $('#currentDecision');
    if (!decision) { container.className = 'current-decision-empty'; container.textContent = '暂无正在评分的岗位'; $('#decisionState').textContent = '等待岗位'; return; }
    const stars = Number(decision.stars ?? decision.remainingStars ?? 5), deductions = controlArray(decision.deductions || decision.matches);
    container.className = 'current-decision'; $('#decisionState').textContent = decision.discarded ? '准备丢弃' : (decision.state || '评分完成');
    container.innerHTML = `<div class="decision-company">${escapeHtml(decision.company || '公司未识别')}</div><div class="decision-title">${escapeHtml(decision.title || '岗位未识别')}</div><div class="decision-stars" aria-label="剩余 ${stars} 星">${controlStars(stars)}</div><div class="decision-deductions">${deductions.length ? deductions.map((item) => `<div><span>${escapeHtml(item.keyword || item.name || '规则命中')}</span><b>−${Number(item.stars ?? item.value ?? 1)} 星</b></div>`).join('') : '<div><span>未命中扣星规则</span><b>0 星</b></div>'}</div>`;
}

function renderAccountQuotas(accounts) {
    $('#accountQuotaBody').innerHTML = accounts.length ? accounts.map((item) => { const used = Number(item.used ?? item.count ?? 0), limit = Number(item.limit ?? item.dailyLimit ?? 0); return `<tr><td><strong>${escapeHtml(item.alias || item.accountId || '未命名账号')}</strong><small>${escapeHtml(item.workerId || '')}</small></td><td>${used} / ${limit || '∞'}</td><td><input type="number" min="0" value="${limit}" data-account-limit="${escapeHtml(item.accountId || '')}"></td><td><span class="control-status ok">监控中</span></td><td><button data-control-action="save_account_limit" data-command-value="${escapeHtml(item.accountId || '')}">保存上限</button></td></tr>`; }).join('') : '<tr><td colspan="5"><div class="control-empty">暂无账号配额数据</div></td></tr>';
}

function renderControlCenter() {
    const data = normalizedControlState();
    renderControlInstances(data.instances); renderCurrentDecision(data.decision); renderAccountQuotas(data.accounts);
    renderDailyGoal();
}

async function loadControlState() {
    try {
        const data = await apiJson('/api/control/state'); state.control = data; state.controlOnline = true;
        $('.control-sync').className = 'control-sync connected'; $('#controlSyncText').textContent = '控制服务已连接'; $('#controlUpdatedAt').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
    } catch (_) {
        state.controlOnline = false; $('.control-sync').className = 'control-sync disconnected'; $('#controlSyncText').textContent = '控制接口暂不可用'; $('#controlUpdatedAt').textContent = '监控数据仍会继续显示';
    }
    renderControlCenter();
}

async function updateControlResource(url, payload, successMessage) {
    try { const result = await apiJson(url, { method: 'PUT', body: JSON.stringify(payload) }); showToast(successMessage); await loadControlState(); return result; }
    catch (error) { showToast(`保存失败：${error.message}`); return null; }
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
}

function toggleTheme() {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    applyTheme(next);
    showToast(next === 'light' ? '已切换白天主题' : '已切换深蓝主题');
}

function exportRecords(records, filenamePrefix = '投递报表') {
    const rows = [['公司', '岗位', '行业', '薪资', '最低K', '最高K', '城市', '经验', '学历', '关键词', '投递时间', '匹配度', '状态', '账号']];
    records.forEach((record) => rows.push([record.company, record.title, record.industry, record.salary, record.salaryMinK ?? '', record.salaryMaxK ?? '', record.city || record.location, record.experience, record.education, record.keyword, record.loggedAt, record.score ?? '', STATUS_LABELS[record.status] || record.status, record.accountId]));
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
        const response = await fetch('/api/dashboard', { cache: 'no-store' }); if (!response.ok) throw new Error(`HTTP ${response.status}`);
        state.payload = await response.json(); state.records = Array.isArray(state.payload.deliveries) ? state.payload.deliveries : [];
        const existingIds = new Set(state.records.map((record) => record.id)); state.selectedIds = new Set([...state.selectedIds].filter((id) => existingIds.has(id)));
        const generated = parseDate(state.payload.generatedAt); $('#lastUpdated').textContent = generated ? `${String(generated.getHours()).padStart(2, '0')}:${String(generated.getMinutes()).padStart(2, '0')} 已同步` : '已同步'; $('#footerTime').textContent = `最后更新 ${state.payload.generatedAt || '—'}`;
        populateFilters(); updateDashboard(); if (!silent) showToast('数据已刷新');
        markDataConnection(true);
    } catch (error) { console.error(error); markDataConnection(false); if (!state.records.length) { state.payload = { summary: {}, deliveries: [] }; state.records = []; populateFilters(); updateDashboard(); } if (!silent) showToast('无法读取统计数据'); }
    finally { $('#refreshButton').classList.remove('loading'); }
}

async function apiJson(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    return data;
}

const CONFIG_LABELS = {
    introduce: '固定打招呼语', character: '回复风格', resume_content: '兼容简历内容', tags: '搜索关键词',
    backend: '后端参数', job_score_delay_base_ms: '评分基础延迟（ms）', job_score_delay_jitter_ms: '评分随机延迟（ms）', daily_greet_limit: '每日投递上限', delivery_db_path: '投递数据库文件',
    frontend: '浏览器脚本参数', serverHost: '本地服务地址', resumeIndex: '简历序号', thread: '匹配阈值', timestampTimeout: '页面通信有效期（ms）', onlyGreet: '仅自动打招呼', manualFilterWaitMs: '手动筛选等待（ms）', roundRestartDelayMs: '轮次重启等待（ms）', maxEmptyRounds: '最大连续空轮', detailTimeout: '职位详情超时（ms）', greetTimeout: '打招呼超时（ms）', preloadScrollPixels: '预加载滚动距离（px）', preloadScrollWaitMs: '预加载滚动等待（ms）', preloadStableRoundsLimit: '预加载稳定轮数', preloadMaxRounds: '预加载最大轮数', preloadActivateCardEvery: '每隔几轮激活岗位卡', preloadActivateCardWaitMs: '激活岗位卡等待（ms）',
    scoring: '岗位扣星规则', title_deduction_keywords: '职位名称扣星词', detail_deduction_keywords: '职位描述扣星词'
};

function configLabel(key) { return CONFIG_LABELS[key] || key; }

function makeConfigControl(path, key, value) {
    const label = document.createElement('label'); label.className = 'config-field';
    const caption = document.createElement('span'); caption.textContent = configLabel(key); label.appendChild(caption);
    let input;
    if (typeof value === 'boolean') {
        label.classList.add('config-switch'); input = document.createElement('input'); input.type = 'checkbox'; input.checked = value; label.prepend(input);
    } else if (Array.isArray(value)) {
        input = document.createElement('textarea'); input.rows = Math.min(8, Math.max(3, value.length)); input.value = value.join('\n'); input.dataset.valueType = 'array';
    } else if (typeof value === 'number') {
        input = document.createElement('input'); input.type = 'number'; input.value = value; input.dataset.valueType = 'number';
    } else if (key === 'introduce' || key === 'character' || key === 'resume_content') {
        input = document.createElement('textarea'); input.rows = key === 'resume_content' ? 6 : 3; input.value = value || '';
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
        starButtons.forEach((button) => button.addEventListener('click', () => setStars(button.dataset.stars)));
        selector.addEventListener('keydown', (event) => { const current = Number(scoreInput.value); if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') { event.preventDefault(); setStars(current - 1); } else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') { event.preventDefault(); setStars(current + 1); } else if (event.key === 'Home') { event.preventDefault(); setStars(1); } else if (event.key === 'End') { event.preventDefault(); setStars(5); } });
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'score-card-remove'; remove.textContent = '×'; remove.title = '删除关键词'; remove.addEventListener('click', () => { card.remove(); summary.textContent = `${configLabel(key)}（${grid.children.length} 条）`; });
        card.append(keywordInput, scoreInput, remove, selector, result); setStars(score); grid.appendChild(card); summary.textContent = `${configLabel(key)}（${grid.children.length} 条）`;
    };
    Object.entries(values).forEach(([keyword, score]) => addCard(keyword, score));
    const addButton = document.createElement('button'); addButton.type = 'button'; addButton.className = 'score-card-add'; addButton.textContent = '＋ 新增关键词'; addButton.addEventListener('click', () => { addCard(); grid.lastElementChild?.querySelector('.score-keyword')?.focus(); });
    details.append(grid, addButton); container.appendChild(details);
}

function fillConfigForm(data) {
    const config = data.config; state.adminConfig = config;
    const form = $('#visualConfigForm'); form.replaceChildren();
    const basics = document.createElement('fieldset'); basics.innerHTML = '<legend>基础资料</legend><div class="config-field-grid"></div>'; const basicGrid = basics.querySelector('div');
    Object.entries(config).filter(([key]) => !['backend', 'frontend', 'scoring'].includes(key)).forEach(([key, value]) => basicGrid.appendChild(makeConfigControl(key, key, value))); form.appendChild(basics);
    ['backend', 'frontend'].forEach((groupKey) => { const fieldset = document.createElement('fieldset'); const legend = document.createElement('legend'); legend.textContent = configLabel(groupKey); fieldset.appendChild(legend); const grid = document.createElement('div'); grid.className = 'config-field-grid'; Object.entries(config[groupKey] || {}).forEach(([key, value]) => grid.appendChild(makeConfigControl(`${groupKey}.${key}`, key, value))); fieldset.appendChild(grid); form.appendChild(fieldset); });
    const scoring = document.createElement('fieldset'); const scoringLegend = document.createElement('legend'); scoringLegend.textContent = configLabel('scoring'); scoring.appendChild(scoringLegend); const scoringHint = document.createElement('p'); scoringHint.className = 'scoring-model-hint'; scoringHint.textContent = '每个岗位初始为 5 星。命中关键词后按规则扣星；同一段文字优先匹配更长的关键词。剩余星级小于 0 时直接丢弃岗位。'; scoring.appendChild(scoringHint); Object.entries(config.scoring || {}).forEach(([key, values]) => renderScoringGroup(scoring, key, values)); form.appendChild(scoring);
    $('#adminSaveState').textContent = '配置已载入';
}

const LLM_KEEP_SECRET = '__KEEP__';

function llmProviderCard(provider = {}) {
    const card = document.createElement('div'); card.className = 'llm-provider-card';
    const configured = Boolean(provider.apiKeyConfigured);
    // index 用于保存时让后端定位旧 key；新建卡片没有 index。
    card.dataset.index = provider.index != null ? String(provider.index) : '';
    card.dataset.keyConfigured = configured ? '1' : '';
    card.dataset.keyDirty = '';
    const test = provider.__test;
    const testClass = test ? (test.ok ? 'ok' : 'bad') : '';
    const testText = test
        ? (test.ok ? `可用 · ${test.latencyMs ?? '—'}ms` : `失败 · ${escapeHtml(String(test.error || test.status || '未知'))}`)
        : '尚未测活';
    const keyPlaceholder = configured ? `已配置（${escapeHtml(provider.apiKeyMasked || '******')}），留空保留` : '输入 API Key';
    card.innerHTML = `
        <div class="llm-provider-top">
            <label class="llm-enable"><input type="checkbox" data-llm-field="enabled" ${provider.enabled !== false ? 'checked' : ''}><span>启用</span></label>
            <span class="llm-provider-test ${testClass}">${testText}</span>
            <div class="llm-provider-tools">
                <button type="button" data-llm-action="test">测活</button>
                <button type="button" class="danger-action" data-llm-action="remove">删除</button>
            </div>
        </div>
        <div class="llm-provider-grid">
            <label><span>名称</span><input type="text" data-llm-field="name" value="${escapeHtml(provider.name || '')}" placeholder="例如 SenseNova"></label>
            <label><span>接口地址</span><input type="url" data-llm-field="api_base" value="${escapeHtml(provider.api_base || '')}" placeholder="https://.../v1"></label>
            <label><span>模型名称</span><input type="text" data-llm-field="model" value="${escapeHtml(provider.model || '')}" placeholder="例如 deepseek-v4-flash"></label>
            <label><span>API Key</span><input type="password" data-llm-field="api_key" placeholder="${keyPlaceholder}" autocomplete="off"></label>
        </div>`;
    // 标记 key 输入被改动，保存时才发送真实值，否则发送哨兵保留原 key。
    card.querySelector('[data-llm-field="api_key"]').addEventListener('input', () => { card.dataset.keyDirty = '1'; });
    return card;
}

function renderLlmProviders() {
    const list = $('#llmProviderList'); list.replaceChildren();
    const providers = state.llm.providers || [];
    if (!providers.length) { list.innerHTML = '<div class="llm-empty">还没有配置任何接口，点击下方按钮添加。</div>'; return; }
    providers.forEach((provider) => list.appendChild(llmProviderCard(provider)));
}

function collectLlmPayload() {
    const cards = $$('#llmProviderList .llm-provider-card');
    const providers = cards.map((card) => {
        const field = (name) => card.querySelector(`[data-llm-field="${name}"]`);
        const keyInput = field('api_key');
        const keyConfigured = card.dataset.keyConfigured === '1';
        const keyDirty = card.dataset.keyDirty === '1';
        // 未改动且原本已配置 → 用哨兵让后端保留；否则发送输入框实际内容。
        const api_key = keyDirty ? keyInput.value : (keyConfigured ? LLM_KEEP_SECRET : '');
        const indexRaw = card.dataset.index;
        return {
            index: indexRaw === '' ? null : Number(indexRaw),
            name: field('name').value.trim(),
            api_base: field('api_base').value.trim(),
            model: field('model').value.trim(),
            enabled: field('enabled').checked,
            api_key,
        };
    });
    return {
        strategy: $('#llmStrategy').value,
        timeout: Number($('#llmTimeout').value) || 180,
        jobFilter: $('#llmJobFilter').checked,
        providers,
    };
}

function applyLlmConfig(data) {
    state.llm = { providers: (data.providers || []).map((item) => ({ ...item })) };
    $('#llmStrategy').value = data.strategy || 'failover';
    $('#llmTimeout').value = data.timeout || 180;
    $('#llmJobFilter').checked = Boolean(data.jobFilter);
    renderLlmProviders();
}

async function loadLlm() {
    try { applyLlmConfig(await apiJson('/api/admin/llm')); }
    catch (error) { $('#llmNotice').textContent = `接口配置读取失败：${error.message}`; }
}

async function saveLlm() {
    const button = $('#saveLlm'); button.disabled = true;
    try { applyLlmConfig(await apiJson('/api/admin/llm', { method: 'PUT', body: JSON.stringify(collectLlmPayload()) })); showToast('接口配置已保存到 .env 并热加载'); }
    catch (error) { showToast(`保存失败：${error.message}`); }
    finally { button.disabled = false; }
}

function addLlmProvider() {
    const list = $('#llmProviderList'); const empty = list.querySelector('.llm-empty'); if (empty) list.replaceChildren();
    list.appendChild(llmProviderCard({ enabled: true }));
}

async function testLlmProvider(card) {
    const badge = card.querySelector('.llm-provider-test'); badge.className = 'llm-provider-test'; badge.textContent = '测活中…';
    const indexRaw = card.dataset.index;
    if (indexRaw === '') { badge.classList.add('bad'); badge.textContent = '请先保存后再测活'; return; }
    try {
        const result = await apiJson('/api/admin/llm/test', { method: 'POST', body: JSON.stringify({ index: Number(indexRaw) }) });
        if (result.ok) { badge.classList.add('ok'); badge.textContent = `可用 · ${result.latencyMs ?? '—'}ms`; }
        else { badge.classList.add('bad'); badge.textContent = `失败 · ${result.error || result.status || '未知'}`; }
    } catch (error) { badge.classList.add('bad'); badge.textContent = `失败 · ${error.message}`; }
}

async function testAllLlm() {
    const cards = $$('#llmProviderList .llm-provider-card');
    await Promise.all(cards.map((card) => card.dataset.index !== '' ? testLlmProvider(card) : Promise.resolve()));
}

async function loadAdmin() {
    try {
        const [config, resumes, promptData, llm] = await Promise.all([apiJson('/api/admin/config'), apiJson('/api/admin/resumes'), apiJson('/api/admin/prompts'), apiJson('/api/admin/llm')]);
        fillConfigForm(config); renderResumeOptions(resumes); state.prompts = promptData.items || []; renderPromptOptions(); applyLlmConfig(llm);
    } catch (error) { $('#adminSaveState').textContent = '管理接口不可用'; $('#configNotice').textContent = error.message; }
    loadLlm();
}

async function saveAdminConfig() {
    const button = $('#saveConfig'); button.disabled = true;
    try {
        const config = structuredClone(state.adminConfig);
        $$('[data-config-path]').forEach((input) => {
            let value;
            if (input.type === 'checkbox') value = input.checked;
            else if (input.dataset.valueType === 'number') value = Number(input.value);
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
    (data.items || []).forEach((item) => { const option = document.createElement('option'); option.value = item.name; option.textContent = `${item.name}${item.exists ? '' : '（新文件）'}`; select.appendChild(option); });
    state.currentResume = data.selected || select.value; select.value = state.currentResume; if (state.currentResume) loadResume(state.currentResume);
}

async function loadResume(name) {
    try { const data = await apiJson(`/api/admin/resumes/${encodeURIComponent(name)}`); state.currentResume = name; $('#resumeEditor').value = data.content || ''; $('#resumeMeta').textContent = `${data.size || 0} bytes`; } catch (error) { showToast(`读取简历失败：${error.message}`); }
}

async function createResume() {
    const name = $('#newResumeName').value.trim(); if (!name) return showToast('请输入简历文件名');
    state.currentResume = name; $('#resumeEditor').value = ''; $('#resumeMeta').textContent = '新文件，保存后创建'; showToast(`已切换到 ${name}`);
}

async function saveCurrentResume() {
    if (!state.currentResume) return showToast('请先选择简历');
    try { const result = await apiJson(`/api/admin/resumes/${encodeURIComponent(state.currentResume)}`, { method: 'PUT', body: JSON.stringify({ content: $('#resumeEditor').value, select: true }) }); $('#resumeMeta').textContent = `${result.size} bytes · 已保存`; showToast('简历已保存并设为当前简历'); const list = await apiJson('/api/admin/resumes'); renderResumeOptions(list); } catch (error) { showToast(`保存失败：${error.message}`); }
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
    Object.assign(state, { search: '', status: 'all', city: 'all', experience: 'all', education: 'all', minSalary: null, maxSalary: null, keyword: '', mapProvince: '', mapCity: '', industry: '', role: '', exactDate: '', page: 1 });
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
    $('#pauseLogs').addEventListener('click', () => { state.logsPaused = !state.logsPaused; $('#pauseLogs').textContent = state.logsPaused ? '继续滚动' : '暂停滚动'; }); $('#clearLogs').addEventListener('click', () => { state.logClearedCursor = state.runtimeCursor; renderLiveLogs(); });
    $('#exportSelected').addEventListener('click', () => exportRecords(state.records.filter((record) => state.selectedIds.has(record.id)), '投递报表_所选'));
    $('#deleteSelected').addEventListener('click', () => deleteDeliveryRecords([...state.selectedIds]));
    $('#clearSelection').addEventListener('click', () => { state.selectedIds.clear(); renderRecords(getFilteredRecords()); });
    $('#deleteConfirmCancel').addEventListener('click', () => closeDeleteConfirm(false));
    $('#deleteConfirmSubmit').addEventListener('click', () => closeDeleteConfirm(true));
    $('#deleteConfirm').addEventListener('click', (event) => { if (event.target.id === 'deleteConfirm') closeDeleteConfirm(false); });
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
    $$('#adminTabs button').forEach((button) => button.addEventListener('click', () => { $$('#adminTabs button').forEach((item) => item.classList.toggle('active', item === button)); $$('.admin-view').forEach((view) => view.classList.toggle('active', view.dataset.adminView === button.dataset.adminTab)); }));
    $('#saveConfig').addEventListener('click', saveAdminConfig); $('#resumeSelect').addEventListener('change', (event) => loadResume(event.target.value)); $('#createResume').addEventListener('click', createResume); $('#saveResume').addEventListener('click', saveCurrentResume); $('#promptSelect').addEventListener('change', (event) => showPrompt(event.target.value)); $('#savePrompt').addEventListener('click', saveCurrentPrompt);
    $('#saveLlm').addEventListener('click', saveLlm); $('#llmAddProvider').addEventListener('click', addLlmProvider); $('#llmTestAll').addEventListener('click', () => $$('#llmProviderList .llm-provider-card').forEach((card) => testLlmProvider(card)));
    $('#llmProviderList').addEventListener('click', (event) => { const button = event.target.closest('[data-llm-action]'); if (!button) return; const card = button.closest('.llm-provider-card'); if (button.dataset.llmAction === 'remove') { card.remove(); if (!$$('#llmProviderList .llm-provider-card').length) $('#llmProviderList').innerHTML = '<div class="llm-empty">还没有配置任何接口，点击下方按钮添加。</div>'; } else if (button.dataset.llmAction === 'test') { testLlmProvider(card); } });
    $('#refreshControl').addEventListener('click', loadControlState);
    $('#controlSection').addEventListener('click', (event) => {
        const button = event.target.closest('[data-control-action]'); if (!button) return;
        const action = button.dataset.controlAction, workerId = button.dataset.workerId || ''; let payload = {};
        if (button.dataset.commandValue !== undefined) payload.value = button.dataset.commandValue;
        if (action === 'refresh_instances' || action === 'test_database') { loadControlState(); return; }
        if (action === 'save_account_limit') { const accountId = button.dataset.commandValue || ''; const input = $(`[data-account-limit="${CSS.escape(accountId)}"]`); updateControlResource(`/api/control/accounts/${encodeURIComponent(accountId)}`, { dailyLimit: Number(input?.value || 0) }, '账号配额已保存'); return; }
        if (action === 'refresh_instances') { loadControlState(); return; }
    });
    $('#liveLogAccountFilter').addEventListener('change', (event) => { state.logAccount = event.target.value; renderLiveLogs(); });
    document.addEventListener('click', (event) => { if (!event.target.closest('.column-manager')) $('#columnManagerMenu').hidden = true; });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !$('#deleteConfirm').hidden) closeDeleteConfirm(false); });
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
