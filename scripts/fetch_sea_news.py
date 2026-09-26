#!/usr/bin/env python3
from __future__ import annotations

import calendar
import html
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests


TZ = ZoneInfo("Asia/Shanghai")
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_ITEMS = max(1, env_int("MAX_ITEMS", 20))
MAX_PER_SOURCE = max(1, env_int("MAX_PER_SOURCE", 3))
LOOKBACK_HOURS = max(1, env_int("LOOKBACK_HOURS", 24))
OVERVIEW_ITEMS = max(3, env_int("OVERVIEW_ITEMS", 6))
FOCUS_ITEMS = max(3, env_int("FOCUS_ITEMS", 5))
USER_AGENT = "sea-news-digest/1.0"

SOURCES = [
    {"name": "🌏 东盟区域", "query": "ASEAN OR Southeast Asia", "lang": "en", "country": "SG"},
    {"name": "🇮🇩 印度尼西亚", "query": "Indonesia OR Jakarta", "lang": "id", "country": "ID"},
    {"name": "🇻🇳 越南", "query": "Việt Nam OR Hà Nội", "lang": "vi", "country": "VN"},
    {"name": "🇹🇭 泰国", "query": "ประเทศไทย OR กรุงเทพ", "lang": "th", "country": "TH"},
    {"name": "🇵🇭 菲律宾", "query": "Philippines OR Manila", "lang": "en", "country": "PH"},
    {"name": "🇲🇾 马来西亚", "query": "Malaysia OR Kuala Lumpur", "lang": "ms", "country": "MY"},
    {"name": "🇸🇬 新加坡", "query": "Singapore", "lang": "en", "country": "SG"},
    {"name": "🇰🇭 柬埔寨", "query": "Cambodia OR Phnom Penh", "lang": "km", "country": "KH"},
    {"name": "🇲🇲 缅甸", "query": "Myanmar OR Yangon", "lang": "my", "country": "MM"},
    {"name": "🇱🇦 老挝", "query": "Laos OR Vientiane", "lang": "lo", "country": "LA"},
    {"name": "🇧🇳 文莱", "query": "Brunei OR Bandar Seri Begawan", "lang": "en", "country": "BN"},
    {"name": "🇹🇱 东帝汶", "query": "Timor-Leste OR Dili", "lang": "pt", "country": "TL"},
]


@dataclass(frozen=True)
class Article:
    region: str
    title: str
    link: str
    published: datetime
    publisher: str


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def clean_title(title: str, publisher: str) -> str:
    title = clean_text(title)
    if publisher:
        suffix = f" - {publisher}"
        if title.casefold().endswith(suffix.casefold()):
            title = title[: -len(suffix)].rstrip()
    return title


def safe_markdown_title(title: str) -> str:
    return re.sub(r"[\[\]*_`|#]", "", clean_text(title))


def truncate_text(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_google_news_url(query: str, lang: str, country: str) -> str:
    ceid = f"{country}:{lang}"
    return (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(query)}"
        f"&hl={lang}&gl={country}&ceid={ceid}"
    )


def get_feed(session: requests.Session, url: str):
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=25)
            response.raise_for_status()
            return feedparser.parse(response.content)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch RSS: {url}") from last_error


def entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return None


def entry_publisher(entry) -> str:
    source = entry.get("source")
    if isinstance(source, dict):
        return clean_text(source.get("title"))
    return clean_text(getattr(source, "title", ""))


