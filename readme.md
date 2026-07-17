# boos-goodjob

原项目：https://github.com/czc6666/czc-good-job



## 项目功能

- [x] 自动投简历
- [x] 多账号投简历
- [x] 统计面板
- [x] 配置面板
- [x] 固定打招呼
- [x] AI智能打招呼
- [x] 关键词筛选岗位
- [x] AI筛选岗位
- [x] 大模型接口管理器
- [x] 重复岗位跳过
- [x] HR 活跃度筛选
- [x] 随机化投递节奏与随机省略招呼语


## TODO
- [ ] AI多简历系统
- [ ] 自动发送附件简历
- [ ] AI聊天系统
- [ ] 防检测系统

------

**主链能力：**

- 在 Boss 直聘岗位列表里轮换搜索关键词
- 对岗位做规则打分
- 达到阈值后自动打招呼
- 收到 Boss 新消息后直接发送指定简历
- 连续多轮没有新岗位时自动切换关键词继续挂机
- 遇到超时、详情异常、打招呼异常时自动恢复

- Boos每个账号每日投递上线: 150
- 推荐接入LLM,智能根据简历匹配岗位与打招呼,提高HR回复率


## 项目介绍

一个面向 Boss 直聘的轻量自动投递简历项目，采用“浏览器脚本 + 本地 Python 后端”的组合方式。

![](./img/01.png)

## 项目结构

- `main.py`：可执行入口，负责组装并启动 FastAPI 服务；直接运行时会在服务就绪后使用系统默认浏览器打开统计面板
- `app/`：后端业务代码包
  - `app/paths.py`：集中管理项目根目录与所有本地数据文件路径
  - `app/config.py`：运行配置、旧配置迁移与热加载
  - `app/state.py`：数据库、日志路径、进程锁和启动迁移等共享资源
  - `app/scoring.py`：岗位文本解析与纯规则扣星评分
  - `app/runtime.py`：浏览器工作器运行态、控制策略与事件流
  - `app/routes/`：按“岗位投递”和“管理运行”归类的 HTTP 接口
  - `app/llm/`：`gateway.py` 单接口请求、`manager.py` 多接口调度、`env_store.py` `.env` 持久化、`tasks.py` 招呼语与筛选、`prompts.py` 提示词
  - `app/storage/`：`io.py` 原子写入与 JSONL、`delivery_store.py`/`resume_store.py`/`admin_store.py` 投递、简历与管理配置存储、`dashboard_data.py` 面板数据聚合
- `dashboard/`：统计管理面板前端资源（HTML/CSS/JS 与地图数据）
- `scripts/`：地图等离线数据构建脚本
- `web_script.js`：Boss 页面 Tampermonkey 单文件脚本
- `user_config.example.json`：可直接复制使用的当前格式配置模板
- `resumes/`：网页管理并提供给 LLM 使用的真实简历目录
- `resume-example.md`：简历模板，仅用于创建真实简历，不在网页管理页展示

**配置文件**

- `user_config.json`、`resumes/` 中的真实简历、日志文件等本地文件默认不进入仓库
- `user_config.example.json` 是公开模板，不建议直接提交真实配置
- `.env.example` 是公开的环境变量模板，真实 `.env` 只保存在本机

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置外部 LLM（建议）

```bash
cp .env.example .env
```

推荐启动服务后，在网页面板的「系统管理 → 接口管理」中配置；也可以直接编辑 `.env`：

```env
GOODJOB_LLM_1_NAME=主接口
GOODJOB_LLM_1_API_BASE=https://your-provider.example/v1
GOODJOB_LLM_1_API_KEY=your-api-key
GOODJOB_LLM_1_MODEL=gpt-4.1-mini
GOODJOB_LLM_1_PROXY_URL=http://127.0.0.1:7890
GOODJOB_LLM_1_PROXY_ENABLED=true
GOODJOB_LLM_1_ENABLED=true
```

代理按接口独立配置，当前支持 `http://` 和 `https://`。关闭代理开关后，该接口强制直连，不读取系统代理环境变量。代理地址含用户名和密码时，网页只显示脱敏值。

### 3. 准备用户配置

```bash
cp user_config.example.json user_config.json
```

首次最少只需要改这些字段：
- `introduce`：固定打招呼语
- `tags`：搜索关键词列表
- `frontend.resumeIndex`：BOSS 页面发送在线简历时使用的序号，从 0 开始；与 LLM 读取的本地简历无关
- `frontend.thread`：投递阈值

