# API 与页面路由说明（阶段一）

## 1. 使用说明

系统是 Flask 服务端渲染应用。接口返回 HTML 页面或 `302` 跳转，不提供 JSON API。

- 基础地址：`http://127.0.0.1:5000`
- 问卷填写者必须先登录
- 写操作使用 `POST`
- 所有写操作都携带 CSRF Token `_csrf`
- 资源 ID 使用 MongoDB ObjectId
- 公开问卷使用随机 `public_id`，不直接暴露问卷 ObjectId

通用表单流程：

```text
GET 页面
  ↓
页面中包含 _csrf Token
  ↓
POST 表单
  ↓
校验、写 MongoDB
  ↓
返回 HTML 或 302 重定向
```

## 2. 认证与权限

| 标记 | 含义 |
| --- | --- |
| 公开 | 不要求登录 |
| 登录 | 会话中必须存在有效用户 |
| 创建者 | 当前用户 `_id` 必须等于问卷 `owner_id` |
| 答卷所属用户 | 当前用户 `_id` 必须等于答卷 `user_id` |

用户登录状态保存在 Flask 会话 Cookie 中。服务端只保存用户 ID，每个请求重新从 `users` 集合加载用户。

## 3. 账号

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 公开 | 首页；已登录跳转工作台 |
| GET/POST | `/register` | 公开 | 注册；成功后自动登录 |
| GET/POST | `/login` | 公开 | 登录；支持 `next` 回跳 |
| POST | `/logout` | 登录 | 退出 |
| GET | `/dashboard` | 登录 | 当前用户创建的问卷列表 |

## 4. 问卷管理

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/surveys/new` | 登录 | 新建问卷页面 |
| POST | `/surveys` | 登录 | 创建问卷 |
| GET | `/surveys/<survey_id>` | 创建者 | 问卷管理页 |
| POST | `/surveys/<survey_id>/settings` | 创建者、草稿 | 修改标题、说明、匿名/重复策略 |
| POST | `/surveys/<survey_id>/deadline` | 创建者、未关闭 | 设置或清空截止时间 |
| POST | `/surveys/<survey_id>/publish` | 创建者、草稿 | 发布问卷 |
| POST | `/surveys/<survey_id>/close` | 创建者、已发布 | 关闭问卷 |

创建问卷表单字段：`title`、`description`、`deadline`、`allow_anonymous`、`allow_multiple`。

### 4.1 创建问卷示例

请求：

```http
POST /surveys
Content-Type: application/x-www-form-urlencoded

_csrf=<token>&title=校园调查&description=匿名问卷&deadline=2026-12-31T23:59&allow_anonymous=on&allow_multiple=on
```

成功结果：

```http
HTTP/1.1 302 Found
Location: /surveys/<survey_id>
```

创建后的问卷初始状态为 `draft`。

### 4.2 截止时间

截止时间使用 HTML `datetime-local` 格式：

```text
YYYY-MM-DDTHH:MM
```

后端按 `APP_TIMEZONE` 解释并转换为 UTC 保存。留空表示不设置截止时间。

### 4.3 问卷状态

```text
draft      草稿，不可填写
published  已发布，未截止时可填写
closed     已关闭，不可填写
```

## 5. 题目与跳转

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/surveys/<survey_id>/questions/new` | 创建者、草稿 | 添加题目页面 |
| POST | `/surveys/<survey_id>/questions` | 创建者、草稿 | 添加题目（兼容 `/questions/new`） |
| GET/POST | `/surveys/<survey_id>/questions/<question_id>/edit` | 创建者、草稿 | 编辑题目 |
| POST | `/surveys/<survey_id>/questions/<question_id>/rules` | 创建者、草稿 | 添加跳转规则 |
| POST | `/surveys/<survey_id>/questions/<question_id>/rules/<rule_id>/delete` | 创建者、草稿 | 删除跳转规则 |

题目表单字段：

- 通用：`type`、`title`、`required`
- 单选/多选：`options`，每行一个选项
- 多选：`min_selections`、`max_selections`、`exact_selections`
- 文本：`min_length`、`max_length`
- 数字：`min_value`、`max_value`、`integer_only`

跳转表单字段：

- 单选：`kind=option_equals`、`value=<选项 key>`、`target=<题目 ID 或 end>`
- 多选包含：`kind=option_contains`、`value=<选项 key>`、`target=...`
- 多选数量：`kind=selection_count`、`operator=eq|gte|lte`、`count=<整数>`、`target=...`
- 数字区间：`kind=number_range`、`min`、`max`、`target=...`

`target=end` 表示结束问卷。

### 5.1 添加题目示例

单选题：

