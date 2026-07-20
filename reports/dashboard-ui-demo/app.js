(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const state = {
    view: 'overview', theme: 'dark', connection: 'online', automation: 'running',
    analysisTab: 'performance', analysisRange: '7d', recordSearch: '', recordStatus: 'all',
    settingsDirty: false, pendingAutomationRow: null, lastFocus: null, sheetFocus: null,
    selectedRecordRows: new Set(), settingsGroup: 'profile'
  };
  const titles = { overview: '总览', automation: '自动化', analysis: '分析', records: '记录', settings: '设置' };
  const jobs = {
    algorithm: { title: '推荐算法工程师', company: '星海科技', citySalary: '上海 · 30-45K', status: '已投递', statusClass: 'success', statusIcon: '✓', match: '92 分', hr: '刚刚活跃', reason: '技能关键词与简历经历高度匹配' },
    frontend: { title: '资深前端工程师', company: '云图智能', citySalary: '杭州 · 25-40K', status: '已投递', statusClass: 'success', statusIcon: '✓', match: '88 分', hr: '今日活跃', reason: 'React 与性能优化经验匹配' },
    product: { title: 'AI 产品经理', company: '深蓝网络', citySalary: '深圳 · 28-42K', status: '待跟进', statusClass: 'warning', statusIcon: '!', match: '76 分', hr: '2 小时前', reason: 'AI 产品规划经验匹配，建议补充行业案例' },
    data: { title: '数据平台工程师', company: '极光数据', citySalary: '北京 · 32-50K', status: '未通过', statusClass: 'failed', statusIcon: '×', match: '64 分', hr: '未活跃', reason: '待补充大规模数据平台案例' }
  };

  const toast = (message) => {
    const region = $('#toast-region'); if (!region) return;
    const node = document.createElement('div'); node.className = 'toast'; node.textContent = message; region.append(node);
    window.setTimeout(() => node.remove(), 2600);
  };
  const focusable = (root) => $$('button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled])', root);
  const activeModal = () => [$('#detailDrawer'), $('#confirmDialog'), $('#filterSheet')].find((el) => el && !el.hidden && el.getAttribute('aria-hidden') !== 'true');

  function switchView(view) {
    if (!titles[view]) return;
    if (state.settingsDirty && state.view === 'settings' && view !== 'settings' && !window.confirm('设置尚未保存，确定离开吗？')) return;
    state.settingsDirty = state.view === 'settings' && view === 'settings' ? state.settingsDirty : false;
    state.view = view;
    $$('.view').forEach((section) => section.classList.toggle('is-visible', section.dataset.title === titles[view]));
    $$('[data-view]').forEach((item) => { const active = item.dataset.view === view; item.classList.toggle('is-active', active); active ? item.setAttribute('aria-current', 'page') : item.removeAttribute('aria-current'); });
    if ($('#crumb-current')) $('#crumb-current').textContent = titles[view];
    if (window.scrollTo) window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }

  function setTheme() {
    state.theme = state.theme === 'dark' ? 'light' : 'dark'; document.body.dataset.theme = state.theme;
    const values = state.theme === 'light' ? ['#f3f7f6', '#fff', '#edf3f1', '#d5e0dd', '#162522', '#667775'] : ['#0d1117', '#131a22', '#19232d', '#293642', '#edf5f2', '#91a3a6'];
    ['--bg', '--surface', '--surface-2', '--line', '--text', '--muted'].forEach((name, index) => document.documentElement.style.setProperty(name, values[index]));
    if ($('#theme-toggle')) $('#theme-toggle').textContent = state.theme === 'dark' ? '☼' : '☾'; toast(state.theme === 'dark' ? '已切换为深色主题' : '已切换为浅色主题');
  }

  function setConnection(value) {
    const labels = { online: '在线', loading: '加载中', offline: '离线', auth: '需授权' }; state.connection = value;
    const button = $('#connection'), menu = $('#connection-menu'); if (!button || !menu) return;
    button.className = `connection is-${value}`; button.setAttribute('aria-label', `连接状态：${labels[value]}`); if ($('#connection-label')) $('#connection-label').textContent = labels[value];
    menu.hidden = true; button.setAttribute('aria-expanded', 'false'); toast(value === 'online' ? '连接已恢复' : `连接状态：${labels[value]}`);
  }

  function openDrawer(record) {
    const job = jobs[record] || jobs.algorithm; const drawer = $('#detailDrawer'); if (!drawer) return;
    state.lastFocus = document.activeElement; drawer.hidden = false; drawer.setAttribute('aria-hidden', 'false'); drawer.classList.add('is-open');
    $('#drawer-title').textContent = job.title; $('#drawer-company').textContent = `${job.company} / ${job.citySalary}`; $('#drawer-match').textContent = job.match; $('#drawer-status').textContent = job.status; $('#drawer-meta').textContent = `${job.citySalary} · ${job.hr}`; $('#drawer-hr').textContent = job.hr; $('#drawer-reason').textContent = job.reason; $('#drawer-status-icon').className = `run-status ${job.statusClass}`; $('#drawer-status-icon').textContent = job.statusIcon; $('#drawer-log').textContent = `09:42 已读取${job.title}描述 · 已匹配简历 · 状态：${job.status}`;
    $('#drawer-close').focus();
  }
  function closeDrawer() {
    const drawer = $('#detailDrawer'); if (!drawer) return; drawer.classList.remove('is-open'); drawer.hidden = true; drawer.setAttribute('aria-hidden', 'true');
    if (state.lastFocus && state.lastFocus.focus) state.lastFocus.focus();
  }
  function closeConfirm() { $('#confirmDialog').hidden = true; $('#confirmDialog').setAttribute('aria-hidden', 'true'); if (state.lastFocus?.focus) state.lastFocus.focus(); }
  function restoreSheetFocus() { if (state.sheetFocus?.focus) state.sheetFocus.focus(); state.sheetFocus = null; }
  function closeSheet() { $('#filterSheet').hidden = true; $('#filterSheet').setAttribute('aria-hidden', 'true'); restoreSheetFocus(); }

  function applyRecordFilters() {
    const query = state.recordSearch.toLowerCase(); $$('#recordsBody tr').forEach((row) => { row.hidden = !((state.recordStatus === 'all' || row.dataset.status === state.recordStatus) && (!query || row.textContent.toLowerCase().includes(query))); });
    updateRecordSelection();
  }

  function updateRecordSelection() {
    const rows = $$('#recordsBody tr');
    rows.forEach((row) => {
      const key = row.dataset.recordKey;
      const checkbox = row.querySelector('.record-select');
      if (checkbox && key) checkbox.checked = state.selectedRecordRows.has(key);
    });
    const visibleRows = rows.filter((row) => !row.hidden && row.dataset.recordKey);
    const selectedVisible = visibleRows.filter((row) => state.selectedRecordRows.has(row.dataset.recordKey)).length;
    const count = $('#records-selected-count');
    if (count) count.textContent = `已选择 ${state.selectedRecordRows.size} 条`;
    const selectAll = $('#records-select-all');
    if (selectAll) {
      selectAll.checked = visibleRows.length > 0 && selectedVisible === visibleRows.length;
      selectAll.indeterminate = selectedVisible > 0 && selectedVisible < visibleRows.length;
    }
  }

  function setupRecordBulkTools() {
    const body = $('#recordsBody');
    const toolbar = $('#records-bulk-tools');
    if (!body || !toolbar) return;
    const rows = $$('tr', body);
    rows.forEach((row, index) => {
      const key = row.querySelector('.row-open')?.dataset.record || `record-${index + 1}`;
      row.dataset.recordKey = key;
      let cell = row.querySelector('.record-select-cell');
      if (!cell) {
        cell = document.createElement('td');
        cell.className = 'record-select-cell';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'record-select';
        checkbox.dataset.recordKey = key;
        checkbox.setAttribute('aria-label', `选择${row.querySelector('td:nth-child(2)')?.textContent.trim() || '投递记录'}`);
        checkbox.addEventListener('change', () => {
          checkbox.checked ? state.selectedRecordRows.add(key) : state.selectedRecordRows.delete(key);
          updateRecordSelection();
        });
        cell.append(checkbox);
        row.prepend(cell);
      }
    });
    const header = $('.records-table thead tr');
    if (header && !header.querySelector('.record-select-head')) {
      const th = document.createElement('th');
      th.className = 'record-select-head';
      th.textContent = '选择';
      header.prepend(th);
    }
    const selectAll = toolbar.querySelector('input[type="checkbox"]');
    if (selectAll) {
      selectAll.id = 'records-select-all';
      selectAll.setAttribute('aria-label', '选择全部记录');
      selectAll.addEventListener('change', () => {
        rows.filter((row) => !row.hidden).forEach((row) => {
          const key = row.dataset.recordKey;
          selectAll.checked ? state.selectedRecordRows.add(key) : state.selectedRecordRows.delete(key);
        });
        updateRecordSelection();
      });
    }
    const count = toolbar.querySelector('span');
    if (count) count.id = 'records-selected-count';
    const actions = $$('.text-btn', toolbar);
    if (actions[0]) {
      actions[0].id = 'records-mark-followup';
      actions[0].addEventListener('click', () => {
        rows.filter((row) => state.selectedRecordRows.has(row.dataset.recordKey)).forEach((row) => {
          row.dataset.status = 'warning';
          const chip = row.querySelector('.status-chip');
          if (chip) { chip.className = 'status-chip paused'; chip.textContent = '待跟进'; }
        });
        applyRecordFilters();
        toast(state.selectedRecordRows.size ? '已将选中记录标记为待跟进' : '请先选择投递记录');
      });
    }
    if (actions[1]) {
      actions[1].id = 'records-export-csv';
      actions[1].addEventListener('click', () => {
        const exportRows = rows.filter((row) => !state.selectedRecordRows.size || state.selectedRecordRows.has(row.dataset.recordKey));
        const csv = ['职位,公司,城市 / 薪资,投递状态,HR 活跃', ...exportRows.map((row) => [...row.querySelectorAll('td')].slice(1, 6).map((cell) => `"${cell.textContent.trim().replaceAll('"', '""')}"`).join(','))].join('\\n');
        if (typeof URL !== 'undefined' && URL.createObjectURL) {
          const blob = new Blob([`\\ufeff${csv}`], { type: 'text/csv;charset=utf-8' });
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a'); link.href = url; link.download = 'job-radar-records.csv'; link.click();
          window.setTimeout(() => URL.revokeObjectURL(url), 0);
        }
        toast(`已导出 ${exportRows.length} 条记录`);
      });
    }
    updateRecordSelection();
  }
  function markSettingsDirty() { state.settingsDirty = true; if ($('#save-settings')) $('#save-settings').disabled = false; const banner = $('#settings-unsaved'); if (banner) { banner.classList.add('is-dirty'); banner.querySelector('strong').textContent = '有未保存的更改'; banner.querySelector('small').textContent = '保存后才会应用到下一轮任务'; } }

  function switchSettingsGroup(group) {
    state.settingsGroup = group;
    $$('#settings-groups [data-settings-tab]').forEach((tab) => {
      const active = tab.dataset.settingsTab === group;
      tab.classList.toggle('is-active', active);
      tab.setAttribute('aria-selected', String(active));
      tab.setAttribute('tabindex', active ? '0' : '-1');
    });
    $$('#settings-groups .settings-section').forEach((section) => {
      const active = section.dataset.settingsGroup === group;
      section.hidden = !active;
    });
  }

  function setupSettingsGroups() {
    const root = $('#settings-groups');
    if (!root) return;
    const groups = ['profile', 'browser', 'llm', 'prompt', 'deduction'];
    const tabs = $$('.settings-nav button', root);
    const sections = $$('.settings-content .settings-section', root);
    const labels = ['求职资料', '浏览器参数', 'LLM 接口', '提示词', '扣星规则'];
    if (sections.length < groups.length && sections[2]) {
      const prompt = document.createElement('article');
      prompt.className = 'settings-section';
      prompt.innerHTML = '<h2>提示词</h2><label>岗位评分提示词<input value="提取技能、行业场景与可迁移经验"></label><label>跟进消息提示词<input value="简洁、具体、带有岗位上下文"></label>';
      $('.settings-content', root)?.append(prompt);
    }
    const panels = $$('.settings-content .settings-section', root);
    groups.forEach((group, index) => {
      const tab = tabs[index];
      const section = panels[index];
      if (!tab || !section) return;
      tab.dataset.settingsTab = group;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', `settings-panel-${group}`);
      tab.setAttribute('aria-label', labels[index]);
      section.dataset.settingsGroup = group;
      section.id = `settings-panel-${group}`;
      section.setAttribute('role', 'tabpanel');
      tab.addEventListener('click', () => switchSettingsGroup(group));
    });
    switchSettingsGroup(state.settingsGroup);
    $$('.settings-content input', root).forEach((input) => input.addEventListener('input', markSettingsDirty));
  }

  function updateAnalysisRange(value) {
    state.analysisRange = value; const label = $('#analysis-range-label'); if (label) label.textContent = value === '30d' ? '最近 30 天 · 72 次投递' : '最近 7 天 · 18 次投递';
    const summary = $('#analysis-summary'); if (summary) summary.textContent = value === '30d' ? '最近 30 天 · 72 次投递 · 18 次沟通 · 8 次面试' : '最近 7 天 · 18 次投递 · 4 次沟通 · 2 次面试';
    const title = $('#analysis-title'); if (title) title.dataset.range = value; toast(`时间范围：${value === '30d' ? '最近 30 天' : '最近 7 天'}`);
  }

  // Base navigation and connection menu semantics.
  $$('[data-view]').forEach((item) => item.addEventListener('click', () => switchView(item.dataset.view)));
  $('#theme-toggle')?.addEventListener('click', setTheme);
  $('#connection')?.addEventListener('click', () => { const menu = $('#connection-menu'); menu.hidden = !menu.hidden; $('#connection').setAttribute('aria-expanded', String(!menu.hidden)); });
  const connectionMenu = $('#connection-menu'); if (connectionMenu) { connectionMenu.setAttribute('role', 'menu'); $$('[data-connection]', connectionMenu).forEach((item) => { item.setAttribute('role', 'menuitem'); item.addEventListener('click', () => setConnection(item.dataset.connection)); }); }
  $('#command-palette')?.addEventListener('click', () => toast('命令面板演示：按快捷键搜索操作')); $('#profile-menu')?.addEventListener('click', () => toast('个人菜单暂为演示状态'));
  $$('[id^="new-automation"]').forEach((button) => button.addEventListener('click', () => toast('投递计划向导即将打开')));

  // Pause/resume automation with a real row reference and focus restoration.
  $$('.row-menu').forEach((button) => { const rowTitle = button.closest('.automation-row')?.querySelector('.auto-copy strong')?.textContent.trim() || '投递计划'; button.setAttribute('aria-label', `${rowTitle}：暂停或继续`); button.addEventListener('click', () => { const row = button.closest('.automation-row'); const chip = row.querySelector('.status-chip'); if (chip.classList.contains('paused')) { chip.className = 'status-chip running'; chip.innerHTML = '<i></i>运行中'; toast(`${rowTitle}已继续`); return; } state.pendingAutomationRow = row; state.lastFocus = button; $('#confirmDialog').hidden = false; $('#confirmDialog').setAttribute('aria-hidden', 'false'); $('#confirm-ok').focus(); }); });
  $('#confirm-cancel')?.addEventListener('click', closeConfirm); $('#confirm-ok')?.addEventListener('click', () => { const chip = state.pendingAutomationRow?.querySelector('.status-chip'); if (chip) { chip.className = 'status-chip paused'; chip.innerHTML = '<i></i>已暂停'; } state.automation = 'paused'; closeConfirm(); toast('投递计划已暂停'); });

  // Analysis tabs, keyboard navigation, and observable range state.
  const tabs = $$('[data-analysis-tab]'); tabs.forEach((button, index) => { button.setAttribute('tabindex', button.getAttribute('aria-selected') === 'true' ? '0' : '-1'); button.addEventListener('click', () => { state.analysisTab = button.dataset.analysisTab; tabs.forEach((tab) => { const active = tab === button; tab.classList.toggle('is-active', active); tab.setAttribute('aria-selected', String(active)); tab.setAttribute('tabindex', active ? '0' : '-1'); const panel = document.getElementById(tab.getAttribute('aria-controls')); if (panel) panel.hidden = !active; }); const title = $('#analysis-title'); if (title) title.textContent = button.textContent; }); button.addEventListener('keydown', (event) => { if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return; event.preventDefault(); tabs[(index + (event.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length].focus(); }); });
  $('#analysis-filter')?.addEventListener('change', (event) => updateAnalysisRange(event.target.value === '最近 30 天' ? '30d' : '7d')); $('#export-analysis')?.addEventListener('click', () => toast('分析报告已准备下载'));
  if ($('#analysis-range-label') === null && $('#analysis-filter')) { const label = document.createElement('span'); label.id = 'analysis-range-label'; label.textContent = '最近 7 天 · 18 次投递'; $('#analysis-filter').after(label); }

  // Records, drawer, and mobile filter sheet.
  setupRecordBulkTools();
  $('#recordSearch')?.addEventListener('input', (event) => { state.recordSearch = event.target.value; applyRecordFilters(); }); $('#recordStatus')?.addEventListener('change', (event) => { state.recordStatus = event.target.value; applyRecordFilters(); });
  $$('.row-open').forEach((button) => button.addEventListener('click', () => openDrawer(button.dataset.record))); $('#drawer-close')?.addEventListener('click', closeDrawer); $('.drawer-backdrop')?.addEventListener('click', closeDrawer); $('#refresh-records')?.addEventListener('click', () => toast('记录已刷新')); $('#clear-records')?.addEventListener('click', () => toast('清理记录需要管理员确认'));
  $('#recordFilterButton')?.addEventListener('click', () => { state.sheetFocus = document.activeElement; $('#filterSheet').hidden = false; $('#filterSheet').setAttribute('aria-hidden', 'false'); $('#sheetStatus').value = state.recordStatus; $('#sheetStatus').focus(); }); $('#sheetApply')?.addEventListener('click', () => { state.recordStatus = $('#sheetStatus').value; $('#recordStatus').value = state.recordStatus; closeSheet(); applyRecordFilters(); });

  // Settings persistence guard.
  setupSettingsGroups();
  $$('#view-settings input').forEach((input) => input.addEventListener('input', markSettingsDirty)); $('#save-settings')?.addEventListener('click', () => { state.settingsDirty = false; $('#save-settings').disabled = true; const banner = $('#settings-unsaved'); if (banner) { banner.classList.remove('is-dirty'); banner.querySelector('strong').textContent = '所有设置已保存'; banner.querySelector('small').textContent = '刚刚保存'; } toast('设置已保存'); });
  window.addEventListener('beforeunload', (event) => { if (!state.settingsDirty) return; event.preventDefault(); event.returnValue = ''; });

  document.addEventListener('click', (event) => { if (!event.target.closest('.connection-wrap')) { $('#connection-menu').hidden = true; $('#connection').setAttribute('aria-expanded', 'false'); } });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { $('#connection-menu').hidden = true; $('#connection').setAttribute('aria-expanded', 'false'); if (!$('#detailDrawer').hidden) closeDrawer(); if (!$('#confirmDialog').hidden) closeConfirm(); if (!$('#filterSheet').hidden) closeSheet(); return; }
    const modal = activeModal(); if (event.key === 'Tab' && modal) { const items = focusable(modal); if (!items.length) return; const first = items[0], last = items[items.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); } }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); toast('命令面板演示：输入操作名称'); }
  });
})();
