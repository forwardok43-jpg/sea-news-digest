# SEA News Digest

每天北京时间 08:00，从 Google News RSS 检索东南亚新闻，整理为 Markdown 简报并通过企业微信群机器人推送。

简报结构：

- 30 秒速览
- 今日重点
- 其他动态
- 今日观察

## 必需的 GitHub Secret

- `WECOM_WEBHOOK_URL`

## 手动运行

进入 GitHub Actions 页面，选择 `SEA News Digest`，点击 `Run workflow`。

## 可选环境变量

- `MAX_ITEMS`：最多推送条数，默认 20
- `MAX_PER_SOURCE`：每个来源最多条数，默认 3
- `LOOKBACK_HOURS`：新闻时间范围，默认 24 小时
- `OVERVIEW_ITEMS`：30 秒速览条数，默认 6
- `FOCUS_ITEMS`：今日重点条数，默认 5