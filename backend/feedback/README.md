# 意见反馈

学员提交反馈后，后台授权人员可以查看并更新处理状态、填写内部备注。首版不提供学员反馈历史、附件、后台回复或通知。

## 学员接口

`POST /api/student/v1/feedback/`，需要学员 Bearer 登录，并提供 `Idempotency-Key` 请求头。

```json
{
  "type": "question",
  "content": "这道题的参考答案疑似有误。",
  "contact": "可选联系方式"
}
```

`type` 可选 `feature`（功能问题）、`question`（题目问题）、`payment`（支付问题）、`account`（账号问题）、`other`（其他）。`content` 最长 5000 字；`contact` 可省略，最长 200 字。用户身份从 Bearer 会话取得，不能由请求体指定。

请求成功返回反馈编号、类型、待处理状态和提交时间，不返回反馈正文或联系方式。相同学员使用同一幂等键和相同内容重试时返回首次结果；同一键提交不同内容返回 409。`GET /api/student/v1/feedback/` 及反馈详情接口均未开放。

## 后台

- `feedback.view_feedback`：查看反馈列表和内容。
- `feedback.process_feedback`：修改处理状态和内部备注。
- 状态：待处理、处理中、已处理、已关闭。
- 每次后台处理字段有变更时记录处理人和处理时间。
- 反馈由学员提交，后台不允许新建或物理删除。

## 集成

将 `feedback.apps.FeedbackConfig` 加入 `INSTALLED_APPS`，并将 `feedback.student_urls` 挂载到 `/api/student/v1/`。随后运行 `python manage.py migrate`。该模块使用现有用户模型、学员 Bearer 认证与 `common.IdempotencyRecord`，不新增第三方依赖。

独立测试命令（在 backend 目录）：

```powershell
$env:DJANGO_SECRET_KEY = "<本机随机密钥>"
$env:DJANGO_DEBUG = "true"
python manage.py test feedback.tests --settings=feedback.tests.settings
```
