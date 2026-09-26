# SEA News Digest

每天北京时间 08:00，从 Google News RSS 检索东南亚重点国家过去 24 小时的热点新闻，并将完整标题翻译为简体中文后推送到企业微信群。

当前输出规则：

- 每个国家最多 2 条
- 总计最多 20 条
- 不输出链接
- 标题完整中文翻译，不截断
- 自动过滤 Facebook、X、YouTube 等社交媒体内容
- 消息过长时自动拆分为多个连续消息

## 必需的 GitHub Secret

- `WECOM_WEBHOOK_URL`
- `OPENROUTER_API_KEY`

## 标题翻译

标题使用免费 MyMemory 翻译接口逐条翻译为简体中文。

## 手动运行

进入 GitHub Actions 页面，选择 `SEA News Digest`，点击 `Run workflow`。

## 可选环境变量

- `MAX_ITEMS`：最多候选新闻数，默认 30
- `MAX_PER_SOURCE`：每个国家最多候选数，默认 2
- `LOOKBACK_HOURS`：新闻时间范围，默认 24 小时
- `DIGEST_ITEMS`：最终简报条数，默认 20
- `ENABLE_AI_SUMMARY`：是否启用 AI 内容摘要，默认关闭