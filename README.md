# 轻问卷：基于 MongoDB 的在线问卷系统（阶段一）

阶段一实现了一个可直接运行的轻量问卷系统，功能覆盖初始需求中的账号、问卷管理、单选、多选、文本/数字填空、条件跳转、填写校验、统计和测试要求。界面使用 Flask 服务端模板，不依赖前端构建工具。

## 已实现功能

- 用户注册、登录、退出；密码使用 Werkzeug 加盐哈希保存。
- 用户只能管理自己创建的问卷；公开链接可供其他登录用户填写。
- 创建问卷、设置说明、匿名策略、重复填写策略和截止时间。
- 发布问卷、复制公开链接、关闭问卷。
- 单选题、多选题、文本填空题、数字填空题。
- 必答、选项数量限制、文本长度限制、数字范围与整数限制。
- 可配置跳转：单选选项、多选包含某选项、多选数量、数字区间、跳转到结束。
- 逐题保存答案；未提交答卷可继续填写；支持多人同时填写。
- 整卷统计与单题统计：选项分布、回答人数、文本答案、数字平均值/最小值/最大值。

## 目录

```text
阶段一代码/
├─ app.py                 # 兼容入口，保持 python app.py 启动方式
├─ wsgi.py                # WSGI 入口
├─ backend/               # 后端代码
│  ├─ app.py              # Flask 应用、路由、账号与填写流程
│  ├─ survey_logic.py     # 题目、校验、跳转与统计逻辑
│  ├─ schema.py           # MongoDB JSON Schema
│  ├─ db.py               # 连接、校验器与索引初始化
│  └─ config.py           # 环境配置
├─ frontend/              # 前端页面与静态资源
│  ├─ templates/          # Jinja 页面
│  └─ static/style.css    # 页面样式
├─ tests/                 # unittest 自动化测试
├─ docs/                  # 设计文档、测试说明与项目报告
├─ requirements.txt
└─ docker-compose.yml     # 可选的 MongoDB 容器
```

## 运行环境

- Python 3.10+（本机验证版本为 3.12.8）
- MongoDB 6.0+（本机验证版本为 8.3）
- 依赖：Flask 3.1.3、PyMongo 4.18.1

## 启动

1. 启动 MongoDB。

   已有本机 MongoDB：

   ```powershell
   mongod --dbpath C:\path\to\mongo-data
   ```

   或使用 Docker：

   ```powershell
   docker compose up -d mongo
   ```

2. 安装依赖并启动应用。

   ```powershell
   cd 阶段一代码
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   $env:MONGO_URI = "mongodb://127.0.0.1:27017"
   $env:MONGO_DB_NAME = "survey_phase1"
   python app.py
   ```

3. 打开 `http://127.0.0.1:5000`，先注册账号。

首次启动会自动创建四个集合、安装 JSON Schema 校验器并建立索引，不需要手工执行建表脚本。

## 配置

可通过环境变量配置，示例见 `.env.example`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | 开发用默认值 | Flask 会话签名密钥，部署时务必修改 |
| `MONGO_URI` | `mongodb://127.0.0.1:27017` | MongoDB 连接串 |
| `MONGO_DB_NAME` | `survey_phase1` | 数据库名 |
| `SURVEY_BASE_URL` | 空 | 复制问卷链接时使用的外部地址 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 截止时间与时区显示 |

## 测试

测试使用独立的临时数据库，每个用例结束后自动删除。先启动一个测试用 MongoDB，再执行：

```powershell
cd 阶段一代码
$env:TEST_MONGO_URI = "mongodb://127.0.0.1:27018"
python -m unittest discover -s tests -v
```

本机验证结果：`Ran 10 tests ... OK`。

另有真实 HTTP + CSRF 冒烟脚本，先启动应用再执行：

```powershell
pip install -r requirements-dev.txt
$env:LIVE_BASE_URL = "http://127.0.0.1:5000"
python tests/live_smoke.py
```

## 关键规则

### 跳转

- 每道题的 `jump_rules` 是数组，按顺序匹配，第一条命中规则生效。
- 没有规则命中时，进入题目顺序中的下一题。
- 规则目标只能是当前题目之后的题目，或 `target = null` 表示结束问卷，避免循环跳转。
- 被跳过的题目不进入 `path`，因此不会被当作“必答题未回答”。

### 匿名

填写者仍需登录，这是初始需求中“填写问卷的人需要先登录”的要求。选择匿名时：

- 答卷标记 `anonymous = true`；
- 统计页面不展示任何填写者身份；
- 数据库保留内部 `user_id`，仅用于访问控制、恢复未提交答卷和执行“每人限一份”限制。

### 编辑限制

问卷发布后，题目、选项和跳转规则锁定，避免已提交答卷与题目结构不一致；截止时间仍可在关闭前调整。草稿阶段可以编辑题目。

## 更多说明

- [系统说明](docs/系统说明.md)
- [数据库设计](docs/数据库设计.md)
- [API 说明](docs/API说明.md)
- [关键逻辑说明](docs/关键逻辑说明.md)
- [测试用例说明](docs/测试说明.md)
