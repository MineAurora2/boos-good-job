# 投递记录经验学历采集与匹配度移除设计

## 背景与目标

“全部投递记录”当前能展示经验和学历字段，但浏览器脚本写入的两个值始终为空；同一页面还展示了用户不再需要的匹配度。本次改动需要修复后续投递记录的经验、学历采集，并从投递记录功能中完整移除匹配度展示，同时保留岗位评分和投递阈值判断。

## 根因证据

在用户当前登录的 BOSS 职位详情页中，岗位头部的经验元素为 `<span class="text-desc text-experiece">1-3年</span>`，学历元素为 `<span class="text-desc text-degree">本科</span>`。BOSS 使用了 `text-experiece` 这一既有拼写。

现有脚本却从 `.job-primary .job-tags span` 等标签集合中查找经验和学历。浏览器实测该集合包含“节日福利”“带薪年假”“五险一金”等福利，不包含岗位经验或学历，因此写入 `job_actions.jsonl` 的 `experience` 和 `education` 持续为空。

## 采集设计

- 在职位详情选择器中分别声明经验和学历选择器，不再把两个字段混在福利标签集合中扫描。
- 经验优先读取 `.job-primary .text-experiece`，并兼容站点未来可能修正为 `.job-primary .text-experience`；学历读取 `.job-primary .text-degree`。
- 将读取逻辑收敛为一个无副作用的岗位资格提取函数，返回 `{ experience, education }`，供搜索页和聊天页共享的职位详情数据流使用。
- 当头部元素不存在或文本为空时，从 `meta[name="description"]` 的标准职位摘要中降级提取“要求”和“学历”。解析失败时继续返回空字符串，不阻断评分或投递流程。
- 新采集值继续通过现有 `jobInfo -> logAction -> job_actions.jsonl -> load_dashboard_data -> dashboard` 数据链路进入列表、筛选、详情和 CSV，不新增数据库字段或接口。

该修复只影响更新油猴脚本后产生的新记录。历史日志没有可靠的职位详情来源，本次不猜测或回填既有空值。

## 匹配度移除设计

- 从投递记录默认列、列顺序、动态列定义、单元格渲染和列设置中删除 `score`。
- 从记录详情抽屉和 CSV 导出中删除“匹配度”字段。
- 删除仅供投递记录匹配度进度条使用的 `.score-pill` 样式。
- 表格偏好恢复继续按现有有效列白名单过滤。旧 `localStorage` 中残留的 `score` 会被忽略，不导致列设置报错。
- 保留评分接口、评分规则配置、动作日志中的 `score`、AI 筛选以及投递阈值判断；它们仍是自动投递核心逻辑，不属于本次展示删除范围。

## 测试与验收

- 先增加失败测试，证明当前详情页结构无法通过现有选择器采集 `1-3年` 和 `本科`。
- 覆盖 BOSS 当前错误拼写 `text-experiece`、兼容拼写 `text-experience`、DOM 缺失时元描述降级，以及所有来源缺失时返回空值。
- 增加前端负向契约，验证投递记录默认列、列定义、详情、CSV 和专用样式不再包含匹配度，同时评分配置界面仍保留。
- 验证旧表格偏好包含 `score` 时会被安全过滤。
- 运行油猴脚本与 Dashboard 的 Node 测试和语法检查，再运行仓库完整测试脚本。
- 启动本地 Dashboard 做一次浏览器检查，确认经验/学历列仍可用、匹配度不再出现、页面无控制台错误；检查结束后关闭本次启动的项目进程。

## 预计改动范围

修改 `web_script.js`、`dashboard/app.js`、`dashboard/styles.css`、`dashboard/index.html` 及对应本地回归测试。不会修改现有 `job_actions.jsonl`、`delivery_state.db`、评分算法、用户配置或历史投递记录。