```http
POST /surveys/<survey_id>/questions

_csrf=<token>&type=single&title=你的性别&required=on&options=男%0A女%0A其他
```

多选题：

```http
POST /surveys/<survey_id>/questions

_csrf=<token>&type=multiple&title=喜欢的水果&required=on&options=苹果%0A香蕉%0A西瓜&min_selections=2&max_selections=3
```

数字题：

```http
POST /surveys/<survey_id>/questions

_csrf=<token>&type=number&title=年龄&required=on&min_value=0&max_value=120&integer_only=on
```

### 5.2 跳转规则示例

单选 A 跳转到指定题目：

```http
POST /surveys/<survey_id>/questions/<question_id>/rules

_csrf=<token>&kind=option_equals&value=A&target=<question_id>
```

多选数量至少 2 项时结束问卷：

```http
POST /surveys/<survey_id>/questions/<question_id>/rules

_csrf=<token>&kind=selection_count&operator=gte&count=2&target=end
```

数字在 18~60 之间时跳转：

```http
POST /surveys/<survey_id>/questions/<question_id>/rules

_csrf=<token>&kind=number_range&min=18&max=60&target=<question_id>
```

## 6. 公开问卷与填写

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/s/<public_id>` | 公开 | 问卷介绍页 |
| POST | `/s/<public_id>/start` | 登录 | 创建或恢复一份答卷 |
| GET | `/fill/<response_id>` | 答卷所属用户 | 跳转到当前题目 |
| GET | `/fill/<response_id>/q/<question_id>` | 答卷所属用户 | 显示当前题目 |
| POST | `/fill/<response_id>/answer` | 答卷所属用户 | 校验并保存答案，计算下一题 |
| GET | `/fill/<response_id>/review` | 答卷所属用户 | 提交前检查 |
| POST | `/fill/<response_id>/submit` | 答卷所属用户 | 提交答卷 |
| GET | `/fill/<response_id>/done` | 答卷所属用户 | 提交成功页 |

答题表单字段：`qid`、`answer`（多选可重复）、`action=next|skip`。

### 6.1 开始填写

```http
POST /s/<public_id>/start

_csrf=<token>
```

如果用户已有未提交答卷，接口会返回该答卷的填写地址；否则创建新答卷。

### 6.2 提交答案

单选题：

```http
POST /fill/<response_id>/answer

_csrf=<token>&qid=<question_id>&answer=A&action=next
```

多选题：

```http
POST /fill/<response_id>/answer

_csrf=<token>&qid=<question_id>&answer=A&answer=C&action=next
```

选答题跳过：

```http
POST /fill/<response_id>/answer

_csrf=<token>&qid=<question_id>&action=skip
```

成功保存答案后返回 `302`，`Location` 指向下一题、提交检查页或结束页面。

### 6.3 答卷 ID

`response_id` 是某一次填写对应的 MongoDB ObjectId。答卷只能由创建它的登录用户继续操作。

## 7. 统计

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/surveys/<survey_id>/stats` | 创建者 | 整卷统计 |
| GET | `/surveys/<survey_id>/stats/<question_id>` | 创建者 | 单题统计 |

统计只读取 `status = submitted` 的答卷。单选题和多选题输出每个选项人数及占该题回答人数的百分比；文本题输出全部文本；数字题输出平均值、最小值和最大值。

## 8. 状态与错误约定

- `400`：CSRF 校验失败、表单校验失败；
- `404`：资源不存在或当前用户无权访问；
- `409`：问卷关闭或截止后，答卷页面拒绝继续填写；
- `405`：请求方法不允许；
- `500`：未处理的服务器异常；
- 页面写操作成功后使用 `302` 重定向，避免重复提交。

## 9. 完整用户流程

```text
GET /register
  → POST /register
  → GET /dashboard
  → POST /surveys
  → POST /surveys/<survey_id>/questions（可重复）
  → POST /surveys/<survey_id>/questions/<question_id>/rules（可重复）
  → POST /surveys/<survey_id>/publish
  → GET /s/<public_id>
  → POST /s/<public_id>/start
  → GET/POST /fill/<response_id>...
  → GET /fill/<response_id>/review
  → POST /fill/<response_id>/submit
  → GET /surveys/<survey_id>/stats
```

## 10. 接口设计说明

- 采用服务端表单而不是前后端分离 JSON API，符合课程允许简单前端的要求。
- 所有问卷管理接口都通过 `owner_id` 做数据隔离。
- 答卷操作不信任 URL 中的 `response_id`，必须匹配当前登录用户。
- CSRF 防护覆盖所有 POST 请求。
- 业务错误通过页面提示和 HTTP 状态码反馈。
