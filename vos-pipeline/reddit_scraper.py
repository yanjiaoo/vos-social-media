#!/usr/bin/env python3
"""
Reddit deep scraper with comment analysis for Amazon seller subreddits.
Uses the public JSON API (no OAuth required).
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from models import RawItem, CONFIRMATION_WORDS, get_source_priority

USER_AGENT = "Mozilla/5.0 (compatible; VOSPipeline/1.0)"
REQUEST_DELAY = 2  # seconds between requests to avoid rate limiting


class RedditScraper:
    SUBREDDITS = ["FulfillmentByAmazon", "AmazonSeller"]
    MIN_COMMENTS = 3
    MIN_UPVOTES = 5

    def __init__(self):
        self._last_request_time = 0

    def _throttle(self):
        """Ensure minimum delay between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_time = time.time()

    def _get_json(self, url: str) -> dict:
        """Fetch JSON from a URL with throttling and error handling."""
        self._throttle()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"  [Reddit] Rate limited, waiting 60s...")
                time.sleep(60)
                # Retry once
                self._throttle()
                req2 = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req2, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            raise

    def fetch_posts(self, subreddit: str, limit: int = 25) -> list:
        """Fetch hot posts from a subreddit via the public JSON API."""
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
        posts = []
        try:
            data = self._get_json(url)
            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                if post.get("stickied"):
                    continue
                posts.append({
                    "title": post.get("title", ""),
                    "selftext": post.get("selftext", ""),
                    "url": f"https://www.reddit.com{post.get('permalink', '')}",
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "created_utc": post.get("created_utc", 0),
                    "subreddit": subreddit,
                })
        except Exception as e:
            print(f"  [Reddit] Error fetching r/{subreddit}: {e}")
        return posts

    def fetch_comments(self, post_url: str, limit: int = 30) -> list:
        """Fetch top comments for a post. Returns list of comment dicts."""
        json_url = post_url.rstrip("/") + ".json?limit=" + str(limit) + "&sort=top"
        comments = []
        try:
            data = self._get_json(json_url)
            if isinstance(data, list) and len(data) > 1:
                comment_listing = data[1].get("data", {}).get("children", [])
                for child in comment_listing:
                    if child.get("kind") != "t1":
                        continue
                    c = child.get("data", {})
                    comments.append({
                        "body": c.get("body", ""),
                        "score": c.get("score", 0),
                        "author": c.get("author", ""),
                    })
        except Exception as e:
            print(f"  [Reddit] Error fetching comments: {e}")
        return comments

    @staticmethod
    def count_confirmations(comments: list) -> int:
        """Count comments containing confirmation words (case-insensitive)."""
        count = 0
        for c in comments:
            body_lower = c.get("body", "").lower()
            for word in CONFIRMATION_WORDS:
                if word in body_lower:
                    count += 1
                    break  # count each comment at most once
        return count

    @staticmethod
    def extract_seller_voices(comments: list, max_voices: int = 3) -> list:
        """Extract top-scored comments (score >= 5) as seller voices."""
        eligible = [c for c in comments if c.get("score", 0) >= 5]
        eligible.sort(key=lambda c: c.get("score", 0), reverse=True)
        voices = []
        for c in eligible[:max_voices]:
            score = c.get("score", 0)
            body = c.get("body", "").strip()
            if len(body) > 300:
                body = body[:297] + "..."
            voices.append({
                "source": f"Reddit (👍{score})",
                "content": body,
            })
        return voices

    def scrape_all(self) -> list:
        """Scrape all configured subreddits, apply engagement filter, return RawItems."""
        all_items = []
        for sub in self.SUBREDDITS:
            source_name = f"Reddit r/{sub}"
            print(f"  [Reddit] Scraping r/{sub}...")
            posts = self.fetch_posts(sub)
            for post in posts:
                # Engagement filter: skip posts with <3 comments AND <5 upvotes
                if post["num_comments"] < self.MIN_COMMENTS and post["score"] < self.MIN_UPVOTES:
                    continue

                # Fetch comments for posts with >10 comments
                comments = []
                confirmation_count = 0
                seller_voices = []
                if post["num_comments"] > 10:
                    comments = self.fetch_comments(post["url"], limit=30)
                    confirmation_count = self.count_confirmations(comments)
                    seller_voices = self.extract_seller_voices(comments)

                # Convert created_utc to ISO date
                try:
                    dt = datetime.fromtimestamp(post["created_utc"], tz=timezone.utc)
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                item = RawItem(
                    title=post["title"],
                    content=post["selftext"][:500] if post["selftext"] else "",
                    source_platform=source_name,
                    source_priority=get_source_priority(source_name),
                    date=date_str,
                    url=post["url"],
                    engagement=post["score"],
                    confirmation_count=confirmation_count,
                    seller_voices=seller_voices,
                )
                all_items.append(item)
            print(f"  [Reddit] Got {len([i for i in all_items if i.source_platform == source_name])} posts from r/{sub}")
        return all_items
