# 2026-07-19 投递转化复盘汇报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从仓库日志和投递状态库生成一份可复算、可打开、面向管理层的 7 页中文 PPT，解释当日进入投递少和 3% 指标异常。

**Architecture:** 新增一个只读分析/制作用 Python 脚本，解析 `job_actions.jsonl`、`ai_filter_log.jsonl` 和 `delivery_state.db`，将核心统计保存为 JSON 快照并用 `python-pptx` 生成演示文稿。PPT 只使用原生形状和文本，避免运行项目服务或增加业务依赖。

**Tech Stack:** Python 3、`sqlite3`、`json`、`python-pptx`、PowerPoint 原生形状。

---

### Task 1: 固化审计口径

**Files:**
- Create: `reports/2026-07-19-delivery-conversion-review-data.json`
- Create: `scripts/build_delivery_review_ppt.py`

- [x] **Step 1: 实现只读数据抽取**

读取三类数据源，固定筛选 `2026-07-19`，输出评估量、进入投递状态、按账号计数、事件损耗和运行时间段；数据库连接使用只读查询，不写回任何仓库数据。

- [x] **Step 2: 运行脚本生成快照**

运行 `C:\Users\MineAurora\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\build_delivery_review_ppt.py --data-only`，确认快照包含 `evaluated=473`、`entered=95`、`sent=89`、`displayedRate=3` 和 `sameDayRate=20.1`。

### Task 2: 生成 PPT

**Files:**
- Modify: `scripts/build_delivery_review_ppt.py`
- Create: `reports/2026-07-19-delivery-conversion-review.pptx`

- [x] **Step 1: 添加统一主题和页脚**

设置 16:9 页面、深色背景、标题层级、颜色常量和数据来源脚注；每页以 `add_textbox`、`add_shape` 等原生对象绘制。

- [x] **Step 2: 添加 7 页内容**

按设计文档依次添加封面、指标纠偏、漏斗、损耗归因、运行效率、原因定级和整改优先级，所有核心数字来自快照而不是手写常量。

- [x] **Step 3: 运行脚本生成 PPTX**

运行 `C:\Users\MineAurora\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe scripts\build_delivery_review_ppt.py`，输出 JSON 快照和 PPTX。

### Task 3: 验证与交付

**Files:**
- Test: `reports/2026-07-19-delivery-conversion-review.pptx`
- Modify: `agents.md` only if a new process cleanup note is needed; otherwise no change.

- [x] **Step 1: 校验统计结果**

用独立只读 Python 查询重新计算 `473`、`95`、`89`、`3665`、`20.1%` 和 `2.6%`，与快照逐项比较。

- [x] **Step 2: 校验 PPTX 结构**

使用 `python-pptx` 打开文件，断言页数为 7、每页至少一个标题文本和一个来源脚注文本。

- [x] **Step 3: 检查工作区与进程**

运行 `git diff --check`、`git status --short`，确认没有服务进程被脚本启动且相关 Python 进程已退出。

- [x] **Step 4: 提交中文 Git 日志**

使用 `git add docs/superpowers/specs docs/superpowers/plans scripts/build_delivery_review_ppt.py reports`，提交信息为 `分析：复盘2026-07-19投递转化率与低进入量原因`。
