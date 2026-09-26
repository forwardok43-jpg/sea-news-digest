#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import calendar
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
import trafilatura
from googlenewsdecoder import gnews_decoder_async


TZ = ZoneInfo("Asia/Shanghai")
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
LOG_PREVIEW = os.getenv("LOG_PREVIEW", "0") == "1"
ENABLE_AI_SUMMARY = os.getenv("ENABLE_AI_SUMMARY", "0") == "1"


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


MAX_ITEMS = max(1, env_int("MAX_ITEMS", 30))
MAX_PER_SOURCE = max(1, env_int("MAX_PER_SOURCE", 2))
LOOKBACK_HOURS = max(1, env_int("LOOKBACK_HOURS", 24))
DIGEST_ITEMS = max(3, env_int("DIGEST_ITEMS", 20))
USER_AGENT = "Mozilla/5.0 (compatible; sea-news-digest/1.0; +https://github.com/forwardok43-jpg/sea-news-digest)"

DEFAULT_SUMMARY_MODELS = (
    "thinkingmachines/inkling:free,"
    "dots-studio/dots-3-note-preview:free,"
    "nvidia/nemotron-3-super-120b-a12b:free,"
    "liquid/lfm-2.5-2.6b:free,"
    "qwen/qwen3.8-27b:free,"
    "google/gemma-4-31b-it:free,"
    "openrouter/free"
)
SUMMARY_MODELS = [
    item.strip()
    for item in os.getenv("SUMMARY_MODELS", DEFAULT_SUMMARY_MODELS).split(",")
    if item.strip()
]

PRIORITY_REGIONS = [
    "印度尼西亚",
    "越南",
    "泰国",
    "菲律宾",
    "马来西亚",
    "新加坡",
    "柬埔寨",
    "缅甸",
    "老挝",
    "文莱",
    "东帝汶",
]

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
    source_lang: str = ""
    content: str = ""
    summary: str = ""
    title_zh: str = ""


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


def truncate_text(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))

def normalize_summary(summary: str, article: Article) -> str:
    text = clean_text(summary)
    text = re.sub(r"^(摘要|总结|简讯)[：:]\s*", "", text)
    text = text.strip("\"'“”")
    text = text.rstrip("。！？!?，,；;：:")
    if chinese_char_count(text) < 4:
        return ""
    if len(text) > 18:
        text = text[:18].rstrip("。！？!?，,；;：:")
    return text


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
                source_lang=source["lang"],
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


def select_digest_articles(articles: list[Article], limit: int) -> list[Article]:
    selected: list[Article] = []
    selected_links: set[str] = set()

    for region in PRIORITY_REGIONS:
        candidates = [article for article in articles if region in article.region]
        if not candidates:
            continue
        article = max(candidates, key=lambda item: item.published)
        selected.append(article)
        selected_links.add(article.link)
        if len(selected) >= limit:
            return selected

    for article in articles:
        if article.link in selected_links:
            continue
        selected.append(article)
        if len(selected) >= limit:
            break

    return selected


def decode_google_news_links(articles: list[Article]) -> list[Article]:
    pending = [article for article in articles if "news.google.com" in article.link]
    if not pending:
        return articles

    try:
        results = asyncio.run(
            gnews_decoder_async(
                [article.link for article in pending],
                timeout=15,
                concurrency=min(8, len(pending)),
            )
        )
    except Exception as exc:
        print(f"[WARN] Google News link decoding failed: {exc}")
        return articles

    if isinstance(results, dict):
        results = [results]

    decoded_links: dict[str, str] = {}
    for article, result in zip(pending, results):
        if result.get("success") and result.get("decoded_url"):
            decoded_links[article.link] = result["decoded_url"]
        else:
            print(f"[WARN] Could not decode link for {article.region}")

    return [
        replace(article, link=decoded_links.get(article.link, article.link))
        for article in articles
    ]


