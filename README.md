# SEA News Digest

每天北京时间 08:00，从 Google News RSS 检索东南亚重点国家新闻，抓取文章正文，生成 12～18 字简体中文摘要，并按国家汇总推送到企业微信群。

## 必需的 GitHub Secret

- `WECOM_WEBHOOK_URL`
- `OPENROUTER_API_KEY`

## 免费摘要模型

默认优先使用：

- `qwen/qwen3.8-27b:free`
- `google/gemma-4-31b-it:free`
- `nvidia/nemotron-3-super-120b-a12b:free`
- `openrouter/free`

如果所有模型都不可用，则退回标题摘要。

## 手动运行

进入 GitHub Actions 页面，选择 `SEA News Digest`，点击 `Run workflow`。

## 可选环境变量

- `MAX_ITEMS`：最多候选新闻数，默认 30
- `MAX_PER_SOURCE`：每个国家最多候选数，默认 1
- `LOOKBACK_HOURS`：新闻时间范围，默认 24 小时
- `DIGEST_ITEMS`：最终简报国家数，默认 8
- `SUMMARY_MODELS`：逗号分隔的免费模型列表