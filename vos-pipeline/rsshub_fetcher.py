#!/usr/bin/env python3
"""
RSS feed fetcher — Google News RSS + Value Added Resource (native RSS).
These are the two stable sources that work reliably from GitHub Actions.
"""
import re
import html
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote

from models import RawItem, get_source_priority


class RSSFetcher:
    """Fetches seller news from Google News RSS and Value Added Resource."""

    NATIVE_RSS = {
        "Value Added Resource": "https://www.valueaddedresource.net/feed/",
    }

    GOOGLE_NEWS_QUERIES = [
        {"q": "亚马逊 卖家 政策 OR 广告 OR 合规 OR FBA", "source": "雨果跨境", "lang": "zh"},
        {"q": "亚马逊 FBA OR 费用 OR 佣金 卖家 2026", "source": "行业媒体", "lang": "zh"},
        {"q": "亚马逊卖家 政策 OR 变动 site:mp.weixin.qq.com", "source": "微信公众号", "lang": "zh"},
        {"q": "跨境电商 亚马逊 卖家 热议 OR 新规", "source": "行业媒体", "lang": "zh"},
        {"q": "Amazon seller policy change FBA fee 2026", "source": "Value Added Resource", "lang": "en"},
        {"q": "Amazon FBA seller protest complaint 2026", "source": "Value Added Resource", "lang": "en"},
        {"q": "Amazon seller advertising PPC policy 2026", "source": "PPC Land", "lang": "en"},
    ]

    TIMEOUT = 10  # seconds per request

    def _parse_date(self, date_str: str) -> str:
        if not date_str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for fmt in [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
        if match:
            return match.group(0)
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _clean_html(self, text: str) -> str:
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()[:500]

    def _parse_feed(self, xml_data: bytes, source_name: str) -> list:
        items = []
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            print(f"  [RSS] XML parse error for {source_name}: {e}")
            return items

        # RSS 2.0
        for item_el in root.findall('.//item'):
            title = self._clean_html(item_el.findtext('title', ''))
            if not title:
                continue
            description = self._clean_html(item_el.findtext('description', ''))
            pub_date = item_el.findtext('pubDate', '')
            link = item_el.findtext('link', '')
            items.append(RawItem(
                title=title,
                content=description,
                source_platform=source_name,
                source_priority=get_source_priority(source_name),
                date=self._parse_date(pub_date),
                url=link or "",
            ))

        # Atom
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('.//atom:entry', ns):
            title = self._clean_html(entry.findtext('atom:title', '', ns))
            if not title:
                continue
            content_el = entry.find('atom:content', ns) or entry.find('atom:summary', ns)
            content = self._clean_html(content_el.text if content_el is not None and content_el.text else '')
            pub_date = entry.findtext('atom:published', '', ns) or entry.findtext('atom:updated', '', ns)
            link_el = entry.find('atom:link', ns)
            link = link_el.get('href', '') if link_el is not None else ''
            items.append(RawItem(
                title=title,
                content=content,
                source_platform=source_name,
                source_priority=get_source_priority(source_name),
                date=self._parse_date(pub_date),
                url=link,
            ))

        return items

    def _fetch_url(self, url: str, source_name: str) -> list:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; VOSPipeline/2.0)"
            })
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                data = resp.read()
            return self._parse_feed(data, source_name)
        except Exception as e:
            print(f"  [RSS] Failed to fetch {source_name} ({url}): {e}")
            return []

    def _fetch_google_news(self, query: str, lang: str, source_name: str, max_items: int = 10) -> list:
        hl = "zh-CN" if lang == "zh" else "en"
        gl = "CN" if lang == "zh" else "US"
        ceid = "CN:zh-Hans" if lang == "zh" else "US:en"
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
        return self._fetch_url(url, source_name)[:max_items]

    def fetch_all(self) -> list:
        """Fetch from all stable sources: Google News RSS + Value Added Resource."""
        all_items = []
        seen_titles = set()

        # 1. Value Added Resource (native RSS — always stable)
        for source_name, url in self.NATIVE_RSS.items():
            print(f"  [RSS] Fetching {source_name}...")
            items = self._fetch_url(url, source_name)
            for item in items:
                if item.title not in seen_titles:
                    seen_titles.add(item.title)
                    all_items.append(item)
            print(f"  [RSS] Got {len(items)} items from {source_name}")

        # 2. Google News RSS (multiple queries, deduplicated)
        print(f"  [RSS] Fetching Google News ({len(self.GOOGLE_NEWS_QUERIES)} queries)...")
        google_count = 0
        for qcfg in self.GOOGLE_NEWS_QUERIES:
            items = self._fetch_google_news(qcfg["q"], qcfg["lang"], qcfg["source"])
            for item in items:
                if item.title not in seen_titles:
                    seen_titles.add(item.title)
                    all_items.append(item)
                    google_count += 1
        print(f"  [RSS] Got {google_count} unique items from Google News")

        print(f"  [RSS] Total: {len(all_items)} items")
        return all_items