def extract_article_content(html_text: str, url: str) -> str:
    try:
        extracted = trafilatura.extract(
            html_text,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception:
        extracted = None

    if extracted:
        return clean_text(extracted)

    try:
        metadata = trafilatura.extract_metadata(html_text)
        if metadata and metadata.description:
            return clean_text(metadata.description)
    except Exception:
        pass

    return ""


def fetch_article_content(article: Article) -> Article:
    try:
        response = requests.get(
            article.link,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            },
            timeout=(5, 18),
            allow_redirects=True,
        )
        response.raise_for_status()
        content = extract_article_content(response.text[:800000], response.url)
        content = truncate_text(content, 1800)
        print(f"[CONTENT] {article.region}: {len(content)} chars")
        return replace(article, content=content)
    except Exception as exc:
        print(f"[WARN] Content fetch failed for {article.region}: {exc}")
        return article


def enrich_articles(articles: list[Article]) -> list[Article]:
    if not articles:
        return articles
    with ThreadPoolExecutor(max_workers=min(6, len(articles))) as executor:
        return list(executor.map(fetch_article_content, articles))


def parse_summary_response(content: str) -> dict[int, str]:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            summaries: dict[int, str] = {}
            for item in payload:
                summary = clean_text(item.get("summary"))
                if summary and chinese_char_count(summary) >= 4:
                    summaries[int(item["id"])] = summary
            if summaries:
                return summaries
        except Exception:
            pass

    summaries: dict[int, str] = {}
    pattern = re.compile(
        r'\{\s*"id"\s*:\s*(\d+)\s*,\s*"summary"\s*:\s*"((?:\\.|[^"\\])*)"\s*\}',
        re.DOTALL,
    )
    for item_id, encoded_summary in pattern.findall(text):
        try:
            summary = clean_text(json.loads(f'"{encoded_summary}"'))
        except Exception:
            summary = clean_text(encoded_summary)
        if summary and chinese_char_count(summary) >= 4:
            summaries[int(item_id)] = summary
    return summaries


def request_openrouter_summaries(
    articles: list[Article],
    model: str,
) -> dict[int, str]:
    payload_items = []
    for index, article in enumerate(articles, start=1):
        payload_items.append(
            {
                "id": index,
                "country": article.region,
                "title": article.title,
                "source": article.publisher,
                "content": truncate_text(article.content or article.title, 1200),
            }
        )

    system_prompt = (
        "你是专业的东南亚新闻中文编辑。请逐条概括新闻内容，"
        "每条必须是简体中文，严格控制在12到18个汉字。"
        "只陈述最重要事实，不评论、不重复标题、不写来源、不加句末标点。"
        '只返回JSON数组，格式为：[{"id":1,"summary":"摘要"}]。'
    )
    user_prompt = json.dumps(payload_items, ensure_ascii=False)

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/forwardok43-jpg/sea-news-digest",
            "X-Title": "SEA News Digest",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 900,
        },
        timeout=(10, 180),
    )

    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")

    data = response.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"]))

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices")

    message = choices[0].get("message", {}) or {}
    content = message.get("content") or message.get("reasoning") or ""
    if not isinstance(content, str):
        raise RuntimeError(f"Unexpected content type: {type(content).__name__}")

    summaries = parse_summary_response(content)
    if not summaries:
        raise RuntimeError(
            f"Could not parse summaries: {truncate_text(content, 300)}"
        )
    return summaries


