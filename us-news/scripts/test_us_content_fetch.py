#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
import trafilatura
from googlenewsdecoder import gnews_decoder_async


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

SAMPLES = [
    {"label": "美国政治与政府", "query": "United States politics OR White House OR Congress OR election", "hl": "en", "gl": "US"},
    {"label": "白宫与总统", "query": "White House president policy United States", "hl": "en", "gl": "US"},
    {"label": "国会与立法", "query": "United States Congress Senate House legislation", "hl": "en", "gl": "US"},
    {"label": "美国最高法院", "query": "United States Supreme Court ruling", "hl": "en", "gl": "US"},
    {"label": "美国选举", "query": "United States election campaign politics", "hl": "en", "gl": "US"},
    {"label": "美国经济", "query": "United States economy inflation jobs", "hl": "en", "gl": "US"},
    {"label": "美联储与货币政策", "query": "Federal Reserve interest rates monetary policy", "hl": "en", "gl": "US"},
    {"label": "美国科技与AI", "query": "United States technology artificial intelligence", "hl": "en", "gl": "US"},
    {"label": "美国半导体产业", "query": "United States semiconductor chip manufacturing", "hl": "en", "gl": "US"},
    {"label": "美国能源政策", "query": "United States energy policy oil gas", "hl": "en", "gl": "US"},
    {"label": "美国医疗健康", "query": "United States health care medical policy", "hl": "en", "gl": "US"},
    {"label": "美国移民政策", "query": "United States immigration border policy", "hl": "en", "gl": "US"},
    {"label": "美国国防与军队", "query": "United States defense military Pentagon", "hl": "en", "gl": "US"},
    {"label": "美国国家安全", "query": "United States national security intelligence", "hl": "en", "gl": "US"},
    {"label": "美中关系", "query": "United States China relations trade tariffs technology", "hl": "en", "gl": "US"},
    {"label": "美国与欧盟关系", "query": "United States European Union relations trade tariffs", "hl": "en", "gl": "US"},
    {"label": "美国与日本关系", "query": "United States Japan relations trade defense investment", "hl": "en", "gl": "US"},
    {"label": "美国与韩国关系", "query": "United States South Korea relations trade defense", "hl": "en", "gl": "US"},
    {"label": "美国与印度关系", "query": "United States India relations trade technology defense", "hl": "en", "gl": "US"},
    {"label": "美国与主要经济体", "query": "United States major economies trade tariffs supply chain", "hl": "en", "gl": "US"},
]


def clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def build_feed_url(sample: dict) -> str:
    return (
        "https://news.google.com/rss/search"
        f"?q={quote_plus(sample['query'])}"
        f"&hl={sample['hl']}&gl={sample['gl']}&ceid={sample['gl']}:{sample['hl']}"
    )


def get_first_entry(session: requests.Session, sample: dict) -> dict | None:
    response = session.get(build_feed_url(sample), timeout=25)
    response.raise_for_status()
    feed = feedparser.parse(response.content)

    if not feed.entries:
        return None

    entry = feed.entries[0]
    source = entry.get("source")
    publisher = ""
    if isinstance(source, dict):
        publisher = clean_text(source.get("title"))

    return {
        "label": sample["label"],
        "title": clean_text(entry.get("title")),
        "publisher": publisher,
        "google_link": clean_text(entry.get("link")),
    }


def decode_links(items: list[dict]) -> list[dict]:
    try:
        results = asyncio.run(
            gnews_decoder_async(
                [item["google_link"] for item in items],
                timeout=15,
                concurrency=8,
            )
        )
    except Exception as exc:
        print(f"[DECODE ERROR] {exc}")
        results = []

    if isinstance(results, dict):
        results = [results]

    for item, result in zip(items, results):
        if result.get("success") and result.get("decoded_url"):
            item["decoded_url"] = result["decoded_url"]
        else:
            item["decoded_url"] = item["google_link"]
            item["decode_error"] = clean_text(result.get("message"))

    for item in items:
        item.setdefault("decoded_url", item["google_link"])
        item.setdefault("decode_error", "")
    return items


def extract_content(html_text: str, url: str) -> tuple[str, str]:
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

    if extracted and len(clean_text(extracted)) >= 100:
        return clean_text(extracted), "trafilatura"

    try:
        metadata = trafilatura.extract_metadata(html_text)
        description = clean_text(getattr(metadata, "description", ""))
        if description:
            return description, "metadata"
    except Exception:
        pass

    return "", "none"


def fetch_content(item: dict) -> dict:
    result = dict(item)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        }
    )

    try:
        response = session.get(
            item["decoded_url"],
            timeout=(8, 25),
            allow_redirects=True,
        )
        result["http_status"] = response.status_code
        result["final_url"] = response.url
        result["domain"] = urlparse(response.url).netloc.replace("www.", "")
        result["content_type"] = clean_text(response.headers.get("content-type"))

        content, method = extract_content(response.text[:800000], response.url)
        result["content_length"] = len(content)
        result["extract_method"] = method
        result["content_preview"] = content[:200]
        result["success"] = len(content) >= 200
    except Exception as exc:
        result.setdefault("http_status", None)
        result.setdefault("final_url", item.get("decoded_url", ""))
        result.setdefault("domain", "")
        result["content_length"] = 0
        result["extract_method"] = "error"
        result["content_preview"] = ""
        result["error"] = clean_text(exc)
        result["success"] = False

    return result


def main() -> None:
    feed_session = requests.Session()
    feed_session.headers.update({"User-Agent": USER_AGENT})

    items: list[dict] = []
    for sample in SAMPLES:
        try:
            entry = get_first_entry(feed_session, sample)
            if entry:
                items.append(entry)
                print(f"[FEED OK] {sample['label']}")
            else:
                print(f"[FEED EMPTY] {sample['label']}")
        except Exception as exc:
            print(f"[FEED ERROR] {sample['label']}: {exc}")

    items = decode_links(items)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_content, item) for item in items]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: item["label"])
    success_count = sum(1 for item in results if item.get("success"))
    trafilatura_count = sum(
        1 for item in results if item.get("extract_method") == "trafilatura"
    )
    metadata_count = sum(
        1 for item in results if item.get("extract_method") == "metadata"
    )

    print("\n" + "=" * 80)
    for item in results:
        print(
            f"{'OK' if item.get('success') else 'FAIL'} | "
            f"{item['label']} | {item.get('domain', '')} | "
            f"status={item.get('http_status')} | "
            f"length={item.get('content_length', 0)} | "
            f"method={item.get('extract_method')}"
        )
        print(f"  TITLE: {item.get('title', '')}")
        print(f"  URL: {item.get('final_url', '')}")
        if item.get("content_preview"):
            print(f"  PREVIEW: {item['content_preview']}")
        if item.get("error"):
            print(f"  ERROR: {item['error']}")

    summary = {
        "tested": len(results),
        "success": success_count,
        "failed": len(results) - success_count,
        "success_rate": round(success_count / len(results) * 100, 1) if results else 0,
        "trafilatura_success": trafilatura_count,
        "metadata_fallback": metadata_count,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    print("\n" + "=" * 80)
    print("SUMMARY=" + json.dumps(summary, ensure_ascii=False))

    with open("content_test_results.json", "w", encoding="utf-8") as handle:
        json.dump(
            {"summary": summary, "results": results},
            handle,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    main()