def fetch_source(session: requests.Session, source: dict) -> list[Article]:
    url = build_google_news_url(source["query"], source["lang"], source["country"])
    feed = get_feed(session, url)

    if getattr(feed, "bozo", False):
        print(f"[WARN] RSS parse issue: {source['name']}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles: list[Article] = []

    for entry in feed.entries:
        published = entry_datetime(entry)
        if published is None or published < cutoff:
            continue

        publisher = entry_publisher(entry)
        title = clean_title(clean_text(entry.get("title")), publisher)
        link = clean_text(entry.get("link"))
        if not title or not link:
            continue

        articles.append(
            Article(
                region=source["name"],
                title=title,
                link=link,
                published=published,
                publisher=publisher,
            )
        )

    articles.sort(key=lambda item: item.published, reverse=True)
    return articles[:MAX_PER_SOURCE]


def deduplication_key(article: Article) -> str:
    return re.sub(r"[\W_]+", "", article.title.casefold())


def collect_articles(session: requests.Session):
    articles: list[Article] = []
    failures: list[str] = []

    for source in SOURCES:
        try:
            items = fetch_source(session, source)
            print(f"[OK] {source['name']}: {len(items)}")
            articles.extend(items)
        except Exception as exc:
            message = f"{source['name']}: {exc}"
            print(f"[FAIL] {message}")
            failures.append(message)

    articles.sort(key=lambda item: item.published, reverse=True)
    unique_articles: list[Article] = []
    seen: set[str] = set()

    for article in articles:
        key = deduplication_key(article)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_articles.append(article)

    return unique_articles[:MAX_ITEMS], failures


def pick_representative(articles: list[Article], limit: int) -> list[Article]:
    selected: list[Article] = []
    selected_links: set[str] = set()
    regions: set[str] = set()

    for article in articles:
        if article.region in regions:
            continue
        selected.append(article)
        selected_links.add(article.link)
        regions.add(article.region)
        if len(selected) >= limit:
            return selected

    for article in articles:
        if article.link in selected_links:
            continue
        selected.append(article)
        selected_links.add(article.link)
        if len(selected) >= limit:
            break

    return selected


def article_meta(article: Article) -> str:
    local_time = article.published.astimezone(TZ).strftime("%m-%d %H:%M")
    parts = [article.publisher, local_time]
    return " · ".join(part for part in parts if part)


def build_messages(articles: list[Article]) -> list[str]:
    now = datetime.now(TZ)
    overview = pick_representative(articles, min(OVERVIEW_ITEMS, len(articles)))
    focus = pick_representative(articles, min(FOCUS_ITEMS, len(articles)))
    focus_links = {article.link for article in focus}
    remaining = [article for article in articles if article.link not in focus_links]

    overview_lines = [
        f"• {article.region}：{truncate_text(article.title, 78)}"
        for article in overview
    ]

    sections: list[str] = [
        "━━━━━━━━━━━━━━",
        "东南亚新闻简报",
        f"{now:%Y-%m-%d %H:%M} 北京时间",
        f"范围：东盟及区域来源｜时段：过去 {LOOKBACK_HOURS} 小时｜精选 {len(articles)} 条",
        "━━━━━━━━━━━━━━",
        "【30 秒速览】\n\n" + "\n".join(overview_lines),
    ]

    focus_parts = ["【今日重点】"]
    for index, article in enumerate(focus, start=1):
        title = truncate_text(clean_text(article.title), 120)
        focus_parts.append(
            f"{index}. {article.region}\n"
            f"{title}\n"
            f"{article_meta(article)}\n"
            f"{article.link}"
        )
    sections.append("\n\n".join(focus_parts))

    if remaining:
        other_parts = ["【其他动态】"]
        current_region = None
        for article in remaining:
            if article.region != current_region:
                if current_region is not None:
                    other_parts.append("")
                other_parts.append(article.region)
                current_region = article.region

            title = truncate_text(clean_text(article.title), 110)
            other_parts.append(
                f"• {title}\n"
                f"{article_meta(article)}\n"
                f"{article.link}"
            )
        sections.append("\n".join(other_parts))

    region_counts = Counter(article.region for article in articles)
    top_regions = "、".join(region for region, _ in region_counts.most_common(3))
    latest = max(article.published for article in articles).astimezone(TZ)

    sections.append(
        "【今日观察】\n\n"
        f"• 本期共整理 {len(articles)} 条，覆盖 {len(region_counts)} 个来源分类。\n"
        f"• 新闻量相对集中：{top_regions}。\n"
        f"• 最新一条发布时间：{latest:%Y-%m-%d %H:%M}（北京时间）。"
    )
    sections.append(
        "━━━━━━━━━━━━━━\n\n"
        "说明：本简报由新闻源自动汇总，标题和链接以原文为准。"
    )

    messages = split_text("\n\n".join(sections), 1800)
    if len(messages) > 1:
        messages = [messages[0]] + [
            f"东南亚新闻简报（续）\n\n{message}" for message in messages[1:]
        ]
    return messages


def split_text(text: str, limit_bytes: int) -> list[str]:
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    messages: list[str] = []
    current = ""

    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if current and len(candidate.encode("utf-8")) > limit_bytes:
            messages.append(current)
            current = block
        else:
            current = candidate

    if current:
        messages.append(current)
    return messages


def send_wecom_text(session: requests.Session, content: str) -> None:
    payload = {"msgtype": "text", "text": {"content": content}}
    last_error = None

    for attempt in range(3):
        try:
            response = session.post(WECOM_WEBHOOK_URL, json=payload, timeout=25)
            response.raise_for_status()
            result = response.json()
            if result.get("errcode") != 0:
                raise RuntimeError(f"WeCom API error: {result}")
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))

    raise RuntimeError("Failed to send WeCom message") from last_error


def main() -> None:
    if not DRY_RUN and not WECOM_WEBHOOK_URL:
        raise SystemExit("Missing WECOM_WEBHOOK_URL")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    articles, failures = collect_articles(session)

    if not articles:
        if failures:
            raise RuntimeError("All news sources failed:\n" + "\n".join(failures))
        print("No articles found in the requested time window")
        return

    messages = build_messages(articles)
    if DRY_RUN:
        print("\n\n--- message chunk ---\n\n".join(messages))
        return

    for message in messages:
        send_wecom_text(session, message)

    print(f"Pushed {len(articles)} articles in {len(messages)} plain-text briefing messages")


if __name__ == "__main__":
    main()