### 4. （可选）准备简历文件

```bash
cp resume-example.md resumes/resume.md
```

说明：
- `resumes/` 存放简历
- 网页设置的当前简历会作为 LLM 生成定制招呼语和执行 AI 岗位筛选时的默认简历
- `resume-example.md` 简历模板

### 5. 启动后端

```bash
python main.py
```

直接运行 `python main.py` 后，程序会等待服务就绪，并使用系统默认浏览器自动打开投递统计面板。若未自动打开，可手动访问：

```text
http://127.0.0.1:47999/dashboard
```

统计面板同时提供：
- 配置管理：编辑 `user_config.json` 常用参数与高级评分规则
- 简历管理：选择、新建和编辑 `resumes/` 中的 Markdown/TXT 简历，并设置 LLM 使用的当前简历
- 提示词管理：通过 `prompt_overrides.json` 安全覆盖固定提示词，不直接改 Python 源码
- 实时控制：按全部实例或单个浏览器执行开启、暂停和结束，并展示连接、执行、同步状态
- 实时监控：展示脚本版本、在线实例、当前阶段、计数器和集中实时日志
- 高级统计：真实中国省级地图、行业 TOP 10，以及城市、经验、学历、薪资上下限和关键词筛选

常用自动化配置：

- `frontend.hrActiveFilterEnabled`：开启 HR 活跃度过滤；无法识别活跃状态时默认放行
- `frontend.hrActiveMinLevel`：最低活跃档位，可选 `online`、`just_now`、`today`、`within_3_days`、`this_week`、`this_month`
- `frontend.antiDetectionEnabled`：开启岗位顺序、随机跳过、随机等待和随机省略招呼语等行为节奏随机化

### 6. 部署浏览器脚本

把 `web_script.js` 内容粘贴到 Tampermonkey 中，然后打开 Boss 直聘页面。油猴脚本只显示左下角只读状态窗，不再提供本地启停或日志面板。

在 Tampermonkey 菜单中完成：

- `设置 goodJobs 账号标识`：同一 Boss 账号在多个浏览器运行时填写相同标识
- `设置 goodJobs 后端连接`：填写 `http(s)://IP或域名:端口`；局域网可不填令牌，公网连接填写共享令牌

首次接入实例默认是“已结束”，不会自动投递。打开后端 Dashboard，在“脚本实时监控”中点击全局或实例的“开启”后才会运行；“结束”只销毁当前自动化执行链，控制心跳仍然保留，因此可以再次从网页开启。

### 7. 远程访问安全

后端对环回、RFC1918、IPv6 ULA 和链路本地地址免令牌。公网访问必须在 `.env` 配置 32–256 位共享令牌：

```env
GOODJOB_SHARED_TOKEN=replace-with-a-random-token-at-least-32-characters
```

公网入口必须使用 HTTPS 反向代理。只有自有代理地址可以加入 `GOODJOB_TRUSTED_PROXIES`，禁止使用 `*`：

```env
GOODJOB_TRUSTED_PROXIES=127.0.0.1/32
```

可信代理必须在每个请求中覆盖 `X-Forwarded-For` 和 `X-Forwarded-Proto`；任一头缺失、重复或非法时后端都会拒绝请求。未加入可信列表的本机或局域网 peer 不得携带这两个转发头，以免错误获得局域网免令牌权限。

Dashboard 的令牌只保存在当前标签页 `sessionStorage`；油猴令牌只保存在 Tampermonkey GM storage，不会写入 `user_config.json`、URL 或日志。

## 最小使用路径

1. 复制 `.env.example` 为 `.env`，按需配置外部 LLM
2. 复制 `user_config.example.json` 为 `user_config.json`
3. 复制 `resume-example.md` 为 `resumes/resume.md`
4. 修改：
   - `introduce`
   - `tags`
   - `frontend.resumeIndex`（BOSS 在线简历序号）
   - `frontend.thread`
5. 启动后端 `python main.py`（服务就绪后会自动打开统计面板）
6. 浏览器装入 `web_script.js`
7. 打开 Boss 直聘页面，在 Tampermonkey 菜单设置账号标识和后端连接
8. 在 Dashboard 的“脚本实时监控”中点击“开启”
