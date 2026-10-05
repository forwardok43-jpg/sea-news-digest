# US & Major Economies News Digest Addon

This directory is an additive package for:

```text
forwardok43-jpg/sea-news-digest
```

It adds a separate daily US news workflow and does not modify the existing ASEAN workflow or scripts.

## Included

```text
.github/workflows/us-news.yml
.github/workflows/test-us-content-fetch.yml
us-news/scripts/fetch_us_digest.py
us-news/scripts/test_us_content_fetch.py
us-news/requirements.txt
```

## Coverage

- US domestic politics, economy, technology, security, society
- US-China relations
- US relations with the EU, Japan, South Korea, India, the UK, Canada, Mexico, Australia and Brazil
- US trade, investment and supply-chain relations with major economies

## Schedule

```text
UTC 00:00 = Beijing time 08:00
```

The US workflow uses the existing repository secrets:

```text
WECOM_WEBHOOK_URL
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_API_TOKEN
OPENROUTER_API_KEY
```

No new repository secrets are required.

## Manual test

```text
dry_run = true
enable_ai_summary = false
digest_items = 12
```

Expected artifacts:

```text
今日美国与主要经济体新闻总览_YYYY-MM-DD.docx
美国与主要经济体新闻摘要汇编_YYYY-MM-DD.docx
```
