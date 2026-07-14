# goodjob

一个面向 Boss 直聘的轻量自动投递简历项目，采用“浏览器脚本 + 本地 Python 后端”的组合方式。

当前主线已经收敛为：
- 单简历
- 单方向投递
- 规则筛选岗位
- 固定打招呼
- 收到 Boss 新消息后直接发送简历
- 默认不继续自动聊天

它不是多招聘平台框架，不是复杂多轮自动聊天助手，也不是招聘 SaaS。

## 适合什么方向

当前仓库模板默认更适合这些岗位：
- AI 产品工程师
- AI 应用工程师
- AI Agent / 智能体
- 工作流工程师
- AI Native / Vibe Coding / 大模型应用落地类岗位

当前评分逻辑的核心偏好：
- 标题只做弱信号
- JD 正文里的真实技术要求、工具链、工作流是强信号
- 更关注 Claude Code、Cursor、Codex、Agent、Workflow、Prompt，以及需求到调试部署上线的闭环能力

不适合作为主目标方向的岗位：
- 传统算法训练 / 模型研发
- 传统运维 / SRE / DevOps
- 销售 / 运营
- 纯 C/C++/Go 底层岗

## 现在能做什么

当前主链能力：
- 在 Boss 直聘岗位列表里轮换搜索关键词
- 对岗位做规则打分
- 达到阈值后自动打招呼
- 收到 Boss 新消息后直接发送指定简历
- 连续多轮没有新岗位时自动切换关键词继续挂机
- 遇到超时、详情异常、打招呼异常时自动恢复

## 演示视频

- B 站演示视频：https://www.bilibili.com/video/BV1MyX6BFEp3

## 项目结构

- `main.py`：FastAPI 后端入口
- `core.py`：规则评分主逻辑 + 遗留聊天能力
- `config.py`：配置加载与岗位评分配置
- `web_script.js`：Boss 页面 Tampermonkey 脚本
- `dashboard/`：投递统计面板页面
- `dashboard_data.py`：统计面板数据聚合
- `user_config.example.json`：用户配置模板
- `resume-example.md`：简历模板
- `PROJECT_MEMORY.md`：长期项目背景与关键决策
- `DEV_LOG.md`：开发演进记录

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

如果要运行测试：

```bash
pip install -r requirements-dev.txt
```

### 2. 配置外部 LLM（可选）

```bash
cp .env.example .env
```

然后在 `.env` 中填写自己的服务地址和 API Key：

```env
GOODJOB_LLM_API_BASE=https://your-provider.example/v1
GOODJOB_LLM_API_KEY=your-api-key
```

`.env` 永远不要提交到 Git。没有外部 LLM 时，项目仍可使用规则评分和固定打招呼主链。

### 3. 准备用户配置

```bash
cp user_config.example.json user_config.json
```

首次最少只需要改这些字段：
- `introduce`：固定打招呼语
- `tags`：搜索关键词列表
- `frontend.resumeIndex`：发第几份简历，从 0 开始
- `frontend.thread`：投递阈值

### 4. （可选）准备简历文件

```bash
cp resume-example.md resume.md
```

说明：
- 当前自动投递主链并不依赖 `resume.md` 做岗位打分
- 它主要用于你自己管理简历内容，或保留给遗留接口扩展使用

### 5. 启动后端

```bash
python main.py
```

Windows 下也可以直接双击：

```text
start_backend.bat
```

启动后可打开投递统计面板：

```text
http://127.0.0.1:47999/dashboard
```

面板会读取本地 `job_actions.jsonl` 与 `delivery_state.db`，展示投递公司、岗位、薪资、地区、日期、趋势图、薪资分布、岗位分布和地区地图，并支持筛选与 CSV 导出。历史日志没有地区字段时会显示“待补充”，更新后的 `web_script.js` 会为新投递自动采集工作地点。

统计面板同时提供：
- 本机配置管理：编辑 `user_config.json` 常用参数与高级评分规则
- 简历管理：选择、新建和编辑项目内的 Markdown/TXT 简历
- 提示词管理：通过 `prompt_overrides.json` 安全覆盖固定提示词，不直接改 Python 源码
- 实时监控：展示脚本版本、在线实例、当前阶段、计数器和实时日志
- 高级统计：真实中国省级地图、行业 TOP 10，以及城市、经验、学历、薪资上下限和关键词筛选

管理接口只允许从 `127.0.0.1` / `localhost` 访问，LLM API 地址和 Key 在网页中只显示 `.env` 的配置状态，不会回传原值。新版脚本会每 8 秒发送一次心跳；更新 Tampermonkey 后，管理台才能显示真实在线状态和新增职位字段。

### 6. 部署浏览器脚本

把 `web_script.js` 内容粘贴到 Tampermonkey 中，然后打开 Boss 直聘页面即可。

## 最小使用路径

1. 复制 `.env.example` 为 `.env`，按需配置外部 LLM
2. 复制 `user_config.example.json` 为 `user_config.json`
3. 修改：
   - `introduce`
   - `tags`
   - `frontend.resumeIndex`
   - `frontend.thread`
4. 启动后端 `python main.py`
5. 浏览器装入 `web_script.js`
6. 打开 Boss 直聘页面测试

## 多浏览器、多账号投递

所有浏览器必须连接同一个 Python 后端。后端使用 `delivery_state.db` 做统一协调：

