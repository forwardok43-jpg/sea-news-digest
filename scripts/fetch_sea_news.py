#!/usr/bin/env python3
from __future__ import annotations

import calendar
import html
import os
import re
import time
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
USER_AGENT = "sea-news-digest/1.0"

SOURCES = [
    {"name": "东盟区域", "query": "ASEAN OR Southeast Asia", "lang": "en", "country": "SG"},
    {"name": "印度尼西亚", "query": "Indonesia OR Jakarta", "lang": "id", "country": "ID"},
    {"name": "越南", "query": "Việt Nam OR Hà Nội", "lang": "vi", "country": "VN"},
    {"name": "泰国", "query": "ประเทศไทย OR กรุงเทพ", "lang": "th", "country": "TH"},
    {"name": "菲律宾", "query": "Philippines OR Manila", "lang": "en", "country": "PH"},
    {"name": "马来西亚", "query": "Malaysia OR Kuala Lumpur", "lang": "ms", "country": "MY"},
    {"name": "新加坡", "query": "Singapore", "lang": "en", "country": "SG"},
    {"name": "柬埔寨", "query": "Cambodia OR Phnom Penh", "lang": "km", "country": "KH"},
    {"name": "缅甸", "query": "Myanmar OR Yangon", "lang": "my", "country": "MM"},
    {"name": "老挝", "query": "Laos OR Vientiane", "lang": "lo", "country": "LA"},
    {"name": "文莱", "query": "Brunei OR Bandar Seri Begawan", "lang": "en", "country": "BN"},
    {"name": "东帝汶", "query": "Timor-Leste OR Dili", "lang": "pt", "country": "TL"},
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
    return re.sub(r"[\[\]*_`]", "", clean_text(title))


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


def build_messages(articles: list[Article]) -> list[str]:
    now = datetime.now(TZ)
    header = (
        "# 东南亚新闻日报\n"
        f"> {now:%Y-%m-%d %H:%M} 北京时间\n"
        f"> 过去 {LOOKBACK_HOURS} 小时 · 共 {len(articles)} 条"
    )
    blocks: list[str] = []

    for index, article in enumerate(articles, start=1):
        local_time = article.published.astimezone(TZ).strftime("%m-%d %H:%M")
        title = safe_markdown_title(article.title)
        link = article.link.replace("(", "%28").replace(")", "%29")
        blocks.append(
            f"{index}. [{title}]({link})\n"
            f"> {article.region} · {article.publisher or '来源未知'} · {local_time}"
        )

    messages: list[str] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate.encode("utf-8")) > 3500:
            messages.append(current)
            current = f"# 东南亚新闻日报（续）\n\n{block}"
        else:
            current = candidate

    if current:
        messages.append(current)
    return messages


def send_wecom_markdown(session: requests.Session, content: str) -> None:
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
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
        send_wecom_markdown(session, message)

    print(f"Pushed {len(articles)} articles in {len(messages)} messages")


if __name__ == "__main__":
    main()