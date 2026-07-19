# README 设置参数说明实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `readme.md` 中增加完整、准确、可检索的前端面板设置说明。

**Architecture:** 只修改 README 文档，不改变运行代码。新增章节按面板分组使用 Markdown 表格，配置键、范围和运行行为均以 `app/config.py`、`app/storage/admin_store.py`、`web_script.js` 和 `app/llm/` 的实际逻辑为依据；删除旧的重复短说明，保留快速开始流程。

**Tech Stack:** Markdown、PowerShell、Git。

---

### Task 1: 建立设置说明章节

**Files:**
- Modify: `readme.md`，位于“统计面板同时提供”之后、“部署浏览器脚本”之前

- [ ] **Step 1: 添加基础资料、后端参数和浏览器脚本参数表**

写明固定招呼语、回复风格、搜索关键词、当前简历、每日投递上限、数据库文件、简历序号、匹配阈值、通信有效期、仅打招呼、轮次等待、空轮上限、详情超时、招呼超时，并同时列出默认值/范围、单位和实际作用。

- [ ] **Step 2: 添加预加载、防检测和 HR 筛选说明**

分别说明预加载滚动距离、等待、稳定轮数、最大轮数、岗位卡激活间隔/等待；说明防检测总开关对子开关的约束、乱序、跳过概率、不带招呼概率和随机延时；列出 HR 活跃状态多选值及未知状态行为。

- [ ] **Step 3: 添加扣星规则说明**

说明 5 星起算、每星 20 分、标题/描述关键词匹配、重叠关键词优先长词、累计扣星、丢弃条件及阈值联动，并明确关闭扣星后的结果。

- [ ] **Step 4: 添加 LLM 接口管理和油猴连接设置说明**

说明调度策略、超时、AI 二次筛选、接口卡片字段、代理、接口顺序、测活、账号标识、后端地址和共享令牌，并说明凭据脱敏及配置存储位置。

- [ ] **Step 5: 标注生效条件和当前无效字段**

明确 `character`/“回复风格”当前只保存和下发，未被招呼语生成或 AI 筛选消费；明确数据库文件修改需重启，常规配置保存后热加载；说明 LLM 招呼语与 AI 二次筛选是独立开关。

### Task 2: 清理重复说明并验证文档

**Files:**
- Modify: `readme.md`

- [ ] **Step 1: 删除旧的“常用自动化配置”重复短段落**

保留其信息，但统一放入新章节，避免 HR 和防检测配置出现两套可能不一致的解释。

- [ ] **Step 2: 检查 Markdown 和内容完整性**

运行：

```powershell
git diff --check
rg -n "回复风格|每日投递上限|preloadMaxRounds|antiDetectionEnabled|hrActiveLevels|调度策略|账号标识" readme.md
```

预期：`git diff --check` 无输出；搜索结果覆盖新章节中的关键字段。

- [ ] **Step 3: 检查工作区并提交**

运行：

```powershell
git status --short
git diff --stat
git add readme.md
git commit -m "文档：补充前端面板设置参数说明"
```

预期：只提交 `readme.md` 的文档变更，提交信息为中文，工作区无未提交修改。