def translate_title_to_chinese(title: str, source_lang: str) -> str:
    title = clean_text(title)
    if chinese_char_count(title) >= 6:
        return title

    language = (source_lang or "auto").strip()
    language_pairs = [f"{language}|zh-CN"]
    if language != "en":
        language_pairs.append("en|zh-CN")

    for langpair in language_pairs:
        try:
            response = requests.get(
                "https://api.mymemory.translated.net/get",
                params={"q": title[:450], "langpair": langpair},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            translated = clean_text(
                (data.get("responseData") or {}).get("translatedText")
            )
            if chinese_char_count(translated) >= 4:
                print(f"[TITLE] Translated with {langpair}: {truncate_text(translated, 30)}")
                return translated
        except Exception as exc:
            print(f"[WARN] Title translation failed ({langpair}): {exc}")

    return ""

def summarize_articles(articles: list[Article]) -> list[Article]:
    summaries: dict[int, str] = {}

    if ENABLE_AI_SUMMARY and OPENROUTER_API_KEY:
        minimum_valid = max(1, len(articles) // 2)
        for model in SUMMARY_MODELS:
            try:
                print(f"[SUMMARY] Trying model: {model}")
                candidate = request_openrouter_summaries(articles, model)
                valid = {
                    item_id: summary
                    for item_id, summary in candidate.items()
                    if chinese_char_count(summary) >= 6
                }
                if len(valid) >= minimum_valid:
                    summaries = valid
                    print(
                        f"[SUMMARY] Success with model: {model}; "
                        f"valid={len(valid)}/{len(articles)}"
                    )
                    break
                print(
                    f"[WARN] Model returned insufficient Chinese summaries "
                    f"({len(valid)}/{len(articles)}): {model}"
                )
            except Exception as exc:
                print(f"[WARN] Summary model failed ({model}): {exc}")
            time.sleep(2)
    else:
        print("[INFO] AI content summaries are disabled; translating full titles only")

    translated_titles: dict[int, str] = {}
    for index, article in enumerate(articles, start=1):
        translated = translate_title_to_chinese(article.title, article.source_lang)
        if translated:
            translated_titles[index] = translated

    summarized: list[Article] = []
    for index, article in enumerate(articles, start=1):
        summary = normalize_summary(summaries.get(index, ""), article)
        summarized.append(
            replace(
                article,
                summary=summary,
                title_zh=translated_titles.get(index, ""),
            )
        )
    return summarized

def source_label(article: Article) -> str:
    if article.publisher:
        return article.publisher
    return urlparse(article.link).netloc.replace("www.", "")


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


def build_messages(articles: list[Article]) -> list[str]:
    now = datetime.now(TZ)
    parts = [
        "【东南亚新闻简报】",
        f"{now:%Y-%m-%d %H:%M} 北京时间",
        f"过去 {LOOKBACK_HOURS} 小时｜精选 {len(articles)} 条热点",
        "────────────────",
    ]

    article_blocks: list[str] = []
    for article in articles:
        local_time = article.published.astimezone(TZ).strftime("%m-%d %H:%M")
        title = article.title_zh or article.title
        block = f"【{article.region}】\n{title}"
        if article.summary and article.summary != title:
            block += f"\n摘要：{article.summary}"
        block += f"\n{source_label(article)} · {local_time}"
        article_blocks.append(block)

    text = "\n\n".join(parts + article_blocks)
    text += (
        "\n\n────────────────\n"
        "说明：标题和摘要由免费翻译及摘要服务自动生成，重要信息请以原文为准。"
    )

    messages = split_text(text, 1900)
    if len(messages) > 1:
        messages = [messages[0]] + [
            f"【东南亚新闻简报（续）】\n\n{message}" for message in messages[1:]
        ]
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

    digest_articles = select_digest_articles(articles, DIGEST_ITEMS)
    print(f"[DIGEST] Selected {len(digest_articles)} country-level articles")

    digest_articles = decode_google_news_links(digest_articles)
    if ENABLE_AI_SUMMARY:
        digest_articles = enrich_articles(digest_articles)
    digest_articles = summarize_articles(digest_articles)

    messages = build_messages(digest_articles)
    for index, message in enumerate(messages, start=1):
        print(f"[SEND] Part {index}/{len(messages)}, {len(message.encode('utf-8'))} bytes")

    if LOG_PREVIEW:
        print("\n\n--- preview ---\n\n" + "\n\n--- next message ---\n\n".join(messages))

    if DRY_RUN:
        print("\n\n--- message chunk ---\n\n".join(messages))
        return

    for message in messages:
        send_wecom_text(session, message)

    print(f"Pushed {len(digest_articles)} country summaries in {len(messages)} message(s)")


if __name__ == "__main__":
    main()