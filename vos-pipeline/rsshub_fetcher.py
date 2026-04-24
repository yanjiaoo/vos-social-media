#!/usr/bin/env python3
"""
RSSHub multi-source feed fetcher for Chinese seller forums and native RSS sources.
"""
import re
import html
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from models import RawItem, get_source_priority


class RSSHubFetcher:
    ROUTES = {
        "知无不言_hot": "/wearesellers/hot",
        "知无不言_new": "/wearesellers/new",
        "AMZ123": "/amz123/news",
        "卖家之家": "/mjzj/article",
        "Amazon Seller Central Forums": "/amazon/seller-forums",
        "雨果跨境": "/cifnews/article",
    }
    NATIVE_RSS = {
        "Value Added Resource": "https://www.valueaddedresource.net/feed/",
    }
    
    # Google News RSS as fallback (always works from GitHub Actions)
    GOOGLE_NEWS_QUERIES = [
        {"q": "知无不言 亚马逊 卖家 政策 OR 广告 OR 合规", "source": "知无不言", "lang": "zh"},
        {"q": "卖家之家 亚马逊 政策 OR 费用 OR 广告", "source": "卖家之家", "lang": "zh"},
        {"q": "AMZ123 亚马逊 卖家 政策 OR 热议", "source": "AMZ123", "lang": "zh"},
        {"q": "雨果跨境 亚马逊 卖家 政策 OR FBA", "source": "雨果跨境", "lang": "zh"},
        {"q": "亚马逊 FBA OR 广告 OR 账号 卖家 2026", "source": "行业媒体", "lang": "zh"},
        {"q": "亚马逊卖家 政策 OR 变动 site:mp.weixin.qq.com", "source": "微信公众号", "lang": "zh"},
        {"q": "Amazon seller policy change FBA fee 2026", "source": "Industry Media", "lang": "en"},
        {"q": "Amazon FBA seller protest complaint 2026", "source": "Industry Media", "lang": "en"},
    ]
    TIMEOUT = 5  # seconds per source (reduced from 15 to avoid pipeline stalling)

    # Multiple RSSHub instances — only try first one, skip if fails
    RSSHUB_INSTANCES = [
        "https://rsshub.rssforever.com",
    ]

    # Map route keys to display source names
    _SOURCE_NAMES = {
        "知无不言_hot": "知无不言",
        "知无不言_new": "知无不言",
        "AMZ123": "AMZ123",
        "卖家之家": "卖家之家",
        "Amazon Seller Central Forums": "Amazon Seller Central Forums",
        "雨果跨境": "雨果跨境",
        "Value Added Resource": "Value Added Resource",
    }

    def __init__(self, rsshub_base_url: str = None):
        # If no URL provided, will try multiple instances
        self.rsshub_base_url = rsshub_base_url

    def _parse_date(self, date_str: str) -> str:
        """Parse various date formats to ISO YYYY-MM-DD."""
        if not date_str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Try RFC 2822 format (common in RSS)
        for fmt in [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        # Fallback: try to extract date-like pattern
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
        if match:
            return match.group(0)
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _clean_html(self, text: str) -> str:
        """Strip HTML tags and decode entities."""
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r'<[^>]+>', '', text)
        return text.strip()[:500]

    def _parse_feed(self, xml_data: bytes, source_name: str) -> list:
        """Parse RSS XML and return list of RawItem."""
        items = []
        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            print(f"  [RSS] XML parse error for {source_name}: {e}")
            return items

        # Handle both RSS 2.0 and Atom feeds
        # RSS 2.0: <channel><item>
        for item_el in root.findall('.//item'):
            title = self._clean_html(item_el.findtext('title', ''))
            if not title:
                continue
            description = self._clean_html(item_el.findtext('description', ''))
            pub_date = item_el.findtext('pubDate', '')
            link = item_el.findtext('link', '')
            author = item_el.findtext('author', '') or item_el.findtext(
                '{http://purl.org/dc/elements/1.1/}creator', ''
            )

            items.append(RawItem(
                title=title,
                content=description,
                source_platform=source_name,
                source_priority=get_source_priority(source_name),
                date=self._parse_date(pub_date),
                url=link or "",
                engagement=0,
                confirmation_count=0,
                seller_voices=[],
            ))

        # Atom: <entry>
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
            author_el = entry.find('atom:author/atom:name', ns)
            author = author_el.text if author_el is not None else ''

            items.append(RawItem(
                title=title,
                content=content,
                source_platform=source_name,
                source_priority=get_source_priority(source_name),
                date=self._parse_date(pub_date),
                url=link,
                engagement=0,
                confirmation_count=0,
                seller_voices=[],
            ))

        return items

    def fetch_feed(self, url: str, source_name: str) -> list:
        """Fetch and parse a single RSS feed. Returns empty list on failure."""
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; VOSPipeline/1.0)"
            })
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                data = resp.read()
            return self._parse_feed(data, source_name)
        except Exception as e:
            print(f"  [RSS] Failed to fetch {source_name} ({url}): {e}")
            return []

    def fetch_all(self) -> list:
        """Fetch all configured feeds, trying multiple RSSHub instances for fallback."""
        all_items = []

        # Determine which RSSHub instances to try
        if self.rsshub_base_url:
            instances = [self.rsshub_base_url.rstrip("/")]
        else:
            instances = self.RSSHUB_INSTANCES

        # RSSHub routes — try each instance until one works
        rsshub_success = False
        for instance in instances:
            print(f"  [RSS] Trying RSSHub instance: {instance}")
            instance_items = []
            failures = 0
            for key, route in self.ROUTES.items():
                source_name = self._SOURCE_NAMES.get(key, key)
                url = f"{instance}{route}"
                items = self.fetch_feed(url, source_name)
                if items:
                    instance_items.extend(items)
                    print(f"  [RSS] Got {len(items)} items from {source_name}")
                else:
                    failures += 1
            
            if instance_items:
                all_items.extend(instance_items)
                rsshub_success = True
                print(f"  [RSS] RSSHub instance {instance}: {len(instance_items)} items total")
                break  # Found a working instance
            else:
                print(f"  [RSS] RSSHub instance {instance} failed ({failures} failures), trying next...")

        if not rsshub_success:
            print("  [RSS] All RSSHub instances failed")

        # Native RSS feeds (always try these)
        for source_name, url in self.NATIVE_RSS.items():
            print(f"  [RSS] Fetching {source_name} (native)...")
            items = self.fetch_feed(url, source_name)
            all_items.extend(items)
            print(f"  [RSS] Got {len(items)} items from {source_name}")

        print(f"  [RSS] Total: {len(all_items)} items from all feeds")
        return all_items

    def _fetch_google_news_rss(self, query: str, lang: str, source_name: str, max_items: int = 8) -> list:
        """Fetch from Google News RSS as fallback source."""
        from urllib.parse import quote
        hl = "zh-CN" if lang == "zh" else "en"
        gl = "CN" if lang == "zh" else "US"
        ceid = "CN:zh-Hans" if lang == "zh" else "US:en"
        url = f"https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
        return self.fetch_feed(url, source_name)[:max_items]

    def fetch_google_news_fallback(self) -> list:
        """Fetch from Google News RSS queries as fallback when RSSHub fails."""
        all_items = []
        seen_titles = set()
        for qcfg in self.GOOGLE_NEWS_QUERIES:
            items = self._fetch_google_news_rss(qcfg["q"], qcfg["lang"], qcfg["source"])
            for item in items:
                if item.title not in seen_titles:
                    seen_titles.add(item.title)
                    all_items.append(item)
        print(f"  [RSS] Google News fallback: {len(all_items)} items")
        return all_items
