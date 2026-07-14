# goodjob

原项目：https://github.com/czc6666/czc-good-job



## 项目功能

- [x] 自动投简历

- [x]  多账号投简历

- [x] 前端展示面板

- [x] 固定打招呼

- [x] AI智能打招呼

- [x] 负向关键词筛选岗位

- [x] 规则筛选岗位

- [x] 默认不继续自动聊天

------

**主链能力：**

- 在 Boss 直聘岗位列表里轮换搜索关键词
- 对岗位做规则打分
- 达到阈值后自动打招呼
- 收到 Boss 新消息后直接发送指定简历
- 连续多轮没有新岗位时自动切换关键词继续挂机
- 遇到超时、详情异常、打招呼异常时自动恢复

## 项目介绍

一个面向 Boss 直聘的轻量自动投递简历项目，采用“浏览器脚本 + 本地 Python 后端”的组合方式。

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

  

**配置文件**

- `user_config.json`、`resume.md`、日志文件等本地文件默认不进入仓库
- `user_config.example.json` 是公开模板，不建议直接提交真实配置
- `.env.example` 是公开的环境变量模板，真实 `.env` 只保存在本机

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

启动后可打开投递统计面板：

```text
http://127.0.0.1:47999/dashboard
```

统计面板同时提供：
- 本机配置管理：编辑 `user_config.json` 常用参数与高级评分规则
- 简历管理：选择、新建和编辑项目内的 Markdown/TXT 简历
- 提示词管理：通过 `prompt_overrides.json` 安全覆盖固定提示词，不直接改 Python 源码
- 实时监控：展示脚本版本、在线实例、当前阶段、计数器和实时日志
- 高级统计：真实中国省级地图、行业 TOP 10，以及城市、经验、学历、薪资上下限和关键词筛选

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
