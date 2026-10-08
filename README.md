# 科管系统 MVP

面向科研院所的科研项目全生命周期管理系统，覆盖项目申报、立项审批、进度跟踪、经费审核、成果登记和结项验收。

> **评审重点：** [设计决策说明](./docs/design-decisions.md) 集中阐述了为什么选择当前功能、为什么主动舍弃其他功能，以及状态机、权限、审计和数据一致性的核心设计思路。

## 功能概览

- 三类角色：科研人员、科研管理员、财务人员。
- 项目状态闭环：草稿 → 立项审批 → 执行 → 验收 → 结项。
- 阶段进展、预算执行、成果和验收记录集中展示。
- 后端权限校验、CSRF 防护、签名 Session Cookie 和关键操作审计。
- SQLite 数据持久化，首次启动自动建立数据库和演示数据。
- 响应式服务端页面，无需单独构建前端。

## 技术栈

- Python 3.11+
- FastAPI、Jinja2
- SQLAlchemy 2.0、SQLite
- PBKDF2 密码哈希和 HMAC 签名会话
- Pytest、HTTPX

## 快速启动

### 方式一：使用 pip

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000>。

### 方式二：使用 uv

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

首次启动会自动在 `data/keguan.db` 建立数据库并写入演示数据。也可以手动执行：

```bash
python -m scripts.seed
```

种子脚本是幂等的；数据库已有用户时不会重复写入。

## 演示账号

| 角色 | 用户名 | 密码 |
|---|---|---|
| 科研人员 | `researcher` | `Demo123!` |
| 科研管理员 | `admin` | `Demo123!` |
| 财务人员 | `finance` | `Demo123!` |

以上账号仅供本地演示，不可用于生产环境。

## 推荐演示流程

1. 使用 `researcher` 创建项目并提交立项。
2. 使用 `admin` 在审批中心批准立项。
3. 使用 `researcher` 填报进度、支出和成果。
4. 使用 `finance` 审核支出。
5. 使用 `researcher` 提交验收。
6. 使用 `admin` 通过验收，检查项目进入“已结项”只读状态。

系统也预置了草稿、待审批和执行中项目，便于直接查看不同状态。

## 测试

```bash
pytest
```

测试使用独立的 `data/test_keguan.db`，覆盖：

- 密码、会话和 CSRF 安全逻辑。
- 角色权限边界。
- 从创建项目到结项的完整业务流程。
- 超预算支出拦截。

可选静态检查：

```bash
ruff check .
python -m compileall -q app scripts
```

## 配置

复制 `.env.example` 中的变量到运行环境。当前版本直接读取系统环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `APP_NAME` | `科管系统` | 页面和 API 名称 |
| `DATABASE_URL` | `sqlite:///./data/keguan.db` | SQLAlchemy 数据库地址 |
| `SECRET_KEY` | 本地开发默认值 | 会话和 CSRF 签名密钥，生产必须修改 |
| `SESSION_MAX_AGE` | `28800` | 登录有效时间（秒） |
| `DEBUG` | `false` | 调试模式 |

## 项目结构

```text
app/
├── main.py          # 应用入口与异常处理
├── web.py           # 页面路由与请求适配
├── services.py      # 权限、状态机和业务事务
├── models.py        # SQLAlchemy 数据模型
├── security.py      # 密码、会话与 CSRF
├── seed.py          # 演示数据
├── templates/       # Jinja2 页面
└── static/          # 页面样式
scripts/seed.py      # 手动种子命令
tests/               # 自动化测试
docs/                # 设计说明
```

功能范围、核心取舍和后续演进路线见 [docs/design-decisions.md](./docs/design-decisions.md)。

## 当前限制

- SQLite 适合单机演示，不适合大量并发写入；生产环境建议迁移 PostgreSQL。
- 当前不提供真实附件上传、外部财务系统、消息通知和动态审批流。
- 数据结构在启动时通过 SQLAlchemy 自动建立；生产部署应接入 Alembic 迁移。
- Session Cookie 在本地 HTTP 下未启用 `Secure`；HTTPS 部署必须启用。
