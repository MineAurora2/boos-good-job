// ==UserScript==
// @name         goodJobs
// @namespace    http://tampermonkey.net/
// @version      2026-07-14-llm-polling.1
// @description  goodJobs篡改猴插件
// @match        https://www.zhipin.com/*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=zhipin.com
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// ==/UserScript==

(function () {
    'use strict';

    const SCRIPT_VERSION = '2026-07-14-llm-polling.1';
    const SCRIPT_DISABLED_KEY = '__goodjobs_script_disabled';
    const SCRIPT_COMMAND_KEY = '__goodjobs_script_command';
    const SCRIPT_LIFECYCLE_CHANNEL = '__goodjobs_lifecycle';
    const GREET_SESSION_KEY = '__goodjobs_pending_greet_session';
    const MANAGED_CHILD_NAMES = ['__zhipin_detail', '__zhipin_chat', '__zhipin_chat_greet'];

    class ScriptStoppedError extends Error {
        constructor(reason = 'script_stopped') {
            super(`goodJobs stopped: ${reason}`);
            this.name = 'AbortError';
            this.code = 'GOODJOBS_STOPPED';
            this.reason = reason;
        }
    }

    const isStopError = (error) => error?.code === 'GOODJOBS_STOPPED' || error?.name === 'AbortError';

    const runtimeLifecycle = {
        state: 'running',
        reason: '',
        controller: new AbortController(),
        cleanups: new Set(),
        childWindows: new Set(),
        commandHandler: null,
        channel: null,
        lastCommandType: '',
        lastCommandAt: 0,
        stopPromise: null,
        get signal() {
            return this.controller.signal;
        },
        isStopping() {
            return this.state !== 'running' || this.signal.aborted;
        },
        guard() {
            if (this.isStopping() || localStorage.getItem(SCRIPT_DISABLED_KEY) === '1') {
                throw new ScriptStoppedError(this.reason || 'disabled');
            }
        },
        addCleanup(cleanup) {
            if (typeof cleanup !== 'function') return () => void 0;
            if (this.isStopping()) {
                try { cleanup(); } catch (_) { /* ignore cleanup failure */ }
                return () => void 0;
            }
            this.cleanups.add(cleanup);
            return () => this.cleanups.delete(cleanup);
        },
        trackWindow(openedWindow) {
            if (!openedWindow) return openedWindow;
            this.childWindows.add(openedWindow);
            return openedWindow;
        },
        closeChildWindows(delay = 0) {
            const openedWindows = Array.from(this.childWindows);
            this.childWindows.clear();
            const closeAll = () => {
                for (const openedWindow of openedWindows) {
                    try {
                        if (openedWindow && !openedWindow.closed) openedWindow.close();
                    } catch (_) { /* ignore cross-window cleanup failure */ }
                }
            };
            if (delay > 0) window.setTimeout(closeAll, delay);
            else closeAll();
        },
        stop(reason = 'manual_stop') {
            if (this.stopPromise) return this.stopPromise;
            this.state = 'stopping';
            this.reason = reason;
            try { this.controller.abort(new ScriptStoppedError(reason)); } catch (_) { this.controller.abort(); }
            const cleanups = Array.from(this.cleanups).reverse();
            this.cleanups.clear();
            this.stopPromise = (async () => {
                const pendingCleanups = [];
                for (const cleanup of cleanups) {
                    try {
                        const result = cleanup();
                        if (result && typeof result.then === 'function') pendingCleanups.push(result);
                    } catch (error) {
                        console.warn('[goodJobs] 清理资源失败', error);
                    }
                }
                if (pendingCleanups.length) {
                    await Promise.race([
                        Promise.allSettled(pendingCleanups),
                        new Promise((resolve) => window.setTimeout(resolve, 2000)),
                    ]);
                }
                this.state = 'stopped';
                console.info(`[goodJobs] 当前页面执行链已停止: ${reason}`);
            })();
            return this.stopPromise;
        },
        publish(type) {
            const command = { type, id: `${Date.now()}-${Math.random().toString(16).slice(2)}` };
            try { this.channel?.postMessage(command); } catch (_) { /* storage fallback below */ }
            try { localStorage.setItem(SCRIPT_COMMAND_KEY, JSON.stringify(command)); } catch (_) { /* ignore */ }
        },
        installCommandListener(handler) {
            this.commandHandler = handler;
            const onCommand = (command) => {
                if (!command?.type || typeof this.commandHandler !== 'function') return;
                const now = Date.now();
                if (command.type === this.lastCommandType && now - this.lastCommandAt < 500) return;
                this.lastCommandType = command.type;
                this.lastCommandAt = now;
                Promise.resolve(this.commandHandler(command)).catch((error) => {
                    console.warn('[goodJobs] 跨页面停止命令处理失败', error);
                });
            };
            const onStorage = (event) => {
                if (event.key === SCRIPT_DISABLED_KEY && event.newValue === '1') {
                    onCommand({ type: 'exit', id: 'disabled-storage' });
                    return;
                }
                if (event.key !== SCRIPT_COMMAND_KEY || !event.newValue) return;
                try { onCommand(JSON.parse(event.newValue)); } catch (_) { /* ignore malformed command */ }
            };
            window.addEventListener('storage', onStorage);
            if (typeof BroadcastChannel !== 'undefined') {
                this.channel = new BroadcastChannel(SCRIPT_LIFECYCLE_CHANNEL);
                this.channel.addEventListener('message', (event) => onCommand(event.data));
            }
            this.addCleanup(() => {
                window.removeEventListener('storage', onStorage);
                try { this.channel?.close(); } catch (_) { /* ignore */ }
                this.channel = null;
            });
        },
    };

    const scriptLifecycle = {
        isDisabled() {
            return localStorage.getItem(SCRIPT_DISABLED_KEY) === '1';
        },
        async exit() {
            if (!window.confirm('确定要退出并关闭 goodJobs 脚本吗？\n关闭后将停止搜索、投递、聊天和心跳。')) return false;
            const shouldCloseCurrentWindow = MANAGED_CHILD_NAMES.includes(window.name);
            localStorage.setItem(SCRIPT_DISABLED_KEY, '1');
            runtimeLifecycle.publish('exit');
            await runtimeLifecycle.stop('user_exit');
            if (runtimeLifecycle.childWindows.size) {
                await new Promise((resolve) => window.setTimeout(resolve, 2100));
                runtimeLifecycle.closeChildWindows();
            }
            if (shouldCloseCurrentWindow) {
                try { window.close(); } catch (_) { /* ignore */ }
            } else {
                try { window.name = ''; } catch (_) { /* ignore */ }
                window.location.reload();
            }
            return true;
        },
        enable() {
            localStorage.removeItem(SCRIPT_DISABLED_KEY);
            window.location.reload();
        },
        async restart() {
            localStorage.removeItem(SCRIPT_DISABLED_KEY);
            runtimeLifecycle.publish('restart');
            await runtimeLifecycle.stop('user_restart');
            if (runtimeLifecycle.childWindows.size) {
                await new Promise((resolve) => window.setTimeout(resolve, 2100));
                runtimeLifecycle.closeChildWindows();
            }
            localStorage.setItem('__zhipin_search', String(Date.now()));
            try { window.name = '__zhipin_search'; } catch (_) { /* ignore */ }
            const searchUrl = `${window.location.origin}/web/geek/job`;
            if (window.location.pathname.startsWith('/web/geek/job')) window.location.reload();
            else window.location.replace(searchUrl);
        },
    };

    if (typeof GM_registerMenuCommand === 'function') {
        GM_registerMenuCommand(
            scriptLifecycle.isDisabled() ? '启用 goodJobs 脚本' : '退出并关闭 goodJobs 脚本',
            () => scriptLifecycle.isDisabled() ? scriptLifecycle.enable() : scriptLifecycle.exit()
        );
        GM_registerMenuCommand('重启 goodJobs 脚本', () => scriptLifecycle.restart());
    }

    if (scriptLifecycle.isDisabled()) {
        console.info('[goodJobs] 脚本已关闭，可通过篡改猴菜单“启用 goodJobs 脚本”重新开启。');
        return;
    }

    runtimeLifecycle.installCommandListener(async (command) => {
        const reason = command.type === 'restart' ? 'remote_restart' : 'remote_exit';
        await runtimeLifecycle.stop(reason);
        if (runtimeLifecycle.childWindows.size) {
            await new Promise((resolve) => window.setTimeout(resolve, 2100));
            runtimeLifecycle.closeChildWindows();
        }
        if (MANAGED_CHILD_NAMES.includes(window.name)) {
            try { window.close(); } catch (_) { /* ignore */ }
            return;
        }
        if (command.type === 'restart') {
            const searchUrl = `${window.location.origin}/web/geek/job`;
            if (window.location.pathname.startsWith('/web/geek/job')) window.location.reload();
            else window.location.replace(searchUrl);
        } else {
            window.location.reload();
        }
    });

    // 配置项
    const OPTIONS = {
        resumeIndex: 0, // 第几份简历，从 0 开始递增
        serverHost: 'http://127.0.0.1:47999', // 本地服务的主机地址
        thread: 50, // 分数阈值，低于这个就不发消息了
        timestampTimeout: 3000, // 时间戳过期时间，单位毫秒，根据当前网络设定，建议不要太大。
        onlyGreet: true, // 是否只打招呼，默认为false，即打招呼和代聊天
        manualFilterWaitMs: 10000, // 每轮搜索后留给用户手动筛选的时间
        roundRestartDelayMs: 2000, // 本轮结束后，启动下一轮前的缓冲时间
        maxEmptyRounds: 3, // 连续多少轮没有拿到新岗位后停止，避免空转
        detailTimeout: 10000, // 获取职位详情超时时间
        greetTimeout: 12000, // 打招呼页回执超时时间
        preloadScrollPixels: 180, // 岗位预加载：每轮下滑像素
        preloadScrollWaitMs: 450, // 岗位预加载：每轮等待毫秒数
        preloadStableRoundsLimit: 24, // 岗位预加载：连续多少轮无增长后结束
        preloadMaxRounds: 30, // 岗位预加载：最多滑动多少轮
        preloadActivateCardEvery: 0, // 预加载时每隔多少轮尝试轻点一次左侧岗位卡片，0 表示关闭
        preloadActivateCardWaitMs: 250, // 轻点岗位卡片后的额外等待时间
    };

    // 元素选择器
    const SELECTORS = {
        ZHIPIN: {
            SEARCH: {
                SEARCHINPUT: 'input', // 搜索框
                SEARCHBTN: '.search-btn', // 搜索按钮
                JOBLISTCTN: '.job-list-container', // 职位列表容器
                JOBLIST: '.rec-job-list', // 职位列表
                JOBCARD: '.job-card-box', // 左侧岗位卡片
                JOBHREFS: '.job-card-box .job-name', // 职位链接
            },
            DETAIL: {
                STARTCHAT: '.btn-startchat', // 开始聊天按钮
                NAMEBOX: '.name', // 职位名称盒子
                JOBNAME: 'h1', // 职位名称
                SALARY: '.salary', // 职位薪资
                DETAIL: '.job-sec-text', // 职位详情
                CHATURL: 'redirect-url', // 聊天链接
                COMPANY: '.company-name', // 公司名称
                LOCATION: '.job-location .location-address, .location-address, .job-address-desc', // 工作地点
                QUALIFICATION_TAGS: '.job-primary .job-tags span, .job-banner .job-tags span, .job-info .job-tags span, .tag-list li', // 经验学历标签
                INDUSTRY: '.company-info a[href*="industry"], .sider-company a[href*="industry"], a[ka*="industry"]', // 公司行业
            },
            CHAT: {
                // 聊天
                CHATINPUT: '#chat-input', // 聊天输入框
                MSGSEND: '.btn-send', // 消息发送按钮
                // 聊天记录
                HISTORYCTN: '.chat-message', // 聊天记录容器
                USEFULMSG: '.item-friend,.item-myself', // 有效的文字聊天记录项
                MSGCONTENT: '.message-content .text', // 聊天记录内容
                // 职位
                JOBEL: '*[ka=geek_chat_job_detail]', // 职位元素
                JOBCITY: '.city', // 职位城市
                // 简历
                RESUMESEND: '.toolbar-btn.tooltip.tooltip-top', // 简历发送按钮
                RESUMEMODAL: '.panel-resume', // 简历发送弹窗，有的时候简历按钮点击会出来一个小弹窗
                RESUMEMODALCONFIRM: '.btn-sure-v2', // 简历发送弹窗确认按钮
                RESUMELIST: '.resume-list', // 简历列表
                RESUMELISTITEM: 'li', // 简历列表项
                RESUMESENDCONFIRM: '.btn-confirm', // 简历发送确认按钮
                // 联系人
                CONTACTLISTEMPTY: '.no-data', // 联系人列表为空
                CONTACTLIST: '.user-list-content', // 联系人列表
                CONTACTLISTITEM: 'li', // 联系人列表项
                NEWMSGNOTICE: '.notice-badge', // 新消息通知图标
                USERNAME: '.name-text', // 联系人名称
            }
        },
    };

    // 搜索路径
    const SEARCHPATH = {
        zhipin: '/web/geek/job',
    };

    // 白名单
    const WHITELIST = {
        zhipin: {
            deatil: '/job_detail',
            chat: '/web/geek/chat'
        },
    };

    // 工具
    const tools = {
        endlessFind: function (selector, timeout = 10000, signal = runtimeLifecycle.signal) {
            return new Promise((resolve, reject) => {
                if (signal?.aborted) {
                    reject(new ScriptStoppedError(runtimeLifecycle.reason || 'find_aborted'));
                    return;
                }
                // 初始立即检查元素是否存在
                let element;
                try {
                    element = document.querySelector(selector);
                } catch (e) {
                    reject(e); // 处理无效选择器
                    return;
                }
                if (element) {
                    resolve(element);
                    return;
                }

                let observer = null;
                let timeoutId = null;
                let settled = false;
                const cleanup = () => {
                    observer?.disconnect();
                    if (timeoutId) clearTimeout(timeoutId);
                    signal?.removeEventListener('abort', onAbort);
                };
                const finish = (callback, value) => {
                    if (settled) return;
                    settled = true;
                    cleanup();
                    callback(value);
                };
                const onAbort = () => {
                    finish(reject, new ScriptStoppedError(runtimeLifecycle.reason || 'find_aborted'));
                };

                // 定义MutationObserver回调
                observer = new MutationObserver(() => {
                    try {
                        const el = document.querySelector(selector);
                        if (el) finish(resolve, el);
                    } catch (e) {
                        finish(reject, e);
                    }
                });

                timeoutId = setTimeout(() => finish(reject, new Error('未找到目标元素')), timeout);
                signal?.addEventListener('abort', onAbort, { once: true });
                if (signal?.aborted) {
                    onAbort();
                    return;
                }

                // 开始观察整个文档的DOM变化
                try {
                    observer.observe(document.documentElement, {
                        childList: true,
                        subtree: true
                    });
                } catch (error) {
                    finish(reject, error);
                }
            });
        },
        inputText: function (el, text) {
            el.value = text;
            el.dispatchEvent(new Event('input', { bubbles: true }));
        },
        extractCity(value) {
            const text = String(value || '').replace(/\s+/g, '').trim();
            if (!text) return '';
            const cities = ['呼和浩特', '乌鲁木齐', '石家庄', '哈尔滨', '北京', '上海', '天津', '重庆', '深圳', '广州', '杭州', '南京', '苏州', '成都', '武汉', '西安', '长沙', '郑州', '青岛', '厦门', '福州', '济南', '合肥', '宁波', '东莞', '佛山', '无锡', '珠海', '惠州', '中山', '南昌', '昆明', '贵阳', '南宁', '海口', '三亚', '沈阳', '大连', '长春', '太原', '兰州', '西宁', '银川', '拉萨', '香港', '澳门'];
            const prefix = cities.find((city) => text.startsWith(city));
            if (prefix) return prefix;
            const cityMatch = text.match(/^([\u4e00-\u9fff]{2,8}?)市/);
            return cityMatch ? cityMatch[1] : '';
        },
        asyncSleep(ms, signal = runtimeLifecycle.signal) {
            return new Promise((resolve, reject) => {
                if (signal?.aborted) {
                    reject(new ScriptStoppedError(runtimeLifecycle.reason || 'sleep_aborted'));
                    return;
                }
                // 创建一个 Blob 对象，包含 Web Worker 的代码
                const workerCode = `self.addEventListener('message', function(e) {
                    const delay = e.data;
                    setTimeout(function() {
                        self.postMessage('done');
                    }, delay);
                });`;

                let workerUrl = '';
                let worker = null;
                let settled = false;
                const cleanup = () => {
                    signal?.removeEventListener('abort', onAbort);
                    try { worker?.terminate(); } catch (_) { /* ignore */ }
                    if (workerUrl) URL.revokeObjectURL(workerUrl);
                    worker = null;
                    workerUrl = '';
                };
                const finish = (callback, value) => {
                    if (settled) return;
                    settled = true;
                    cleanup();
                    callback(value);
                };
                const onAbort = () => {
                    finish(reject, new ScriptStoppedError(runtimeLifecycle.reason || 'sleep_aborted'));
                };
                signal?.addEventListener('abort', onAbort, { once: true });
                if (signal?.aborted) {
                    onAbort();
                    return;
                }
                try {
                    const blob = new Blob([workerCode], { type: 'application/javascript' });
                    workerUrl = URL.createObjectURL(blob);
                    worker = new Worker(workerUrl);
                    worker.onmessage = () => finish(resolve);
                    worker.onerror = (event) => finish(reject, event.error || new Error('等待 Worker 执行失败'));
                    worker.onmessageerror = () => finish(reject, new Error('等待 Worker 消息解析失败'));
                    worker.postMessage(ms);
                } catch (error) {
                    finish(reject, error);
                }
            });
        },
        getTimestamp(key) {
            return Number(localStorage.getItem(key));
        },
        openTabNSetTimestamp(href, key, self = false) {
            runtimeLifecycle.guard();
            localStorage.setItem(key, new Date().getTime());
            const openedWindow = window.open(href, self ? '_self' : key);
            if (!self) runtimeLifecycle.trackWindow(openedWindow);
            return openedWindow;
        },
    };

    async function safeLogAction(api, payload) {
        try {
            await api.logAction(payload);
        } catch (error) {
            if (isStopError(error)) throw error;
            console.log('logAction failed', error);
        }
    }

    const deliveryFlow = {
        isDuplicate(claim) {
            return ['duplicate_job', 'duplicate_company'].includes(claim?.reason);
        },
        duplicateMessage(company, title) {
            return `重复投递（未计数）：公司 [${company}] + 岗位 [${title}] 已领取或投递，已忽略`;
        },
        async precheck(api, company, title) {
            runtimeLifecycle.guard();
            try {
                const result = await api.checkDelivery(company, title);
                runtimeLifecycle.guard();
                return result;
            } catch (error) {
                if (isStopError(error)) throw error;
                return { unavailable: true, error };
            }
        },
        async claim(api, identity, job, jobUrl) {
            runtimeLifecycle.guard();
            try {
                const result = await api.claimDelivery(
                    identity,
                    job.company,
                    job.title,
                    jobUrl,
                    job.salary,
                    job.location
                );
                if (runtimeLifecycle.isStopping()) {
                    if (result?.accepted && result.claimToken) {
                        await api.releaseDelivery(result.claimToken, 'script_stopped_after_claim', { allowDuringStop: true }).catch(() => null);
                    }
                    throw new ScriptStoppedError('stopped_after_claim');
                }
                return result;
            } catch (error) {
                if (isStopError(error)) throw error;
                return { accepted: false, reason: 'service_unavailable', error };
            }
        },
    };

    async function runPeerHeartbeat(broadcast, searchTarget, heartbeatType, payloadFactory = () => ({})) {
        let count = 0;
        while (!runtimeLifecycle.isStopping()) {
            const response = await broadcast.sendAndReceive(
                searchTarget,
                heartbeatType,
                { ...payloadFactory(), count: ++count },
                5000
            );
            if (!response?.success || response?.stopped || response?.cancelled) {
                throw new ScriptStoppedError(response?.cancelled ? 'peer_session_cancelled' : 'peer_stopped');
            }
            await tools.asyncSleep(1000);
        }
    }

    function logDecisionDeductions(decision, writeLog) {
        const deductions = Array.isArray(decision?.deductions) ? decision.deductions : [];
        for (const item of deductions) {
            const location = item.fieldLabel || (item.field === 'title' ? '职位名称' : '职位描述');
            const message = `扣星命中 [${location}] 关键词 [${item.keyword}]：-${item.deductStars} 星`;
            console.log(`[goodJobs] ${message}`);
            writeLog(message);
        }
    }

    // 每个浏览器配置文件拥有独立 workerId；accountId 可通过油猴菜单设置。
    const deliveryIdentity = {
        accountKey: '__goodjobs_account_id',
        workerKey: '__goodjobs_worker_id',
        createId(prefix) {
            const randomId = (typeof crypto !== 'undefined' && crypto.randomUUID)
                ? crypto.randomUUID()
                : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
            return `${prefix}-${randomId}`;
        },
        get() {
            let workerId = localStorage.getItem(this.workerKey);
            if (!workerId) {
                workerId = this.createId('browser');
                localStorage.setItem(this.workerKey, workerId);
            }
            let accountId = localStorage.getItem(this.accountKey);
            if (!accountId) {
                accountId = `account-${workerId.slice(-12)}`;
                localStorage.setItem(this.accountKey, accountId);
            }
            return { accountId, workerId };
        },
        configure() {
            const current = this.get();
            const accountId = window.prompt(
                '请输入当前 Boss 账号标识。同一账号在多个浏览器运行时必须填写相同标识。',
                current.accountId
            );
            if (accountId && accountId.trim()) {
                localStorage.setItem(this.accountKey, accountId.trim());
                banner(`账号标识已保存：${accountId.trim()}，刷新页面后生效`);
            }
        },
    };

    if (typeof GM_registerMenuCommand === 'function') {
        GM_registerMenuCommand('设置 goodJobs 账号标识', () => deliveryIdentity.configure());
    }

    /**
     * 横幅
     * @param {string} text 显示的文本
     */
    function banner(text) {
        const el = document.createElement('div');
        el.style.cssText = `
                position: fixed;
                top: 60px;
                left: 50%;
                transform: translateX(-50%);
                z-index: 9999;
                background-color: rgba(0,0,0,.5);
                padding: 4px 20px;
                text-align: center;
                border-radius: 8px;
                color: #fff;
        `;
        el.innerText = text;
        document.body.appendChild(el);
        setTimeout(function () {
            el.remove();
        }, 3000);
    }

    /**
     * 转换时间
     * @param {number} seconds 秒数
     * @returns {string} 转换后的时间字符串
     */
    function convertTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = seconds % 60;

        return `${hours.toString().padStart(2, 0)
            } : ${minutes.toString().padStart(2, 0)
            } : ${secs.toFixed(0).padStart(2, 0)
            }`;
    }


    class WebBroadcastError extends Error {
        constructor(code, message) {
            super(message);
            this.code = code;
            this.name = 'WebBroadcastError';
        }
    }

    class WebBroadcast {
        static ID_COUNTER = 0; // 自增序列，避免时间戳冲突

        /**
         * @param {string} name 频道名称
         * @param {string} target 当前页面标识
         * @param {object} [options] 配置项
         * @param {number} [options.retry=3] 发送失败重试次数
         * @param {number} [options.retryInterval=1000] 重试间隔(毫秒)
         */
        constructor(name, target, options = {}) {
            this.name = name;
            this.target = target;
            this.retry = options.retry ?? 3;
            this.retryInterval = options.retryInterval ?? 1000;
            this.evts = {};
            this.pendingResponses = {};
            this.pendingReceives = {};
            this.destroyed = false;
            this.boundMessageHandler = this.handleMessage.bind(this);
            this.boundMessageErrorHandler = (event) => this.emitError('MESSAGE_ERROR', '消息解析失败', event);
            this.boundStorageHandler = null;
            this.boundUnloadHandler = () => this.destroy('page_unload');

            // 初始化通信通道
            this.initChannel();
            this.removeLifecycleCleanup = runtimeLifecycle.addCleanup(() => this.destroy('script_stopped'));
        }

        /* -------------------- 核心通信逻辑 -------------------- */
        initChannel() {
            // 优先使用 BroadcastChannel
            if (typeof BroadcastChannel !== 'undefined') {
                this.setupBroadcastChannel();
            } else {
                this.setupStorageFallback();
            }
            window.addEventListener('beforeunload', this.boundUnloadHandler);
        }

        setupBroadcastChannel() {
            this.channelType = 'broadcast';
            this.channel = new BroadcastChannel(this.name);
            this.channel.addEventListener('message', this.boundMessageHandler);
            this.channel.addEventListener('messageerror', this.boundMessageErrorHandler);
        }

        setupStorageFallback() {
            this.channelType = 'storage';
            this.storageKey = `web_broadcast_${this.name}`;

            // 监听 storage 事件
            this.boundStorageHandler = (e) => {
                if (e.key === this.storageKey && e.newValue) {
                    try {
                        const message = JSON.parse(e.newValue);
                        this.handleMessage({ data: message });
                    } catch (error) {
                        this.emitError('MESSAGE_ERROR', 'storage 消息解析失败', error);
                    }
                }
            };
            window.addEventListener('storage', this.boundStorageHandler);
        }

        handleMessage(e) {
            if (this.destroyed) return;
            const resp = e.data;
            if (![this.target, 'all'].includes(resp.to)) return;

            // 处理事件监听
            if (this.evts[resp.type]) {
                Promise.resolve()
                    .then(() => this.evts[resp.type](resp.from, resp.data))
                    .catch((error) => {
                        if (!isStopError(error)) this.emitError('HANDLER_ERROR', `消息处理失败: ${resp.type}`, error);
                    });
            }

            // 处理 receive 等待
            const receiveKey = `${resp.from}-${resp.type}`;
            if (this.pendingReceives[receiveKey]) {
                const pending = this.pendingReceives[receiveKey];
                pending.resolve(resp.data);
                clearTimeout(pending.timer);
                delete this.pendingReceives[receiveKey];
            }

            // 处理 sendAndReceive 响应
            if (this.pendingResponses[resp.data?.requestId]) {
                const pending = this.pendingResponses[resp.data.requestId];
                pending.resolve(resp.data);
                clearTimeout(pending.timer);
                delete this.pendingResponses[resp.data.requestId];
            }
        }

        /* -------------------- 消息收发方法 -------------------- */
        async send(to, type, data = null, attempt = 0) {
            if (this.destroyed || runtimeLifecycle.isStopping()) {
                throw new ScriptStoppedError('broadcast_destroyed');
            }
            const message = { from: this.target, to, type, data };

            try {
                if (this.channelType === 'broadcast') {
                    this.channel.postMessage(message);
                } else {
                    // storage 方案需要先写入再删除，触发事件
                    localStorage.setItem(this.storageKey, JSON.stringify(message));
                    localStorage.removeItem(this.storageKey);
                }
            } catch (error) {
                if (attempt < this.retry) {
                    await tools.asyncSleep(this.retryInterval);
                    return this.send(to, type, data, attempt + 1);
                }
                this.emitError('SEND_FAILED', `消息发送失败: ${type}`, error);
                throw new WebBroadcastError('SEND_FAILED', `消息发送失败: ${type}, ${error.message}`);
            }
        }

        receive(from, type, timeout = 30000) {
            const key = `${from}-${type}`;
            return new Promise((resolve, reject) => {
                if (this.destroyed || runtimeLifecycle.isStopping()) {
                    reject(new ScriptStoppedError('broadcast_receive_stopped'));
                    return;
                }
                const existing = this.pendingReceives[key];
                if (existing) {
                    clearTimeout(existing.timer);
                    existing.reject(new WebBroadcastError('SUPERSEDED', `等待已被新请求替换: ${type}`));
                }
                const timer = setTimeout(() => {
                    reject(new WebBroadcastError('TIMEOUT', `接收超时: ${type}`));
                    delete this.pendingReceives[key];
                }, timeout);

                this.pendingReceives[key] = { resolve, reject, timer };
            });
        }

        sendAndReceive(to, type, data = null, timeout = 30000) {
            const requestId = this.generateRequestId();
            const responseType = `${type}_response`;

            return new Promise((resolve, reject) => {
                if (this.destroyed || runtimeLifecycle.isStopping()) {
                    reject(new ScriptStoppedError('broadcast_request_stopped'));
                    return;
                }
                const timer = setTimeout(() => {
                    reject(new WebBroadcastError('TIMEOUT', `请求超时: ${type}`));
                    delete this.pendingResponses[requestId];
                }, timeout);


                this.pendingResponses[requestId] = { resolve, reject, timer };
                // 发送时携带 responseType
                this.send(to, type, { ...data, requestId, responseType }).catch((error) => {
                    clearTimeout(timer);
                    delete this.pendingResponses[requestId];
                    reject(error);
                });
            });
        }

        reply(originalFrom, originalType, data, requestId, responseType) {
            const finalResponseType = responseType || `${originalType}_response`;
            return this.send(originalFrom, finalResponseType, { ...data, requestId });
        }

        /* -------------------- 工具方法 -------------------- */
        generateRequestId() {
            const time = Date.now().toString(36);
            const random = Math.random().toString(36).slice(2, 6);
            WebBroadcast.ID_COUNTER = (WebBroadcast.ID_COUNTER + 1) % 0xfff;
            return `${time}-${random}-${WebBroadcast.ID_COUNTER.toString(36).padStart(2, '0')}`;
        }

        emitError(code, message, error) {
            const err = new WebBroadcastError(code, `${message}: ${error?.message || error}`);
            console.error(err);
            if (this.evts['error']) {
                this.evts['error'](code, err.message);
            }
        }

        on(evt, fn) {
            if (typeof fn !== 'function') throw new Error('回调必须是函数');
            this.evts[evt] = fn;
        }

        off(evt) {
            delete this.evts[evt];
        }

        destroy(reason = 'broadcast_destroyed') {
            if (this.destroyed) return;
            this.destroyed = true;
            if (this.channel) {
                this.channel.removeEventListener('message', this.boundMessageHandler);
                this.channel.removeEventListener('messageerror', this.boundMessageErrorHandler);
                this.channel.close();
            }
            if (this.boundStorageHandler) window.removeEventListener('storage', this.boundStorageHandler);
            window.removeEventListener('beforeunload', this.boundUnloadHandler);
            const error = new ScriptStoppedError(reason);
            Object.values(this.pendingResponses).forEach((pending) => {
                clearTimeout(pending.timer);
                pending.reject(error);
            });
            Object.values(this.pendingReceives).forEach((pending) => {
                clearTimeout(pending.timer);
                pending.reject(error);
            });
            this.pendingResponses = {};
            this.pendingReceives = {};
            this.evts = {};
            this.removeLifecycleCleanup?.();
            this.removeLifecycleCleanup = null;
        }
    }

    // api请求
    class Api {
        constructor() { }

        /**
         * 封装请求
         * @param {string} path 请求路径
         * @param {string} method 请求方法
         * @param {any} data 请求数据
         * @returns {Promise<any>} 请求结果
         */
        __http(path, method = 'GET', data = null, requestOptions = {}) {
            const signal = requestOptions.allowDuringStop ? null : runtimeLifecycle.signal;
            return new Promise((resolve, reject) => {
                if (signal?.aborted || (!requestOptions.allowDuringStop && runtimeLifecycle.isStopping())) {
                    reject(new ScriptStoppedError('request_aborted'));
                    return;
                }
                let settled = false;
                let requestHandle = null;
                const cleanup = () => signal?.removeEventListener('abort', onAbort);
                const finish = (callback, value) => {
                    if (settled) return;
                    settled = true;
                    cleanup();
                    callback(value);
                };
                const onAbort = () => {
                    try { requestHandle?.abort?.(); } catch (_) { /* ignore */ }
                    finish(reject, new ScriptStoppedError('request_aborted'));
                };
                const onLoad = (resp) => {
                    if (Number(resp?.status) !== 200) {
                        if (!runtimeLifecycle.isStopping()) banner(`请求失败: ${resp?.status || '未知'}`);
                        finish(reject, new Error(`HTTP ${resp?.status || 0}`));
                        return;
                    }
                    try {
                        const raw = resp.response ?? resp.responseText;
                        finish(resolve, raw && typeof raw === 'object' ? raw : JSON.parse(raw || '{}'));
                    } catch (error) {
                        finish(reject, error);
                    }
                };
                const onError = (error) => {
                    if (!runtimeLifecycle.isStopping()) banner('请求出错');
                    finish(reject, error instanceof Error ? error : new Error(`请求出错: ${JSON.stringify(error)}`));
                };

                try {
                    const request = typeof GM !== 'undefined' && typeof GM.xmlHttpRequest === 'function'
                        ? GM.xmlHttpRequest.bind(GM)
                        : (typeof GM_xmlhttpRequest === 'function' ? GM_xmlhttpRequest : null);
                    if (!request) throw new Error('GM_xmlhttpRequest 不可用');
                    signal?.addEventListener('abort', onAbort, { once: true });
                    requestHandle = request({
                        method,
                        url: OPTIONS.serverHost + path,
                        headers: { 'Content-Type': 'application/json' },
                        data,
                        timeout: requestOptions.timeout ?? (requestOptions.allowDuringStop ? 2500 : 1000 * 60 * 10),
                        onload: onLoad,
                        onerror: onError,
                        ontimeout: () => onError(new Error('请求超时')),
                        onabort: () => finish(reject, new ScriptStoppedError('request_aborted')),
                    });
                    if (requestHandle && typeof requestHandle.then === 'function') {
                        requestHandle.then(onLoad).catch(onError);
                    }
                } catch (error) {
                    onError(error);
                }
            });
        }

        /**
         * 获取自我介绍
         */
        getIntroduce() {
            return this.__http('/get-introduce').then((res) => res.introduce);
        }

        /**
         * 获取标签
         */
        getTags() {
            return this.__http('/tags').then((res) => res.tags);
        }

        /**
         * 获取前端运行配置
         */
        getClientConfig() {
            return this.__http('/client-config');
        }

        /**
         * 获取职位匹配度
         * @param {string} title 职位标题
         * @param {string} salary 薪资范围
         * @param {string} detail 职位描述
         */
        getJobScore(title, salary, detail) {
            const data = `# 职位名称\n${title}\n\n# 薪资范围\n${salary}\n\n# 职位描述\n${detail}`;
            return this.__http('/get-job-score', 'POST', JSON.stringify(data));
        }

        /**
         * 所有投递条件通过后，生成最终发送给招聘者的招呼语
         */
        async generateIntroduce(claimToken, company, title, salary, detail) {
            const requestBody = JSON.stringify({
                claimToken,
                company,
                title,
                salary,
                detail,
            });
            const deadline = Date.now() + 10 * 60 * 1000;
            let job = null;
            let startFailures = 0;

            // Starting is idempotent by claimToken, so a transient userscript
            // background restart cannot create duplicate LLM requests.
            while (!job && startFailures < 3) {
                try {
                    job = await this.__http('/generate-introduce/start', 'POST', requestBody, { timeout: 10000 });
                } catch (error) {
                    if (isStopError(error)) throw error;
                    startFailures += 1;
                    if (startFailures >= 3) break;
                    await tools.asyncSleep(1000);
                }
            }

            let pollFailures = 0;
            while (job && job.status === 'pending' && Date.now() < deadline) {
                await tools.asyncSleep(1000);
                try {
                    job = await this.__http(
                        `/generate-introduce/status/${encodeURIComponent(job.jobId)}`,
                        'GET',
                        null,
                        { timeout: 10000 }
                    );
                    pollFailures = 0;
                } catch (error) {
                    if (isStopError(error)) throw error;
                    pollFailures += 1;
                    if (pollFailures >= 3) throw error;
                    // Tampermonkey/Violentmonkey may briefly restart its
                    // background worker. The server-side task keeps running.
                }
            }

            if (!job) throw new Error('无法启动招呼语生成任务');
            if (job.status === 'failed') throw new Error(job.error || '招呼语生成失败');
            if (job.status !== 'completed') throw new Error('招呼语生成等待超时');
            return job;
        }

        /**
         * 记录动作日志
         * @param {object} payload 动作信息
         */
        logAction(payload) {
            return this.__http('/log-action', 'POST', JSON.stringify(payload), { timeout: 5000 });
        }

        claimDelivery(identity, company, title, jobUrl = '', salary = '', location = '') {
            return this.__http('/delivery/claim', 'POST', JSON.stringify({
                accountId: identity.accountId,
                workerId: identity.workerId,
                company,
                title,
                jobUrl,
                salary,
                location,
            }));
        }

        markDelivery(claimToken, status, error = '', requestOptions = {}) {
            return this.__http('/delivery/mark', 'POST', JSON.stringify({ claimToken, status, error }), requestOptions);
        }

        releaseDelivery(claimToken, reason = '', requestOptions = {}) {
            return this.__http('/delivery/release', 'POST', JSON.stringify({ claimToken, reason }), requestOptions);
        }

        checkDailyLimit(accountId) {
            return this.__http('/check-daily-limit', 'POST', JSON.stringify({ accountId }));
        }

        checkDelivery(company, title) {
            return this.__http('/check-greet', 'POST', JSON.stringify({ company, title }));
        }

        heartbeat(payload) {
            return new Promise((resolve) => {
                let settled = false;
                const finish = (result) => {
                    if (settled) return;
                    settled = true;
                    resolve(result);
                };
                const handleResponse = (resp) => {
                    if (Number(resp?.status) !== 200) {
                        finish(null);
                        return;
                    }
                    try {
                        const raw = resp.response ?? resp.responseText;
                        finish(raw && typeof raw === 'object' ? raw : JSON.parse(raw || '{}'));
                    } catch (_) {
                        finish(null);
                    }
                };

                try {
                    const request = typeof GM !== 'undefined' && typeof GM.xmlHttpRequest === 'function'
                        ? GM.xmlHttpRequest.bind(GM)
                        : (typeof GM_xmlhttpRequest === 'function' ? GM_xmlhttpRequest : null);
                    if (!request) {
                        finish(null);
                        return;
                    }
                    const pending = request({
                        method: 'POST',
                        url: OPTIONS.serverHost + '/api/runtime/heartbeat',
                        headers: { 'Content-Type': 'application/json' },
                        data: JSON.stringify(payload),
                        timeout: 10000,
                        onload: handleResponse,
                        onerror: () => finish(null),
                        ontimeout: () => finish(null),
                        onabort: () => finish(null),
                    });
                    if (pending && typeof pending.then === 'function') {
                        pending.then(handleResponse).catch(() => finish(null));
                    }
                } catch (_) {
                    finish(null);
                }
            });
        }
    }

    // 日志记录
    class Logger {
        constructor(startFn, pauseFn, onLog, exitFn) {
            // 校验函数
            if (startFn && !Function.prototype.isPrototypeOf(startFn)) {
                throw new Error('参数错误，startFn应为函数');
            }
            if (pauseFn && !Function.prototype.isPrototypeOf(pauseFn)) {
                throw new Error('参数错误，pauseFn应为函数');
            }
            // 创建元素
            const ctn = document.createElement('div');
            const btnBox = document.createElement('div');
            const clearBtn = document.createElement('div');
            const runBtn = document.createElement('div');
            const foldBtn = document.createElement('div');
            const exitBtn = document.createElement('div');
            const restartBtn = document.createElement('div');
            const msgList = document.createElement('div');
            ctn.style.cssText = `
                position: fixed;
                bottom: 16px;
                left: 16px;
                width: 380px;
                background-color: rgba(0, 0, 0, 0.5);
                color: #fff;
                z-index: 9999;
                font-size: 14px;
                border-radius: 10px;
            `;
            btnBox.style.cssText = `
                width: 380px;
                height: 32px;
                display: flex;
                align-items: center;
                justify-content: flex-end;
            `;
            clearBtn.style.cssText = runBtn.style.cssText = foldBtn.style.cssText = exitBtn.style.cssText = restartBtn.style.cssText = `
                width: 60px;
                height: 32px;
                line-height: 32px;
                text-align: center;
                cursor: pointer;
            `;
            msgList.style.cssText = `
                width: 380px;
                height: 240px;
                padding: 2px 12px 8px;
                overflow-y: auto;
                display: flex;
                flex-direction: column;
                gap: 4px;
            `;
            clearBtn.innerText = "清空";
            runBtn.innerText = "开始";
            foldBtn.innerText = "收起";
            exitBtn.innerText = "退出";
            exitBtn.style.color = '#ff9aa5';
            exitBtn.style.background = 'rgba(255, 80, 96, 0.12)';
            restartBtn.innerText = "重启";
            restartBtn.style.color = '#8eeeff';
            restartBtn.style.background = 'rgba(57, 215, 242, 0.1)';
            document.body.appendChild(ctn);
            ctn.appendChild(btnBox);
            btnBox.appendChild(clearBtn);
            btnBox.appendChild(runBtn);
            btnBox.appendChild(foldBtn);
            btnBox.appendChild(restartBtn);
            btnBox.appendChild(exitBtn);
            ctn.appendChild(msgList);
            this.ctn = ctn;
            this.list = msgList;
            this.runBtn = runBtn;
            this.clearBtn = clearBtn;
            this.exitBtn = exitBtn;
            this.restartBtn = restartBtn;
            this.__startFn = startFn || (() => void 0);
            this.__pauseFn = pauseFn || (() => void 0);
            this.__onLog = Function.prototype.isPrototypeOf(onLog) ? onLog : (() => void 0);
            this.__exitFn = Function.prototype.isPrototypeOf(exitFn) ? exitFn : (() => scriptLifecycle.exit());
            this.__pause = true;
            clearBtn.addEventListener('click', () => this.clear());
            runBtn.addEventListener('click', () => {
                this.__pause = !this.__pause;
                if (this.__pause) {
                    runBtn.innerText = "继续";
                    this.__pauseFn();
                } else {
                    runBtn.innerText = "暂停";
                    this.__startFn();
                }
            });
            foldBtn.addEventListener('click', () => {
                if (foldBtn.innerText === "展开") {
                    msgList.style.height = "240px";
                    foldBtn.innerText = "收起";
                } else {
                    msgList.style.height = "32px";
                    this.list.scrollTop = this.list.scrollHeight;
                    foldBtn.innerText = "展开";
                }
            });
            exitBtn.addEventListener('click', () => this.__exitFn());
            restartBtn.addEventListener('click', () => scriptLifecycle.restart());
        }

        add(message) {
            const item = document.createElement('div');
            item.textContent = message;
            this.list.appendChild(item);
            this.list.scrollTop = this.list.scrollHeight;
            this.__onLog(String(message));
        }

        divider() {
            const item = document.createElement('div');
            item.style.cssText = `
                width: 100%;
                border-top: 1px dashed rgba(255, 255, 255, 0.6);
            `;
            this.list.appendChild(item);
            this.list.scrollTop = this.list.scrollHeight;
        }

        clear() {
            while (this.list.firstChild) {
                this.list.removeChild(this.list.firstChild);
            }
        }

        remove() {
            this.ctn.remove();
        }
    }

    // boss 直聘
    class Zhipin {
        constructor() {
            // 窗口标签
            this.targets = {
                search: "__zhipin_search",
                detail: "__zhipin_detail",
                chat: "__zhipin_chat",
                chatGreet: "__zhipin_chat_greet",
            };
            // 广播类型
            this.bcTypes = {
                // 全局
                STATUS: "status",
                RUN: 'run',
                DIVIDER: 'divider',
                INTRODUCE: 'introduce',
                HEART_BEAT: 'heart-beat',
                // 聊天页和职位详情页
                GET_JOB_INFO: 'get-job-info',
                SAY_HI: 'say-hi',
            };
            // 白名单
            this.whiteList = WHITELIST.zhipin;
            // 记录状态
            this.pause = false;
            this.tags = [];
            this.introduce = ''
        }

        // 注册广播
        __broadcast(target) {
            this.broadcast?.destroy('broadcast_replaced');
            this.broadcast = new WebBroadcast('__zhipin_broadcast', target);
        }

        // 搜索页
        async __search(tagIdx) {
            // api
            const api = new Api();
            const identity = deliveryIdentity.get();
            // 记录开始时间
            const start = new Date().getTime();
            let count = 0;
            let page = 0;
            // 记录职位链接
            let jobHrefs = [];
            let elsLen = 0;
            // 缓存
            let started = false;
            let pendingRoundRestart = false;
            let roundTransitioning = false;
            let currentRound = 0;
            let emptyRounds = 0;
            let roundQueuedCount = 0;
            let currentKeyword = '';
            let currentTagIdx = -1;
            const processedJobHrefs = new Set();
            const runtime = {
                state: 'idle',
                phase: '等待启动',
                keyword: '',
                currentJob: '',
                currentJobUrl: '',
                currentDecision: {},
                lastError: '',
                consecutiveFailures: 0,
                counters: { viewed: 0, queued: 0, sent: 0, failed: 0 },
                logs: [],
            };
            const control = {
                stopped: false,
                heartbeatBusy: false,
                health: {
                    startedAt: new Date().toISOString(),
                    lastHeartbeatAt: '',
                    lastHeartbeatOkAt: '',
                    heartbeatFailures: 0,
                },
            };

            const queueRuntimeLog = (message, level = 'info') => {
                runtime.logs.push({
                    level,
                    message: String(message).slice(0, 2000),
                    loggedAt: new Date().toISOString(),
                });
                if (runtime.logs.length > 200) runtime.logs.splice(0, runtime.logs.length - 200);
            };

            const sendRuntimeHeartbeat = async (statePatch = null) => {
                if (control.heartbeatBusy) return;
                control.heartbeatBusy = true;
                let logs = [];
                let logsRestored = false;
                const restoreLogs = () => {
                    if (logsRestored || !logs.length) return;
                    runtime.logs.unshift(...logs.slice(-100));
                    logsRestored = true;
                };
                try {
                    if (statePatch) Object.assign(runtime, statePatch);
                    logs = runtime.logs.splice(0, 50);
                    control.health.lastHeartbeatAt = new Date().toISOString();
                    const result = await api.heartbeat({
                        workerId: identity.workerId,
                        accountId: identity.accountId,
                        alias: localStorage.getItem('__goodjobs_worker_alias') || '',
                        scriptVersion: SCRIPT_VERSION,
                        role: 'search',
                        state: runtime.state,
                        phase: runtime.phase,
                        paused: this.pause,
                        keyword: runtime.keyword,
                        currentJob: runtime.currentJob,
                        currentJobUrl: runtime.currentJobUrl,
                        currentDecision: runtime.currentDecision,
                        queue: jobHrefs.slice(0, 50).map((url, index) => ({
                            id: url,
                            url,
                            status: index === 0 ? 'next' : 'pending',
                            keyword: currentKeyword,
                        })),
                        path: window.location.pathname,
                        counters: runtime.counters,
                        lastError: runtime.lastError,
                        consecutiveFailures: runtime.consecutiveFailures,
                        config: { threshold: OPTIONS.thread, onlyGreet: OPTIONS.onlyGreet },
                        health: { ...control.health, visible: document.visibilityState === 'visible', broadcastReady: Boolean(this.broadcast) },
                        logs,
                    });
                    if (!result) {
                        restoreLogs();
                        control.health.heartbeatFailures += 1;
                    } else {
                        control.health.lastHeartbeatOkAt = new Date().toISOString();
                        control.health.heartbeatFailures = 0;
                        // 后端心跳只用于监控，不接受或执行远程控制命令。
                    }
                } catch (error) {
                    restoreLogs();
                    control.health.heartbeatFailures += 1;
                    console.warn('[goodJobs] 运行状态心跳失败，将在下一轮重试', error);
                } finally {
                    control.heartbeatBusy = false;
                }
            };

            // 日志启动暂停事件
            const logger = new Logger(() => {
                this.pause = false;
                runtime.state = 'running';
                runtime.phase = '继续运行';
                if (!started) {
                    main().catch((error) => {
                        if (!isStopError(error)) logger.add(`初始化失败: ${error}`);
                    });
                    return;
                }
                if (pendingRoundRestart) {
                    pendingRoundRestart = false;
                    return startRound();
                }
                loop();
            }, () => {
                this.pause = true;
                runtime.state = 'paused';
                runtime.phase = '用户暂停';
                sendRuntimeHeartbeat();
            }, queueRuntimeLog);
            runtimeLifecycle.addCleanup(() => logger.remove());

            const heartbeatTimer = window.setInterval(() => sendRuntimeHeartbeat(), 8000);
            const resumeHeartbeat = () => {
                if (control.stopped || scriptLifecycle.isDisabled()) return;
                sendRuntimeHeartbeat();
            };
            const onVisibilityChange = () => {
                if (document.visibilityState === 'visible') resumeHeartbeat();
            };
            const onBeforeUnload = () => {
                window.clearInterval(heartbeatTimer);
                control.stopped = true;
                sendRuntimeHeartbeat({ state: 'stopped', phase: '页面关闭' });
            };
            document.addEventListener('visibilitychange', onVisibilityChange);
            window.addEventListener('focus', resumeHeartbeat);
            window.addEventListener('online', resumeHeartbeat);
            window.addEventListener('pageshow', resumeHeartbeat);
            window.addEventListener('beforeunload', onBeforeUnload);
            runtimeLifecycle.addCleanup(() => {
                window.clearInterval(heartbeatTimer);
                control.stopped = true;
                const stoppedHeartbeat = api.heartbeat({
                    workerId: identity.workerId,
                    accountId: identity.accountId,
                    scriptVersion: SCRIPT_VERSION,
                    role: 'search',
                    state: 'stopped',
                    phase: runtimeLifecycle.reason === 'user_restart' ? '脚本重启' : '脚本退出',
                    paused: true,
                    path: window.location.pathname,
                    counters: runtime.counters,
                    logs: [],
                });
                document.removeEventListener('visibilitychange', onVisibilityChange);
                window.removeEventListener('focus', resumeHeartbeat);
                window.removeEventListener('online', resumeHeartbeat);
                window.removeEventListener('pageshow', resumeHeartbeat);
                window.removeEventListener('beforeunload', onBeforeUnload);
                return stoppedHeartbeat;
            });

            // 开始广播
            const startBroadcast = () => {
                this.__broadcast(this.targets.search);
                // 接收聊天页的消息提醒
                this.broadcast.on(this.bcTypes.STATUS, (from, data) => {
                    if (from === this.targets.chat) {
                        logger.add(data);
                    }
                });
                // 发送自我介绍
                this.broadcast.on(this.bcTypes.INTRODUCE, async (from, data) => {
                    await this.broadcast.reply(
                        from,
                        this.bcTypes.INTRODUCE,
                        { introduce: this.introduce },
                        data.requestId,
                        data.responseType
                    ).catch(() => null);
                });
                // 分割线
                this.broadcast.on(this.bcTypes.DIVIDER, () => {
                    logger.divider();
                });
                // 监听打招呼
                greetListener();
                // 监听聊天页
                chatListener();
                // 心跳监听
                heartBeatListener();
            };

            // 执行搜索
            const search = async (kw) => {
                try {
                    const input = await tools.endlessFind(SELECTORS.ZHIPIN.SEARCH.SEARCHINPUT);
                    const btn = await tools.endlessFind(SELECTORS.ZHIPIN.SEARCH.SEARCHBTN);
                    tools.inputText(input, kw);
                    btn.click();
                } catch (e) {
                    logger.add('搜索出错');
                    throw new Error('搜索出错');
                }
            };

            // 获取职位链接
            const getJobHrefs = async () => {
                try {
                    const jobUl = await tools.endlessFind(SELECTORS.ZHIPIN.SEARCH.JOBLIST);
                    const aList = jobUl.querySelectorAll(SELECTORS.ZHIPIN.SEARCH.JOBHREFS);
                    const hrefs = Array.from(aList)
                        .map(a => a.href)
                        .slice(elsLen)
                        .filter(href => !processedJobHrefs.has(href));
                    return [hrefs, aList];
                } catch (e) {
                    logger.add('获取职位链接出错');
                    throw new Error('获取职位链接出错');
                }
            };

            const resetRoundState = () => {
                jobHrefs = [];
                elsLen = 0;
                page = 0;
                roundQueuedCount = 0;
                clearPendingGreet();
            };

            const activatePreloadCard = async (round) => {
                if (!OPTIONS.preloadActivateCardEvery || round % OPTIONS.preloadActivateCardEvery !== 0) return;
                try {
                    const jobUl = document.querySelector(SELECTORS.ZHIPIN.SEARCH.JOBLIST);
                    if (!jobUl) return;
                    const cards = Array.from(jobUl.querySelectorAll(SELECTORS.ZHIPIN.SEARCH.JOBCARD));
                    if (!cards.length) return;
                    const visibleCards = cards.filter(card => {
                        const rect = card.getBoundingClientRect();
                        return rect.top < window.innerHeight - 120 && rect.bottom > 120;
                    });
                    const targetCard = visibleCards[visibleCards.length - 1] || cards[cards.length - 1];
                    if (!targetCard) return;
                    targetCard.scrollIntoView({ block: 'center', behavior: 'smooth' });
                    await tools.asyncSleep(120);
                    targetCard.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                    targetCard.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                    targetCard.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                    logger.add(`预加载第 ${round} 轮：已轻点左侧岗位卡片`);
                    await tools.asyncSleep(OPTIONS.preloadActivateCardWaitMs);
                } catch (e) {
                    if (isStopError(e)) throw e;
                    logger.add('预加载时轻点岗位卡片失败，已继续纯滚动');
                }
            };

            // 下一页
            const nextPage = async () => {
                while (true) {
                    let hrefs, els;
                    [hrefs, els] = await getJobHrefs();
                    if (els.length === elsLen) {
                        logger.add('没有更多职位了');
                        return false;
                    }
                    elsLen = els.length;
                    els[elsLen - 1].scrollIntoView();
                    page++;
                    logger.add(`开始浏览第 ${page} 页`);
                    if (hrefs.length) {
                        jobHrefs.push(...hrefs);
                        roundQueuedCount += hrefs.length;
                        logger.add(`本页新增 ${hrefs.length} 个未处理岗位`);
                        return true;
                    }
                    logger.add('本页新增岗位都已处理过，继续向下查找');
                    await tools.asyncSleep(OPTIONS.preloadScrollWaitMs);
                }
            };

            let pendingGreetTimer = null;
            let pendingGreetTitle = '';
            let pendingGreetCompany = '';
            let pendingGreetDecision = null;
            let pendingGreetClaimToken = '';
            let pendingGreetId = '';
            let activeReservedClaimToken = '';
            let activeClaimPhase = '';

            const clearPendingGreet = () => {
                if (pendingGreetTimer) {
                    clearTimeout(pendingGreetTimer);
                    pendingGreetTimer = null;
                }
                pendingGreetTitle = '';
                pendingGreetCompany = '';
                pendingGreetDecision = null;
                pendingGreetClaimToken = '';
                const storedSession = localStorage.getItem(GREET_SESSION_KEY);
                if (storedSession) {
                    try {
                        const parsed = JSON.parse(storedSession);
                        if (!pendingGreetId || parsed.greetId === pendingGreetId) localStorage.removeItem(GREET_SESSION_KEY);
                    } catch (_) {
                        localStorage.removeItem(GREET_SESSION_KEY);
                    }
                }
                pendingGreetId = '';
            };

            runtimeLifecycle.addCleanup(() => {
                this.pause = true;
                control.stopped = true;
                const queuedClaimToken = pendingGreetClaimToken;
                const activeClaimToken = activeReservedClaimToken;
                const claimPhase = activeClaimPhase;
                clearPendingGreet();
                this.broadcast?.destroy('search_stopped');
                activeReservedClaimToken = '';
                activeClaimPhase = '';
                const cleanups = [];
                if (queuedClaimToken) {
                    cleanups.push(api.markDelivery(
                        queuedClaimToken,
                        'failed_unknown',
                        'script_stopped_waiting_greet_result',
                        { allowDuringStop: true, timeout: 2000 }
                    ));
                }
                if (activeClaimToken && activeClaimToken !== queuedClaimToken) {
                    cleanups.push(claimPhase === 'queued'
                        ? api.markDelivery(
                            activeClaimToken,
                            'failed_unknown',
                            'script_stopped_after_queue_before_greet',
                            { allowDuringStop: true, timeout: 2000 }
                        )
                        : api.releaseDelivery(
                            activeClaimToken,
                            'script_stopped_before_queue',
                            { allowDuringStop: true, timeout: 2000 }
                        ));
                }
                return Promise.allSettled(cleanups);
            });

            const armPendingGreet = (title, decision = null, company = '', claimToken = '') => {
                clearPendingGreet();
                pendingGreetId = `greet-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                pendingGreetTitle = title;
                pendingGreetCompany = company;
                pendingGreetDecision = decision;
                pendingGreetClaimToken = claimToken;
                localStorage.setItem(GREET_SESSION_KEY, JSON.stringify({
                    greetId: pendingGreetId,
                    claimToken,
                    title,
                    company,
                    createdAt: Date.now(),
                }));
                pendingGreetTimer = setTimeout(async () => {
                    logger.add(`职位 [${pendingGreetTitle}] 打招呼超时，已跳过`);
                    const timedOutClaimToken = pendingGreetClaimToken;
                    if (timedOutClaimToken) {
                        await api.markDelivery(timedOutClaimToken, 'failed_unknown', 'greet_timeout').catch(() => null);
                    }
                    clearPendingGreet();
                    loop();
                }, OPTIONS.greetTimeout);
            };

            const handleRoundExhausted = async () => {
                if (roundTransitioning) return;
                roundTransitioning = true;
                try {
                    if (roundQueuedCount === 0) {
                        emptyRounds += 1;
                        logger.add(`第 ${currentRound} 轮没有拿到新岗位（连续空轮 ${emptyRounds}/${OPTIONS.maxEmptyRounds}）`);
                    } else {
                        emptyRounds = 0;
                        logger.add(`第 ${currentRound} 轮已处理完当前加载岗位，准备进入下一轮`);
                    }
                    if (emptyRounds >= OPTIONS.maxEmptyRounds) {
                        logger.add(`连续 ${OPTIONS.maxEmptyRounds} 轮没有新岗位，自动切换到下一个关键词继续挂机`);
                        emptyRounds = 0;
                        return startRound();
                    }
                    await tools.asyncSleep(OPTIONS.roundRestartDelayMs);
                    if (this.pause) {
                        pendingRoundRestart = true;
                        logger.add('当前已暂停，下一轮等待继续');
                        return;
                    }
                    await startRound();
                } finally {
                    roundTransitioning = false;
                }
            };

            const logAction = async (payload) => {
                await safeLogAction(api, payload);
            };

            // 获取职位信息
            const getJobInfo = async (href) => {
                // 打开窗口
                const detailWindow = tools.openTabNSetTimestamp(href, this.targets.detail);
                if (!detailWindow) {
                    return { skip: true, skipReason: '浏览器拦截了职位详情窗口' };
                }
                // 接收职位信息
                const info = await this.broadcast.receive(
                    this.targets.detail,
                    this.bcTypes.GET_JOB_INFO,
                    OPTIONS.detailTimeout
                ).catch((error) => {
                    if (isStopError(error)) throw error;
                    return {
                        skip: true,
                        skipReason: `获取职位详情超时（>${(OPTIONS.detailTimeout / 1000).toFixed(0)}s）`,
                    };
                });
                return info;
            };

            // 添加到聊天列表
            const addToChatList = async (url) => {
                runtimeLifecycle.guard();
                return new Promise((resolve, reject) => {
                    fetch(url, { signal: runtimeLifecycle.signal })
                        .then(async resp => {
                            if (!(resp.ok && resp.status === 200)) {
                                const bodyText = await resp.text().catch(() => '');
                                logger.add(`boss直聘网络连接出错: status=${resp.status}`);
                                return reject(new Error(`http_${resp.status}:${bodyText.slice(0, 300)}`));
                            }
                            return resp.json();
                        }).then(resp => {
                            if (resp.code === 0) return resolve(resp);
                            const msg = resp?.zpData?.bizData?.chatRemindDialog?.title || resp?.message || '未知错误';
                            logger.add(`打招呼失败: ${msg}`);
                            reject(new Error(`biz_fail:${msg}`));
                        }).catch(err => {
                            if (runtimeLifecycle.isStopping() || err?.name === 'AbortError') {
                                reject(new ScriptStoppedError('queue_request_aborted'));
                                return;
                            }
                            reject(err instanceof Error ? err : new Error(String(err)));
                        });
                });
            };

            // 打招呼监听
            const greetListener = () => {
                this.broadcast.on(this.bcTypes.SAY_HI, async (from, data) => {
                    if (from !== this.targets.chatGreet) return;
                    const sessionMatches = Boolean(
                        pendingGreetId
                        && data?.greetId === pendingGreetId
                        && (!data?.claimToken || data.claimToken === pendingGreetClaimToken)
                    );
                    // 要自我介绍
                    if (data.requestId) {
                        if (!sessionMatches || runtimeLifecycle.isStopping()) {
                            await this.broadcast.reply(
                                from,
                                this.bcTypes.SAY_HI,
                                { cancelled: true, reason: 'greet_session_expired' },
                                data.requestId,
                                data.responseType
                            ).catch(() => null);
                            return;
                        }
                        const greetIntroduce = pendingGreetDecision?.introduce || this.introduce;
                        logger.add(`打招呼introduce: ${greetIntroduce.substring(0, 40)}...`);
                        await this.broadcast.reply(
                            from,
                            this.bcTypes.SAY_HI,
                            {
                                introduce: greetIntroduce,
                                resumeIndex: pendingGreetDecision?.resumeIndex ?? OPTIONS.resumeIndex,
                                greetId: pendingGreetId,
                                claimToken: pendingGreetClaimToken,
                            },
                            data.requestId,
                            data.responseType
                        ).catch(() => null);
                        return;
                    }
                    if (!sessionMatches) {
                        logger.add('已忽略过期的打招呼页面回执');
                        return;
                    }
                    // 告知结果
                    const finalDecision = pendingGreetDecision;
                    const finalTitle = pendingGreetTitle;
                    const finalCompany = pendingGreetCompany;
                    const finalClaimToken = pendingGreetClaimToken;
                    clearPendingGreet();
                    if (data.success) {
                        logger.add(`打招呼成功`);
                        runtime.counters.sent += 1;
                        runtime.state = 'sent';
                        runtime.phase = '打招呼成功';
                        if (finalClaimToken) {
                            await api.markDelivery(finalClaimToken, 'sent').catch((e) => {
                                console.log('markDelivery sent failed', e);
                            });
                        }
                        await logAction({
                            action: 'greet_sent',
                            scene: 'search',
                            title: finalTitle,
                            company: finalCompany,
                            claimToken: finalClaimToken,
                            resumeIndex: finalDecision?.resumeIndex ?? OPTIONS.resumeIndex,
                        });
                    }
                    // 出错了
                    else {
                        logger.add(`打招呼失败`);
                        runtime.counters.failed += 1;
                        runtime.state = 'error';
                        runtime.phase = '打招呼失败';
                        if (finalClaimToken) {
                            await api.markDelivery(finalClaimToken, 'failed_unknown', 'chat_greet_failed').catch((e) => {
                                console.log('markDelivery failed_unknown failed', e);
                            });
                        }
                        await logAction({
                            action: 'greet_failed',
                            scene: 'search',
                            title: finalTitle,
                            company: finalCompany,
                            claimToken: finalClaimToken,
                            resumeIndex: finalDecision?.resumeIndex ?? OPTIONS.resumeIndex,
                        });
                    }
                    loop();
                });
            };

            // 聊天页监听
            const chatListener = () => {
                this.broadcast.on(this.bcTypes.RUN, async (from, data) => {
                    if (from !== this.targets.chat) return;
                    if (data) {
                        logger.divider();
                        const hasNext = await nextPage();
                        if (!hasNext) return handleRoundExhausted();
                        loop();
                    } else {
                        logger.add(`消息处理出错，重试中...`);
                        tools.openTabNSetTimestamp(this.whiteList.chat, this.targets.chat);
                    }
                });
            };

            // 心跳监听
            const heartBeatListener = () => {
                this.broadcast.on(this.bcTypes.HEART_BEAT, async (from, data) => {
                    const sessionCancelled = Boolean(
                        data?.greetId
                        && (!pendingGreetId || data.greetId !== pendingGreetId)
                    );
                    await this.broadcast.reply(
                        from,
                        this.bcTypes.HEART_BEAT,
                        {
                            success: !control.stopped && !runtimeLifecycle.isStopping() && !sessionCancelled,
                            stopped: control.stopped || runtimeLifecycle.isStopping(),
                            cancelled: sessionCancelled,
                        },
                        data.requestId,
                        data.responseType
                    ).catch(() => null);
                });
            }

            // 单一搜索工作循环，避免暂停/继续或多个回执同时触发并发投递。
            let loopRunning = false;
            let loopRequested = false;
            const loop = async () => {
                if (loopRunning) {
                    loopRequested = true;
                    return;
                }
                loopRunning = true;
                loopRequested = false;
                try {
                    runtimeLifecycle.guard();
                    if (control.stopped) return;
                    // 如果暂停，则跳过
                    if (this.pause) {
                        logger.add('暂停中...');
                        return;
                    }
                    logger.divider();
                    // 判断职位链接是否为空
                    if (jobHrefs.length === 0) {
                        // 判断是否需要代聊天
                        if (OPTIONS.onlyGreet) {
                            const hasNext = await nextPage();
                            if (!hasNext) return handleRoundExhausted();
                            return loop();
                        }
                        logger.add('开始处理聊天消息');
                        tools.openTabNSetTimestamp(this.whiteList.chat, this.targets.chat);
                        return;
                    }
                    // 抽取第一个
                    const href = jobHrefs.shift();
                    runtime.currentJobUrl = href;
                    runtime.currentJob = '';
                    runtime.currentDecision = {};
                    const diff = (new Date().getTime() - start) / 1000;
                    // 获取详情
                    logger.add(`| 浏览: ${++count} | 剩余: ${jobHrefs.length} | 平均: ${(diff / count).toFixed(0)}s | 耗时: ${convertTime(diff)} |`);
                    logger.add(`正在获取职位详情`);
                    const jobInfo = await getJobInfo(href);
                    processedJobHrefs.add(href);
                    runtime.counters.viewed += 1;
                    runtime.currentJob = jobInfo.title || '';
                    if (control.stopped || this.pause) return;
                    if (jobInfo.skip) {
                        logger.add(`职位跳过: ${jobInfo.skipReason}`);
                        await logAction({
                            action: 'job_skip',
                            scene: 'search',
                            title: jobInfo.title || null,
                            salary: jobInfo.salary || null,
                            detail: jobInfo.detail || null,
                            reason: jobInfo.skipReason,
                        });
                        return loop();
                    }
                    // 如果聊过，下一个
                    if (jobInfo.talked) {
                        logger.add(`职位 [${jobInfo.title}] 已经聊过，下一个`);
                        await logAction({
                            action: 'job_already_talked',
                            scene: 'search',
                            title: jobInfo.title,
                            salary: jobInfo.salary,
                        });
                        return loop();
                    }
                    if (!jobInfo.company) {
                        logger.add(`职位 [${jobInfo.title}] 未识别公司，为避免重复投递已跳过`);
                        await logAction({
                            action: 'job_missing_company',
                            scene: 'search',
                            title: jobInfo.title,
                            accountId: identity.accountId,
                            workerId: identity.workerId,
                        });
                        return loop();
                    }
                    const duplicateCheck = await deliveryFlow.precheck(api, jobInfo.company, jobInfo.title);
                    if (duplicateCheck.unavailable) {
                        logger.add('重复投递检查服务不可用，为安全起见已跳过本岗位');
                        return loop();
                    }
                    if (duplicateCheck.greeted) {
                        const duplicateMessage = deliveryFlow.duplicateMessage(jobInfo.company, jobInfo.title);
                        logger.add(duplicateMessage);
                        console.log(`[goodJobs] ${duplicateMessage}`, duplicateCheck.delivery || {});
                        return loop();
                    }
                    // 否则发送消息计算匹配度
                    logger.add(`开始计算职位 [${jobInfo.title}] 的匹配度`);
                    runtime.state = 'evaluating';
                    runtime.phase = '岗位匹配评分';
                    const decision = await api.getJobScore(jobInfo.title, jobInfo.salary, jobInfo.detail);
                    runtime.currentDecision = {
                        workerId: identity.workerId,
                        company: jobInfo.company || '',
                        title: jobInfo.title || '',
                        stars: decision.stars ?? decision.score / 20,
                        rawStars: decision.rawStars,
                        deductedStars: decision.deductedStars ?? 0,
                        discarded: Boolean(decision.discarded),
                        score: decision.score,
                        deductions: decision.deductions || decision.matches || [],
                    };
                    logger.add(`岗位星级: ${decision.stars ?? decision.score / 20}/5 | 扣星: ${decision.deductedStars ?? 0} | 简历索引: ${decision.resumeIndex}`);
                    logDecisionDeductions(decision, (message) => logger.add(message));
                    await logAction({
                        action: 'job_decision_consumed',
                        scene: 'search',
                        title: jobInfo.title,
                        salary: jobInfo.salary,
                        location: jobInfo.location,
                        city: jobInfo.city,
                        industry: jobInfo.industry,
                        experience: jobInfo.experience,
                        education: jobInfo.education,
                        keyword: currentKeyword,
                        score: decision.score,
                        resumeIndex: decision.resumeIndex,
                    });
                    // 如果分数达到阈值，打个招呼
                    if (!decision.discarded && decision.score >= OPTIONS.thread) {
                        if (control.stopped || this.pause) return;
                        runtime.state = 'claiming';
                        runtime.phase = '领取投递权';
                        const claim = await deliveryFlow.claim(api, identity, jobInfo, href);
                        if (claim.reason === 'service_unavailable') {
                            logger.add('投递协调服务不可用，为避免重复投递已跳过');
                            await logAction({
                                action: 'delivery_claim_failed',
                                scene: 'search',
                                title: jobInfo.title,
                                company: jobInfo.company,
                                reason: String(claim.error),
                                accountId: identity.accountId,
                                workerId: identity.workerId,
                            });
                            return loop();
                        }

                        if (!claim.accepted) {
                            const isDuplicateJob = deliveryFlow.isDuplicate(claim);
                            if (claim.reason === 'daily_limit') {
                                logger.add(`账号 [${identity.accountId}] 今日已达上限（${claim.count}/${claim.limit}），自动暂停`);
                                this.pause = true;
                            } else if (isDuplicateJob) {
                                const duplicateMessage = deliveryFlow.duplicateMessage(jobInfo.company, jobInfo.title);
                                logger.add(duplicateMessage);
                                console.log(`[goodJobs] ${duplicateMessage}`, claim.existing || {});
                                return loop();
                            } else {
                                logger.add(`职位 [${jobInfo.title}] 无法领取投递权：${claim.reason}`);
                            }
                            await logAction({
                                action: 'delivery_claim_rejected',
                                scene: 'search',
                                title: jobInfo.title,
                                company: jobInfo.company,
                                reason: claim.reason,
                                existingTitle: claim.existing?.title || '',
                                existingStatus: claim.existing?.status || '',
                                accountId: identity.accountId,
                                workerId: identity.workerId,
                            });
                            if (claim.reason === 'daily_limit') return;
                            return loop();
                        }

                        activeReservedClaimToken = claim.claimToken;
                        activeClaimPhase = 'reserved';

                        if (control.stopped || this.pause) {
                            await api.releaseDelivery(claim.claimToken, 'control_interrupted_before_send').catch(() => null);
                            activeReservedClaimToken = '';
                            activeClaimPhase = '';
                            return control.stopped || this.pause ? undefined : loop();
                        }

                        logger.add(`职位 [${jobInfo.title}] 已通过全部筛选条件，准备进入发送队列`);
                        logger.add(`账号 [${identity.accountId}] 今日占用 ${claim.count}/${claim.limit}，剩余 ${claim.remaining} 次`);

                        try {
                            await addToChatList(jobInfo.addUrl);
                        } catch (err) {
                            if (isStopError(err)) {
                                await api.releaseDelivery(claim.claimToken, 'script_stopped_before_queue', { allowDuringStop: true }).catch(() => null);
                                activeReservedClaimToken = '';
                                activeClaimPhase = '';
                                throw err;
                            }
                            const queueError = String(err);
                            if (queueError.includes('biz_fail:')) {
                                await api.releaseDelivery(claim.claimToken, queueError).catch(() => null);
                            } else {
                                await api.markDelivery(claim.claimToken, 'failed_unknown', queueError).catch(() => null);
                            }
                            activeReservedClaimToken = '';
                            activeClaimPhase = '';
                            await logAction({
                                action: 'greet_queue_failed',
                                scene: 'search',
                                title: jobInfo.title,
                                resumeIndex: decision.resumeIndex,
                                addUrl: jobInfo.addUrl,
                                chatUrl: jobInfo.chatUrl,
                                reason: queueError,
                                accountId: identity.accountId,
                                workerId: identity.workerId,
                                claimToken: claim.claimToken,
                            });
                            clearPendingGreet();
                            return loop();
                        }

                        await api.markDelivery(claim.claimToken, 'queued', '', { allowDuringStop: true }).catch(async (err) => {
                            await logAction({
                                action: 'delivery_mark_queued_failed',
                                scene: 'search',
                                title: jobInfo.title,
                                company: jobInfo.company,
                                reason: String(err),
                                accountId: identity.accountId,
                                workerId: identity.workerId,
                                claimToken: claim.claimToken,
                            });
                        });
                        activeClaimPhase = 'queued';
                        runtime.counters.queued += 1;
                        runtime.state = 'queued';
                        runtime.phase = '已进入投递队列';
                        await logAction({
                            action: 'greet_queued',
                            scene: 'search',
                            title: jobInfo.title,
                            company: jobInfo.company,
                            salary: jobInfo.salary,
                            location: jobInfo.location,
                            city: jobInfo.city,
                            industry: jobInfo.industry,
                            experience: jobInfo.experience,
                            education: jobInfo.education,
                            keyword: currentKeyword,
                            resumeIndex: decision.resumeIndex,
                            score: decision.score,
                            accountId: identity.accountId,
                            workerId: identity.workerId,
                            claimToken: claim.claimToken,
                        });

                        logger.add(`所有条件已通过，最后生成职位 [${jobInfo.title}] 的招呼语`);
                        try {
                            const generated = await api.generateIntroduce(
                                claim.claimToken,
                                jobInfo.company,
                                jobInfo.title,
                                jobInfo.salary,
                                jobInfo.detail
                            );
                            decision.introduce = generated.introduce || this.introduce;
                            decision.introduceGenerated = Boolean(generated.generated);
                        } catch (err) {
                            if (isStopError(err)) throw err;
                            decision.introduce = this.introduce;
                            decision.introduceGenerated = false;
                            logger.add(`定制招呼语生成失败，使用固定招呼语: ${err}`);
                        }
                        logger.add(`最终招呼语: ${(decision.introduce || '').substring(0, 60)}...`);
                        try {
                            await logAction({
                                action: 'chat_open_requested',
                                scene: 'search',
                                title: jobInfo.title,
                                chatUrl: jobInfo.chatUrl,
                                resumeIndex: decision.resumeIndex,
                                accountId: identity.accountId,
                                workerId: identity.workerId,
                                claimToken: claim.claimToken,
                            });
                            armPendingGreet(jobInfo.title, decision, jobInfo.company, claim.claimToken);
                            const greetWindow = tools.openTabNSetTimestamp(jobInfo.chatUrl, this.targets.chatGreet);
                            if (!greetWindow) throw new Error('浏览器拦截了打招呼窗口');
                            activeReservedClaimToken = '';
                            activeClaimPhase = '';
                            return;
                        } catch (err) {
                            if (isStopError(err)) throw err;
                            await api.markDelivery(claim.claimToken, 'failed_unknown', String(err), { allowDuringStop: true }).catch(() => null);
                            activeReservedClaimToken = '';
                            activeClaimPhase = '';
                            await logAction({
                                action: 'chat_open_failed',
                                scene: 'search',
                                title: jobInfo.title,
                                resumeIndex: decision.resumeIndex,
                                addUrl: jobInfo.addUrl,
                                chatUrl: jobInfo.chatUrl,
                                reason: String(err),
                                accountId: identity.accountId,
                                workerId: identity.workerId,
                                claimToken: claim.claimToken,
                            });
                            clearPendingGreet();
                            return loop();
                        }
                    }
                    // 否则下一轮
                    else {
                        runtime.state = 'running';
                        runtime.phase = '继续扫描';
                        await logAction({
                            action: 'job_below_threshold',
                            scene: 'search',
                            title: jobInfo.title,
                            salary: jobInfo.salary,
                            score: decision.score,
                            threshold: OPTIONS.thread,
                            resumeIndex: decision.resumeIndex,
                        });
                        loop();
                    }
                } catch (e) {
                    if (isStopError(e) || runtimeLifecycle.isStopping()) return;
                    console.log(e);
                    logger.add(`循环时出错: ${e}`);
                    runtime.state = 'error';
                    runtime.phase = '循环异常';
                    runtime.lastError = String(e);
                    runtime.counters.failed += 1;
                    sendRuntimeHeartbeat();
                    loop();
                } finally {
                    loopRunning = false;
                    if (loopRequested && !control.stopped && !this.pause && !runtimeLifecycle.isStopping()) {
                        loopRequested = false;
                        queueMicrotask(() => loop());
                    }
                }
            };

            const preloadJobs = async () => {
                logger.add('开始慢速预加载岗位列表');
                let stableRounds = 0;
                let lastCount = 0;
                let lastScrollY = -1;
                for (let round = 1; round <= OPTIONS.preloadMaxRounds; round++) {
                    runtimeLifecycle.guard();
                    const jobUl = await tools.endlessFind(SELECTORS.ZHIPIN.SEARCH.JOBLIST).catch((error) => {
                        if (isStopError(error)) throw error;
                        return null;
                    });
                    const currentCount = jobUl ? jobUl.querySelectorAll(SELECTORS.ZHIPIN.SEARCH.JOBHREFS).length : 0;
                    window.scrollBy({ top: OPTIONS.preloadScrollPixels, left: 0, behavior: 'smooth' });
                    await tools.asyncSleep(OPTIONS.preloadScrollWaitMs);
                    await activatePreloadCard(round);
                    const afterJobUl = document.querySelector(SELECTORS.ZHIPIN.SEARCH.JOBLIST);
                    const afterCount = afterJobUl ? afterJobUl.querySelectorAll(SELECTORS.ZHIPIN.SEARCH.JOBHREFS).length : currentCount;
                    const afterY = window.scrollY;
                    logger.add(`预加载第 ${round} 轮：岗位 ${currentCount} -> ${afterCount}`);
                    if (afterCount > lastCount || afterY > lastScrollY) {
                        stableRounds = 0;
                    } else {
                        stableRounds += 1;
                    }
                    lastCount = Math.max(lastCount, afterCount);
                    lastScrollY = Math.max(lastScrollY, afterY);
                    if (stableRounds >= OPTIONS.preloadStableRoundsLimit) {
                        logger.add(`预加载结束：连续 ${stableRounds} 轮无新增岗位`);
                        break;
                    }
                }
                const finalJobUl = document.querySelector(SELECTORS.ZHIPIN.SEARCH.JOBLIST);
                const finalCount = finalJobUl ? finalJobUl.querySelectorAll(SELECTORS.ZHIPIN.SEARCH.JOBHREFS).length : 0;
                logger.add(`预加载完成，当前已加载岗位数：${finalCount}`);
            };

            const pickNextKeyword = () => {
                if (!this.tags || !this.tags.length) {
                    throw new Error('未获取到岗位关键词列表');
                }
                currentTagIdx = (currentTagIdx + 1) % this.tags.length;
                currentKeyword = this.tags[currentTagIdx];
                runtime.keyword = currentKeyword;
                return currentKeyword;
            };

            let startRoundRunning = false;
            let startRoundRequested = false;
            const startRound = async () => {
                if (startRoundRunning) {
                    startRoundRequested = true;
                    return;
                }
                startRoundRunning = true;
                startRoundRequested = false;
                try {
                    runtimeLifecycle.guard();
                    if (control.stopped) return;
                    if (this.pause) {
                        pendingRoundRestart = true;
                        return;
                    }
                    resetRoundState();
                    currentRound += 1;
                    const keyword = pickNextKeyword();
                    logger.divider();
                    logger.add(`开始第 ${currentRound} 轮`);
                    logger.add(`本轮搜索关键词：${keyword}`);
                    window.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
                    await tools.asyncSleep(600);
                    await search(keyword);
                    logger.add(`第 ${currentRound} 轮已完成搜索（关键词：${keyword}），请在 ${(OPTIONS.manualFilterWaitMs / 1000).toFixed(0)} 秒内手动选择地区、薪资等筛选条件`);
                    await tools.asyncSleep(OPTIONS.manualFilterWaitMs);
                    await preloadJobs();
                    runtimeLifecycle.guard();
                    // 预加载完成后，先提取一批岗位链接再进入循环
                    const hasNext = await nextPage();
                    logger.add(`第 ${currentRound} 轮开始按当前筛选条件扫描岗位（关键词：${keyword}）`);
                    if (!hasNext) return handleRoundExhausted();
                    loop();
                } finally {
                    startRoundRunning = false;
                    if (startRoundRequested && !control.stopped && !this.pause && !runtimeLifecycle.isStopping()) {
                        startRoundRequested = false;
                        queueMicrotask(() => startRound());
                    }
                }
            };

            // 主函数
            const main = async () => {
                started = true;
                runtime.state = 'running';
                runtime.phase = '初始化脚本';
                logger.add('--程序启动--');
                // 开始广播
                startBroadcast();
                // 获取统一配置
                const clientConfig = await api.getClientConfig().catch((e) => {
                    if (isStopError(e)) throw e;
                    logger.add('获取统一配置失败，将回退旧接口');
                    return null;
                });
                if (clientConfig && clientConfig.frontend) {
                    Object.assign(OPTIONS, clientConfig.frontend);
                    logger.add('获取前端配置成功');
                }
                logger.add(`浏览器实例: ${identity.workerId}`);
                logger.add(`当前账号标识: ${identity.accountId}`);
                logger.add(`脚本版本: ${SCRIPT_VERSION}`);
                sendRuntimeHeartbeat();
                if (clientConfig && Array.isArray(clientConfig.tags) && clientConfig.tags.length) {
                    this.tags = clientConfig.tags;
                    logger.add('获取标签成功: ' + this.tags.join('、'));
                } else {
                    this.tags = await api.getTags();
                    logger.add('获取标签成功(旧接口): ' + this.tags.join('、'));
                }
                if (typeof tagIdx === 'number' && this.tags.length) {
                    currentTagIdx = ((tagIdx % this.tags.length) + this.tags.length) % this.tags.length - 1;
                }
                if (clientConfig && typeof clientConfig.introduce === 'string' && clientConfig.introduce) {
                    this.introduce = clientConfig.introduce;
                    logger.add('获取自我介绍成功');
                } else {
                    this.introduce = await api.getIntroduce();
                    logger.add('获取自我介绍成功(旧接口)');
                }
                // 检查每日打招呼限制
                try {
                    const daily = await api.checkDailyLimit(identity.accountId);
                    logger.add(`今日已打招呼 ${daily.count}/${daily.limit}，剩余 ${daily.remaining} 次`);
                    if (daily.reached) {
                        logger.add('今日打招呼已达上限，暂停运行');
                        this.pause = true;
                        return;
                    }
                } catch (e) {
                    if (isStopError(e)) throw e;
                    logger.add('每日限制检查失败');
                }
                await startRound();
            };

            // 初始化
            const init = () => {
                // 如果时间戳小于阈值，直接运行
                if (start - tools.getTimestamp(this.targets.search) < OPTIONS.timestampTimeout) {
                    logger.runBtn.click();
                }
            };

            init();
        }

        // 详情页
        __detail() {
            // 注册广播
            const startBroadcast = () => {
                this.__broadcast(this.targets.detail);
            };
            startBroadcast();

            // 获取职位信息
            const getJobInfo = () => {
                const chatBtn = document.querySelector(SELECTORS.ZHIPIN.DETAIL.STARTCHAT);
                const nameBox = document.querySelector(SELECTORS.ZHIPIN.DETAIL.NAMEBOX);
                const title = nameBox?.querySelector(SELECTORS.ZHIPIN.DETAIL.JOBNAME)?.innerText?.trim() || '';
                const salary = nameBox?.querySelector(SELECTORS.ZHIPIN.DETAIL.SALARY)?.innerText?.trim() || '';
                const detail = document.querySelector(SELECTORS.ZHIPIN.DETAIL.DETAIL)?.innerText?.trim() || '';
                const companyEl = document.querySelector(SELECTORS.ZHIPIN.DETAIL.COMPANY);
                const company = companyEl ? companyEl.innerText.trim() : '';
                const locationEl = document.querySelector(SELECTORS.ZHIPIN.DETAIL.LOCATION);
                const location = locationEl ? locationEl.innerText.trim() : '';
                const qualificationTexts = Array.from(document.querySelectorAll(SELECTORS.ZHIPIN.DETAIL.QUALIFICATION_TAGS))
                    .map(el => el.innerText.trim()).filter(Boolean);
                const experience = qualificationTexts.find(text => /经验|应届|在校|不限|\d+年/.test(text)) || '';
                const education = qualificationTexts.find(text => /学历|初中|高中|中专|大专|本科|硕士|博士/.test(text)) || '';
                const industryEl = document.querySelector(SELECTORS.ZHIPIN.DETAIL.INDUSTRY);
                const industry = industryEl ? industryEl.innerText.trim() : '';
                const city = tools.extractCity(location);
                const actionText = chatBtn ? chatBtn.innerText.trim() : '';
                const chatUrl = chatBtn && chatBtn.getAttribute(SELECTORS.ZHIPIN.DETAIL.CHATURL);
                const addUrl = chatBtn && chatBtn.dataset.url;
                let skip = false;
                let skipReason = '';

                if (!chatBtn) {
                    skip = true;
                    skipReason = '未找到立即沟通按钮';
                } else if (actionText.indexOf('立即沟通') === -1) {
                    skip = true;
                    skipReason = `按钮为 [${actionText || '未知'}]，疑似网申岗位`;
                } else if (!chatUrl || !addUrl) {
                    skip = true;
                    skipReason = '缺少聊天链接，疑似异常岗位';
                }

                return {
                    title,
                    salary,
                    detail,
                    company,
                    location,
                    city,
                    industry,
                    experience,
                    education,
                    actionText,
                    chatUrl,
                    addUrl,
                    skip,
                    skipReason,
                    talked: chatBtn && chatBtn.dataset.isfriend === 'true',
                };
            };
            const jobInfo = getJobInfo();

            // 来自搜索页
            const fromSearchPage = () => {
                // 把职位信息发送给搜索页
                this.broadcast.send(this.targets.search, this.bcTypes.GET_JOB_INFO, jobInfo).catch(() => null);
            };

            // 来自聊天页
            const fromChatPage = () => {
                // 把职位信息发送给聊天页
                this.broadcast.send(
                    this.targets.chat,
                    this.bcTypes.GET_JOB_INFO,
                    jobInfo
                ).then(() => {
                    window.close();
                }).catch(() => null);
            };

            // 主函数
            const main = () => {
                // 判断来源
                const now = new Date().getTime();
                const isFromSearch = now - tools.getTimestamp(this.targets.detail) < OPTIONS.timestampTimeout && window.name === this.targets.detail;
                const isFromChat = now - tools.getTimestamp(this.targets.chat) < OPTIONS.timestampTimeout;

                if (isFromSearch) {
                    fromSearchPage();
                } else if (isFromChat) {
                    fromChatPage();
                }
            };
            main();
        }

        // 聊天页
        async __chat() {
            // 注册广播
            const startBroadcast = (target = this.targets.chat) => {
                this.__broadcast(target);
            };
            const chatApi = new Api();
            const logAction = async (payload) => {
                await safeLogAction(chatApi, payload);
            };

            // 发送消息（双重保险 + 日志）
            const sendMsg = async (text, logFn) => {
                runtimeLifecycle.guard();
                const log = logFn || ((msg) => console.log('[sendMsg]', msg));
                const ipt = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.CHATINPUT);
                const btn = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.MSGSEND);

                // 第一步：聚焦并清空
                log('聚焦输入框');
                ipt.focus();
                ipt.click();
                await tools.asyncSleep(200);
                ipt.innerHTML = '';
                await tools.asyncSleep(100);

                // 第二步：尝试方法A - execCommand
                log('方法A: execCommand插入文字');
                const execOk = document.execCommand('insertText', false, text);
                await tools.asyncSleep(600);
                let inputHasText = ipt.innerText.trim().length > 0;
                log(`方法A结果: execOk=${execOk}, 输入框有文字=${inputHasText}`);

                // 第三步：如果方法A失败，尝试方法B - innerText + 事件模拟
                if (!inputHasText) {
                    log('方法A失败，尝试方法B: innerText + 事件模拟');
                    ipt.focus();
                    ipt.innerText = text;
                    ipt.dispatchEvent(new Event('input', { bubbles: true }));
                    ipt.dispatchEvent(new Event('change', { bubbles: true }));
                    ipt.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'a' }));
                    ipt.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
                    await tools.asyncSleep(600);
                    inputHasText = ipt.innerText.trim().length > 0;
                    log(`方法B结果: 输入框有文字=${inputHasText}`);
                }

                // 第四步：如果还是没有，尝试方法C - innerHTML
                if (!inputHasText) {
                    log('方法B失败，尝试方法C: innerHTML');
                    ipt.focus();
                    ipt.innerHTML = `<p>${text}</p>`;
                    ipt.dispatchEvent(new Event('input', { bubbles: true }));
                    await tools.asyncSleep(600);
                    inputHasText = ipt.innerText.trim().length > 0;
                    log(`方法C结果: 输入框有文字=${inputHasText}`);
                }

                if (!inputHasText) {
                    log('所有方法均失败，无法写入输入框');
                    throw new Error('无法写入输入框');
                }

                // 第五步：点击发送
                runtimeLifecycle.guard();
                log('点击发送按钮');
                btn.click();
                await tools.asyncSleep(500);

                // 第六步：验证是否发送成功（输入框清空 = 成功）
                let sent = ipt.innerText.trim().length === 0;
                if (!sent) {
                    runtimeLifecycle.guard();
                    log('第一次点击未发送成功，重试');
                    btn.click();
                    await tools.asyncSleep(500);
                    sent = ipt.innerText.trim().length === 0;
                }

                if (!sent) {
                    log('发送结果无法确认');
                    throw new Error('点击发送后输入框未清空，发送结果未知');
                }
                log('发送成功');
                return true;
            };

            // 打招呼
            const sayHi = async () => {
                startBroadcast(this.targets.chatGreet);

                let session = null;
                try { session = JSON.parse(localStorage.getItem(GREET_SESSION_KEY) || 'null'); } catch (_) { /* ignore */ }
                if (!session?.greetId || !session?.claimToken) {
                    this.broadcast.destroy('missing_greet_session');
                    window.close();
                    return;
                }

                runPeerHeartbeat(
                    this.broadcast,
                    this.targets.search,
                    this.bcTypes.HEART_BEAT,
                    () => ({ greetId: session.greetId, claimToken: session.claimToken })
                ).catch((error) => {
                    if (!runtimeLifecycle.isStopping()) {
                        runtimeLifecycle.stop(error?.reason || 'greet_peer_lost')
                            .finally(() => window.close());
                    }
                });

                try {
                    const greetDecision = await this.broadcast.sendAndReceive(
                        this.targets.search,
                        this.bcTypes.SAY_HI,
                        { greetId: session.greetId, claimToken: session.claimToken }
                    );
                    if (greetDecision?.cancelled) throw new ScriptStoppedError('greet_session_cancelled');
                    const introduce = greetDecision.introduce;
                    await sendMsg(introduce, (msg) => console.log('[sayHi]', msg));
                    await this.broadcast.send(this.targets.search, this.bcTypes.SAY_HI, {
                        success: true,
                        greetId: session.greetId,
                        claimToken: session.claimToken,
                    });
                } catch (e) {
                    if (!runtimeLifecycle.isStopping()) {
                        await this.broadcast.send(this.targets.search, this.bcTypes.SAY_HI, {
                            success: false,
                            greetId: session.greetId,
                            claimToken: session.claimToken,
                            reason: String(e),
                        }).catch(() => null);
                    }
                } finally {
                    this.broadcast.destroy('greet_finished');
                    window.setTimeout(() => window.close(), 50);
                }
            };

            // 获取聊天记录信息
            const getChatInfo = async () => {
                const ctn = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.HISTORYCTN);

                const getMsgs = async () => {
                    const lis = Array.from(ctn.querySelectorAll(SELECTORS.ZHIPIN.CHAT.USEFULMSG));
                    // 提取历史记录
                    const msgs = [];
                    lis.forEach(li => {
                        const role = li.classList.contains('item-friend') ? 'user' : 'assistant';
                        const msgBox = li.querySelector(SELECTORS.ZHIPIN.CHAT.MSGCONTENT);
                        if (!msgBox) return;
                        msgs.push({
                            role,
                            content: msgBox.innerText,
                        });
                    });
                    // 提取简历，作品集状态
                    let needResume = 0;
                    let needWorks = 0;
                    let resumeSended = false;
                    let worksSended = false;
                    let confirmAddr = false;
                    // 判断聊天字眼中是否有相关信息
                    msgs.reverse();
                    let recent = '';
                    for (const msg of msgs) {
                        if (msg.role !== 'user') {
                            break;
                        }
                        recent += msg.content;
                    }
                    msgs.reverse();
                    if (recent.indexOf('简历') !== -1) {
                        needResume = 1;
                    }
                    if (recent.indexOf('作品') !== -1) {
                        needWorks = 1;
                    }
                    // 判断是否有过明确弹窗
                    const rlis = lis.reverse();
                    for (const li of rlis) {
                        if (li.classList.contains('item-myself')) {
                            break;
                        }
                        const bossGreen = li.querySelector('.boss-green');
                        const dialog = li.querySelector('.item-dialog');
                        if (bossGreen) {
                            const t = bossGreen.innerText;
                            if (t.indexOf('我想要一份您的附件简历，您是否同意\n拒绝\n同意') !== -1) {
                                needResume = 2;
                            }
                        } else if (dialog) {
                            const t = dialog.querySelector('.msg-dialog-title').innerText;
                            if (t.indexOf('您是否接受此工作地点?') !== -1) {
                                confirmAddr = true;
                            }
                        }
                    }
                    // 判断是否发过简历
                    const bossGreen = ctn.querySelectorAll('.boss-green');
                    if (bossGreen.length) {
                        bossGreen.forEach(el => {
                            const t = el.innerText;
                            if (t.indexOf('点击预览附件简历') !== -1) {
                                resumeSended = true;
                            }
                        });
                    }
                    return {
                        msgs,
                        needResume,
                        needWorks,
                        resumeSended,
                        worksSended,
                        confirmAddr,
                        talked: !msgs.every(d => d.role === 'user'),
                        jobEl: (await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.JOBEL)).querySelector(SELECTORS.ZHIPIN.CHAT.JOBCITY)
                    };
                };

                const scroll2Top = async () => {
                    if (ctn.scrollTop === 0) return;
                    ctn.scrollTop = 0;
                    await tools.asyncSleep(300);
                    await scroll2Top();
                };

                // 滚动到顶部
                await tools.asyncSleep(300);
                await scroll2Top();
                // 获取聊天记录
                return await getMsgs();
            };

            // 发送简历
            const sendResume = async (resumeIndex = OPTIONS.resumeIndex) => {
                runtimeLifecycle.guard();
                const sendBtn = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.RESUMESEND);
                runtimeLifecycle.guard();
                sendBtn.click();

                // 可能是弹一个小窗
                const smallDialog = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.RESUMEMODAL).catch((error) => {
                    if (isStopError(error)) throw error;
                    return null;
                });
                if (smallDialog) {
                    runtimeLifecycle.guard();
                    smallDialog.querySelector(SELECTORS.ZHIPIN.CHAT.RESUMEMODALCONFIRM).click();
                    await sendMsg('已发送，请查收', (msg) => console.log('[sendResume]', msg));
                    return {
                        mode: 'small_dialog',
                        selectedResumeIndex: resumeIndex,
                    };
                }

                // 弹出大窗让选择
                const resumeCtn = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.RESUMELIST);
                const confirm = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.RESUMESENDCONFIRM);
                const resumes = resumeCtn.querySelectorAll(SELECTORS.ZHIPIN.CHAT.RESUMELISTITEM);
                const fallbackIndex = resumes[resumeIndex] ? resumeIndex : (resumes[OPTIONS.resumeIndex] ? OPTIONS.resumeIndex : 0);
                const resume = resumes[fallbackIndex];
                await tools.asyncSleep(300);
                runtimeLifecycle.guard();
                resume.click();
                await tools.asyncSleep(300);
                runtimeLifecycle.guard();
                confirm.click();
                await sendMsg('已发送，请查收');
                return {
                    mode: 'resume_list',
                    selectedResumeIndex: fallbackIndex,
                };
            };

            let logger = null;
            let activeChatClaimToken = '';
            let activeChatMessageStarted = false;
            runtimeLifecycle.addCleanup(() => {
                this.broadcast?.destroy('chat_stopped');
                logger?.remove();
                const claimToken = activeChatClaimToken;
                const messageStarted = activeChatMessageStarted;
                activeChatClaimToken = '';
                activeChatMessageStarted = false;
                if (claimToken) {
                    const cleanupRequest = messageStarted
                        ? chatApi.markDelivery(claimToken, 'failed_unknown', 'chat_script_stopped_during_send', { allowDuringStop: true, timeout: 2000 })
                        : chatApi.releaseDelivery(claimToken, 'chat_script_stopped_before_send', { allowDuringStop: true, timeout: 2000 });
                    return cleanupRequest.catch(() => null);
                }
                return null;
            });
            // 给搜索页同步状态
            const status = (text) => {
                if (runtimeLifecycle.isStopping()) return;
                logger && logger.add(text);
                this.broadcast && this.broadcast.send(
                    this.targets.search,
                    this.bcTypes.STATUS,
                    text
                ).catch(() => null);
            };
            // 分割线
            const divider = () => {
                logger && logger.divider();
                this.broadcast && this.broadcast.send(this.targets.search, this.bcTypes.DIVIDER).catch(() => null);
            };

            // 聊天
            const chat = async () => {
                // api
                const api = chatApi;
                const identity = deliveryIdentity.get();
                // 开始广播
                startBroadcast(this.targets.chat);
                // 获取默认自我介绍（兜底）
                const defaultIntroduce = (await this.broadcast.sendAndReceive(
                    this.targets.search,
                    this.bcTypes.INTRODUCE,
                )).introduce;
                runPeerHeartbeat(
                    this.broadcast,
                    this.targets.search,
                    this.bcTypes.HEART_BEAT
                ).catch((error) => {
                    if (!runtimeLifecycle.isStopping()) {
                        runtimeLifecycle.stop(error?.reason || 'chat_peer_lost')
                            .finally(() => window.close());
                    }
                });

                // 一轮
                let round = 0;
                let lastTop = 0;
                const once = async () => {
                    runtimeLifecycle.guard();
                    // 获取联系人列表
                    let empty = false;
                    const ctn = await tools.endlessFind(SELECTORS.ZHIPIN.CHAT.CONTACTLIST).catch(e => {
                        if (isStopError(e)) throw e;
                        if (document.querySelector(SELECTORS.ZHIPIN.CHAT.CONTACTLISTEMPTY)) {
                            status('当前暂无消息');
                            empty = true;
                        }
                    });
                    if (empty) return;
                    if (!ctn) throw new Error('未找到联系人列表');
                    const lis = ctn.querySelectorAll(SELECTORS.ZHIPIN.CHAT.CONTACTLISTITEM);
                    // 遍历新消息
                    for (const ls of lis) {
                        try {
                            runtimeLifecycle.guard();
                            // 无新消息
                            if (!ls.querySelector(SELECTORS.ZHIPIN.CHAT.NEWMSGNOTICE)) continue;
                            // 获取联系人信息
                            const name = ls.querySelector(SELECTORS.ZHIPIN.CHAT.USERNAME);
                            const company = name.nextElementSibling.innerText;
                            divider();
                            status(`[${company} - ${name.innerText}] 发来一条新消息`);
                            // 进入聊天界面
                            runtimeLifecycle.guard();
                            name.click();
                            // 获取聊天记录信息
                            const chatInfo = await getChatInfo();
                            // 如果最新的是我的回复
                            const lastMsg = chatInfo.msgs.slice(-1)[0];
                            if (lastMsg && lastMsg.role === 'assistant') continue;
                            // 如果以前没聊过
                            if (!chatInfo.talked) {
                                localStorage.setItem(this.targets.chat, new Date().getTime());
                                runtimeLifecycle.guard();
                                chatInfo.jobEl.click();
                                status(`正在获取职位详情`);
                                const jobInfo = await this.broadcast.receive(this.targets.detail, this.bcTypes.GET_JOB_INFO);
                                if (!company) {
                                    status(`职位 [${jobInfo.title}] 未识别公司，为避免重复投递已跳过`);
                                    continue;
                                }
                                const duplicateCheck = await deliveryFlow.precheck(api, company, jobInfo.title);
                                if (duplicateCheck.unavailable) {
                                    status('重复投递检查服务不可用，为安全起见已跳过本岗位');
                                    continue;
                                }
                                if (duplicateCheck.greeted) {
                                    const duplicateMessage = deliveryFlow.duplicateMessage(company, jobInfo.title);
                                    status(duplicateMessage);
                                    console.log(`[goodJobs] ${duplicateMessage}`, duplicateCheck.delivery || {});
                                    continue;
                                }
                                // 获取职位匹配度
                                status(`开始计算职位 [${jobInfo.title}] 的匹配度`);
                                const decision = await api.getJobScore(jobInfo.title, jobInfo.salary, jobInfo.detail);
                                status(`岗位星级: ${decision.stars ?? decision.score / 20}/5 | 扣星: ${decision.deductedStars ?? 0} | 简历索引: ${decision.resumeIndex}`);
                                logDecisionDeductions(decision, (message) => status(message));
                                await logAction({
                                    action: 'job_decision_consumed',
                                    scene: 'chat',
                                    title: jobInfo.title,
                                    salary: jobInfo.salary,
                                    location: jobInfo.location,
                                    city: jobInfo.city,
                                    industry: jobInfo.industry,
                                    experience: jobInfo.experience,
                                    education: jobInfo.education,
                                    score: decision.score,
                                    resumeIndex: decision.resumeIndex,
                                });
                                // 如果分数达到阈值并且未聊过天，打个招呼
                                if (!decision.discarded && decision.score >= OPTIONS.thread && !chatInfo.msgs.length) {
                                    const claim = await deliveryFlow.claim(
                                        api,
                                        identity,
                                        { ...jobInfo, company },
                                        window.location.href
                                    );
                                    if (claim.reason === 'service_unavailable') {
                                        status('投递协调服务不可用，为避免重复投递已跳过');
                                        continue;
                                    }
                                    if (!claim.accepted) {
                                        if (claim.reason === 'daily_limit') {
                                            status(`账号 [${identity.accountId}] 今日已达上限（${claim.count}/${claim.limit}）`);
                                        } else if (deliveryFlow.isDuplicate(claim)) {
                                            const duplicateMessage = deliveryFlow.duplicateMessage(company, jobInfo.title);
                                            status(duplicateMessage);
                                            console.log(`[goodJobs] ${duplicateMessage}`, claim.existing || {});
                                        } else {
                                            status(`无法领取投递权：${claim.reason}`);
                                        }
                                        continue;
                                    }
                                    activeChatClaimToken = claim.claimToken;
                                    activeChatMessageStarted = false;
                                    status(`职位 [${jobInfo.title}] 已通过全部条件，最后生成招呼语`);
                                    let finalIntroduce = defaultIntroduce;
                                    try {
                                        const generated = await api.generateIntroduce(
                                            claim.claimToken,
                                            company,
                                            jobInfo.title,
                                            jobInfo.salary,
                                            jobInfo.detail
                                        );
                                        finalIntroduce = generated.introduce || defaultIntroduce;
                                    } catch (e) {
                                        if (isStopError(e)) throw e;
                                        status(`定制招呼语生成失败，使用固定招呼语: ${e}`);
                                    }
                                    try {
                                        activeChatMessageStarted = true;
                                        await sendMsg(finalIntroduce, (msg) => status(`[sendMsg] ${msg}`));
                                        await api.markDelivery(claim.claimToken, 'sent', '', { allowDuringStop: true });
                                        activeChatClaimToken = '';
                                        activeChatMessageStarted = false;
                                        await logAction({
                                            action: 'chat_greet_sent',
                                            scene: 'chat',
                                            title: jobInfo.title,
                                            company: company,
                                            salary: jobInfo.salary,
                                            location: jobInfo.location,
                                            city: jobInfo.city,
                                            industry: jobInfo.industry,
                                            experience: jobInfo.experience,
                                            education: jobInfo.education,
                                            resumeIndex: decision.resumeIndex,
                                            accountId: identity.accountId,
                                            workerId: identity.workerId,
                                            claimToken: claim.claimToken,
                                        });
                                        status(`打招呼成功`);
                                    } catch (e) {
                                        await api.markDelivery(claim.claimToken, 'failed_unknown', String(e), { allowDuringStop: true }).catch(() => null);
                                        activeChatClaimToken = '';
                                        activeChatMessageStarted = false;
                                        if (isStopError(e)) throw e;
                                        await logAction({
                                            action: 'chat_greet_failed',
                                            scene: 'chat',
                                            title: jobInfo.title,
                                            resumeIndex: decision.resumeIndex,
                                            reason: String(e),
                                            accountId: identity.accountId,
                                            workerId: identity.workerId,
                                            claimToken: claim.claimToken,
                                        });
                                        status(`打招呼失败: ${e}`);
                                    }
                                    continue;
                                }
                                // 未达到阈值，直接下一个
                                else if (decision.discarded || decision.score < OPTIONS.thread) {
                                    await logAction({
                                        action: 'chat_rejected_below_threshold',
                                        scene: 'chat',
                                        title: jobInfo.title,
                                        score: decision.score,
                                        threshold: OPTIONS.thread,
                                        resumeIndex: decision.resumeIndex,
                                    });
                                    await sendMsg('不好意思，不太合适哈，祝早日找到合适的人选。', (msg) => status(`[sendMsg] ${msg}`))
                                    continue;
                                }
                            }
                            let isChat = true;
                            // 只要对方发来新消息且还没发过简历，就直接发送简历，不再调用大模型聊天
                            if (!chatInfo.resumeSended) {
                                isChat = false;
                                localStorage.setItem(this.targets.chat, new Date().getTime());
                                runtimeLifecycle.guard();
                                chatInfo.jobEl.click();
                                status(`正在获取职位详情（用于确定简历）`);
                                const jobInfo = await this.broadcast.receive(this.targets.detail, this.bcTypes.GET_JOB_INFO);
                                const decision = await api.getJobScore(jobInfo.title, jobInfo.salary, jobInfo.detail);
                                status(`检测到新消息，直接发送简历（简历索引 ${decision.resumeIndex}）`);
                                const resumeResult = await sendResume(decision.resumeIndex);
                                await logAction({
                                    action: 'resume_sent',
                                    scene: 'chat',
                                    title: jobInfo.title,
                                    salary: jobInfo.salary,
                                    requestedResumeIndex: decision.resumeIndex,
                                    selectedResumeIndex: resumeResult?.selectedResumeIndex ?? decision.resumeIndex,
                                    sendMode: resumeResult?.mode || 'unknown',
                                });
                                status('发送成功');
                            }
                            // 是否需要作品集（当前关闭自动发送，仅保留原入口）
                            if (chatInfo.needWorks && !chatInfo.worksSended) {
                                isChat = false;
                                status('检测到作品集相关消息，当前未开启自动发送作品集');
                            }
                            // 聊天
                            if (isChat) {
                                status('已发过简历，跳过自动聊天');
                            }
                        } catch (e) {
                            if (isStopError(e) || runtimeLifecycle.isStopping()) throw e;
                            status('回复某条消息出错');
                        }
                    }
                    // 向下滚动
                    ctn.scrollTop = 1014 * ++round;
                    await tools.asyncSleep(300);
                    runtimeLifecycle.guard();
                    if (ctn.scrollTop !== lastTop) {
                        lastTop = ctn.scrollTop;
                        await once();
                    }
                };
                // 完成一轮
                await once();
            };

            // 主函数
            const main = async () => {
                // 判断来源
                const now = new Date().getTime();
                const isGreet = now - tools.getTimestamp(this.targets.chatGreet) < OPTIONS.timestampTimeout && window.name === this.targets.chatGreet;
                const isChat = now - tools.getTimestamp(this.targets.chat) < OPTIONS.timestampTimeout && window.name === this.targets.chat;

                if (isGreet) {
                    await sayHi();
                }
                else if (isChat) {
                    // 日志
                    logger = new Logger();
                    logger.runBtn.remove();
                    logger.clearBtn.remove();
                    // 等待加载
                    await tools.asyncSleep(3000);
                    runtimeLifecycle.guard();
                    try {
                        await chat();
                        runtimeLifecycle.guard();
                        status('消息处理完毕');
                        await this.broadcast.send(this.targets.search, this.bcTypes.RUN, true);
                    } catch (error) {
                        if (!isStopError(error) && !runtimeLifecycle.isStopping()) {
                            status('聊天程序运行出错');
                            await this.broadcast.send(this.targets.search, this.bcTypes.RUN, false).catch(() => null);
                        }
                    } finally {
                        this.broadcast?.destroy('chat_finished');
                    }
                }
            };
            main().catch((error) => {
                if (!isStopError(error)) console.error('[goodJobs] 聊天页初始化失败', error);
            });
        }

        // 运行
        run(tagIdx = 0) {
            const path = location.pathname;
            // 在搜索页
            if (path.startsWith(SEARCHPATH.zhipin)) {
                this.__search(tagIdx);
            }
            // 在详情页
            else if (path.startsWith(this.whiteList.deatil)) {
                this.__detail();
            }
            // 在聊天页
            else if (path.startsWith(this.whiteList.chat)) {
                this.__chat();
            }
            // 否则跳转搜索页
            else {
                new Logger(() => {
                    tools.openTabNSetTimestamp(SEARCHPATH.zhipin, this.targets.search, true);
                });
            }
        }
    }

    const goodjobs = new Zhipin().run();
})();