- 去重粒度是公司，不是“公司 + 职位”。同一公司只允许一个账号领取一次。
- 领取、公司去重和账号每日额度预占在一个 SQLite 事务内完成，可安全并发。
- 每个浏览器配置文件会自动生成独立的浏览器实例 ID。
- 在油猴菜单中点击“设置 goodJobs 账号标识”，为每个 Boss 账号设置稳定名称。
- 同一 Boss 账号如果运行在多个浏览器配置文件中，账号标识必须填写一致。
- 浏览器崩溃或发送结果未知时，公司占位不会自动释放，以避免误判后重复投递。

`backend.daily_greet_limit` 是每个账号每天的上限。需要多账号并行时，分别打开不同浏览器配置文件并登录不同账号，然后启动脚本即可。

## 配置说明

当前推荐把普通用户配置集中维护在 `user_config.json`；外部 LLM API 地址和 Key 只放在 `.env`。

普通用户主要改：
- `introduce`
- `tags`
- `frontend.resumeIndex`
- `frontend.thread`

更细的岗位评分规则默认由项目维护者在 `scoring` 中调整，不要求普通用户自己从零设计。

### 顶层字段
- `resume_name`
- `think_model`
- `chat_model`
- `introduce`
- `character`
- `tags`

### `frontend`
浏览器端运行参数，例如：
- `resumeIndex`
- `thread`
- `manualFilterWaitMs`
- `roundRestartDelayMs`
- `maxEmptyRounds`
- `detailTimeout`
- `greetTimeout`
- `preloadScrollPixels`
- `preloadScrollWaitMs`

### `backend`
后端运行参数，例如：
- `job_score_delay_base_ms`
- `job_score_delay_jitter_ms`

### `scoring`
岗位评分规则主要分成：
- 标题强负向词
- 标题弱负向扣分词
- 标题强匹配词
- 标题中匹配词
- 正文强正向词
- 正文辅助正向词
- 正文负向扣分词

当前推荐理解方式：
- 标题只做快速筛选
- JD 正文里的真实技术要求才是主要判断依据

前端启动时会优先请求后端 `/client-config`，统一读取：
- `introduce`
- `tags`
- `frontend`

## 关于大模型依赖

外部 LLM 请求由后端统一调度，多浏览器不会直接同时冲击上游服务。默认策略：

- 最多并发 1 个请求，请求启动间隔至少 0.8 秒。
- 对 `429`、`500`、`502`、`503`、`504` 等临时错误重试 2 次并指数退避。
- 连续失败 3 次后熔断 60 秒，期间立即回退到固定 `introduce`。
- 相同岗位的成功结果缓存 30 分钟，并合并同时到达的相同请求。
- 默认只打印一行降级日志；只有开启 `llm.verbose_errors` 才输出完整异常栈。

除 API 地址和 Key 外，这些参数都可以在管理台的“LLM 服务”区域调整。API 地址和 Key 必须修改本地 `.env`。代理服务不稳定时，建议保持 `max_concurrent_requests=1`，不要通过增加重试次数硬顶上游。

当前主链在未安装 `ollama` 的情况下也能运行，覆盖这些能力：
- 固定 `tags`
- 固定 `introduce`
- 规则岗位评分
- 收到新消息后直接发简历

如果你还要继续启用这些遗留接口：
- `/reply`
- `/is-need-resume`
- `/is-need-works`

再额外安装 `ollama` 即可。

也就是说：
- `ollama` 现在属于遗留能力
- 不是主链运行前提

## 仓库说明

- `user_config.json`、`resume.md`、日志文件等本地文件默认不进入仓库
- `user_config.example.json` 是公开模板，不建议直接提交真实配置
- `.env.example` 是公开的环境变量模板，真实 `.env` 只保存在本机
- 简历、浏览器 Cookie、投递数据库、JSONL 日志和控制状态可能包含个人或第三方信息，发布前必须确认没有被加入暂存区
- 使用浏览器自动化前请遵守 Boss 直聘及当地法律、平台条款和频率限制；项目不代表平台立场，也不保证平台接口长期稳定
- 当前公开仓库优先服务中文用户，因此默认中文说明

如果从旧版 `user_config.json` 迁移，可以运行：

```bash
python scripts/migrate_legacy_secrets.py
```

脚本只会把 `llm.api_base` 和 `llm.api_key` 移到本地 `.env`，终端不会打印真实值。

## 测试与发布检查

```bash
python -m compileall .
python -m unittest discover -v
git add -n .
git status --short
git check-ignore .env user_config.json resume.md delivery_state.db job_actions.jsonl
```

正式提交前应确认 `git status` 和暂存区中没有 `.env`、真实简历、用户配置、数据库、日志或浏览器数据。

## 历史说明

这个项目在个人使用阶段曾演化出一版“双简历 / 双方向自动路由”的复杂版本。

那版代码已经单独归档到分支：
- `archive/double-routing-chaos`

该分支仅供历史参考，不推荐新用户直接从那里开始。

## 后续方向

当前更合理的继续方向是：
- 保持用户配置外置化
- 保持岗位规则可调
- 继续优先保证 Boss 自动化链路稳定
- 不把主线重新做回复杂双路由或重度模型依赖版本
- 继续让评分更贴近 JD 正文真实技术要求，而不是岗位名字字面词
