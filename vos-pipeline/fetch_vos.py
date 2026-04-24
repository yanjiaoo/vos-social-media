#!/usr/bin/env python3
"""
VOS AI Pipeline Orchestrator — Main entry point.
Collects seller discussions from Reddit, RSSHub, and DeepSeek API,
merges and enriches them into intelligence briefings for Amazon AMs.
"""
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone

# Add pipeline directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    TOPIC_LABELS, LAYER_LABELS, ALLOWED_TOPICS,
    normalize_category, normalize_layer, normalize_source,
    get_source_priority, validate_topic,
)
from reddit_scraper import RedditScraper
from rsshub_fetcher import RSSHubFetcher
from noise_filter import NoiseFilter
from topic_merger import TopicMerger
from deepseek_client import DeepSeekClient
from manual_entry import ManualEntryPreserver


class VOSPipeline:
    TOTAL_TOPICS = 20
    EXECUTION_TIMEOUT = 120  # seconds
    OUTPUT_FILE = "vos-data.json"

    def __init__(self):
        self.start_time = time.time()
        self.reddit = RedditScraper()
        self.rsshub = RSSHubFetcher()
        self.noise_filter = NoiseFilter()
        self.merger = TopicMerger()
        self.manual = ManualEntryPreserver()
        self.deepseek = None  # Initialized lazily

    def _check_timeout(self):
        """Check if execution has exceeded the timeout budget."""
        elapsed = time.time() - self.start_time
        if elapsed > self.EXECUTION_TIMEOUT:
            print(f"  [Pipeline] WARNING: Execution timeout ({elapsed:.0f}s > {self.EXECUTION_TIMEOUT}s)")
            return True
        return False

    def _init_deepseek(self):
        """Initialize DeepSeek client (exits if API key missing)."""
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            print("ERROR: DEEPSEEK_API_KEY environment variable is not set or empty.")
            sys.exit(1)
        self.deepseek = DeepSeekClient(api_key)

    def _assign_ranks(self, topics: list) -> list:
        """Sort by effectDate descending and assign sequential ranks."""
        # Sort by effectDate descending (most recent first)
        topics.sort(key=lambda t: t.get("effectDate", ""), reverse=True)
        for i, topic in enumerate(topics):
            topic["rank"] = i + 1
            topic["id"] = f"vos_{i + 1:03d}"
        return topics

    def _ensure_diversity(self, topics: list) -> list:
        """Log warnings if diversity requirements aren't met."""
        categories = set(t.get("topic") for t in topics)
        sources = set(t.get("source") for t in topics)

        if len(categories) < 4:
            print(f"  [Pipeline] Warning: Only {len(categories)} distinct categories (need ≥4)")
        if len(sources) < 3:
            print(f"  [Pipeline] Warning: Only {len(sources)} distinct sources (need ≥3)")

        # Check priority tier coverage
        has_high = any(get_source_priority(t.get("source", "")) == "high" for t in topics)
        has_medium = any(get_source_priority(t.get("source", "")) == "medium" for t in topics)
        if not has_high:
            print("  [Pipeline] Warning: No high-priority source in output")
        if not has_medium:
            print("  [Pipeline] Warning: No medium-priority source in output")

        return topics

    def _enrich_topic(self, topic: dict) -> dict:
        """Ensure all required fields are present with correct values."""
        topic.setdefault("verified", "unconfirmed")
        topic.setdefault("sellerVoices", [])
        topic.setdefault("comparison", [])
        topic.setdefault("links", [])
        topic.setdefault("aiGenerated", True)
        topic.setdefault("sentiment", "neutral")
        topic.setdefault("painPoints", ["待分析"])
        topic.setdefault("alertLevel", "normal")
        topic.setdefault("insightType", "confirmation")

        # Normalize fields
        topic["topic"] = normalize_category(topic.get("topic", ""))
        topic["topicLabel"] = TOPIC_LABELS.get(topic["topic"], "🔥 趋势")
        topic["layer"] = normalize_layer(topic.get("layer", ""))
        topic["layerLabel"] = LAYER_LABELS.get(topic["layer"], "🔍 盲区发现")
        topic["source"] = normalize_source(topic.get("source", ""))

        return topic

    def run(self) -> None:
        """Execute the full pipeline."""
        print("=" * 60)
        print("=== VOS AI Pipeline Starting ===")
        print("=" * 60)

        # 1. Load existing data, identify manual entries
        print("\n[Phase 1] Loading existing data...")
        existing = self.manual.load_existing(self.OUTPUT_FILE)
        manual_entries = self.manual.identify_manual_entries(existing)
        print(f"  Found {len(manual_entries)} manual entries to preserve")

        # 2. Collect from Reddit + RSSHub
        print("\n[Phase 2] Collecting from sources...")
        reddit_items = []
        rss_items = []

        try:
            reddit_items = self.reddit.scrape_all()
            print(f"  Reddit: {len(reddit_items)} items")
        except Exception as e:
            print(f"  [Reddit] Collection failed: {e}")

        if not self._check_timeout():
            try:
                rss_items = self.rsshub.fetch_all()
                print(f"  RSSHub: {len(rss_items)} items")
            except Exception as e:
                print(f"  [RSSHub] Collection failed: {e}")

        candidate_pool = reddit_items + rss_items
        print(f"  Total candidate pool: {len(candidate_pool)} items")

        # 3. Filter noise
        print("\n[Phase 3] Filtering noise...")
        clean_items = self.noise_filter.filter_items(candidate_pool)
        print(f"  After filtering: {len(clean_items)} items (removed {len(candidate_pool) - len(clean_items)})")

        # 4. Cluster related items
        print("\n[Phase 4] Clustering related items...")
        clusters = self.merger.cluster_items(clean_items)
        print(f"  Found {len(clusters)} topic clusters")
        multi_source = [c for c in clusters if len(c.source_platforms) >= 2]
        print(f"  Multi-source clusters: {len(multi_source)}")

        # 5. Generate topics via DeepSeek
        print("\n[Phase 5] Generating topics via DeepSeek...")
        self._init_deepseek()
        ai_topics = []

        try:
            ai_topics = self.deepseek.generate_topics(clean_items)
            print(f"  Generated {len(ai_topics)} AI topics")
        except Exception as e:
            print(f"  [DeepSeek] Topic generation failed: {e}")
            print("  Preserving existing data unchanged.")
            return

        if not ai_topics:
            print("  [DeepSeek] No topics generated. Preserving existing data.")
            return

        # 6. Generate briefings for multi-source clusters
        if multi_source and not self._check_timeout():
            print("\n[Phase 6] Generating intelligence briefings...")
            for cluster in multi_source[:5]:  # Limit to top 5 clusters
                if self._check_timeout():
                    break
                try:
                    briefing = self.deepseek.generate_briefing(cluster.items)
                    # Find matching AI topic and enrich with briefing data
                    for topic in ai_topics:
                        cluster_titles = [it.title.lower() for it in cluster.items]
                        if any(kw in topic.get("title", "").lower() for kw in
                               [t[:10] for t in cluster_titles if len(t) >= 10]):
                            topic["crossSourceCount"] = len(cluster.source_platforms)
                            topic["sourceBreakdown"] = briefing.get("sourceBreakdown", {})
                            topic["sellerConsensus"] = briefing.get("sellerConsensus", "")
                            if briefing.get("briefing"):
                                topic["summary"] = briefing["briefing"]
                            break
                except Exception as e:
                    print(f"  [DeepSeek] Briefing failed: {e}")

        # 7. Enrich Reddit data into AI topics
        print("\n[Phase 7] Enriching with Reddit data...")
        for topic in ai_topics:
            # Try to match with Reddit items for confirmation data
            for item in reddit_items:
                if item.confirmation_count > 0 or item.seller_voices:
                    # Simple title keyword match
                    topic_words = set(topic.get("title", "").lower().split())
                    item_words = set(item.title.lower().split())
                    if len(topic_words & item_words) >= 3:
                        topic["confirmationCount"] = max(
                            topic.get("confirmationCount", 0),
                            item.confirmation_count
                        )
                        if item.seller_voices and not topic.get("sellerVoices"):
                            topic["sellerVoices"] = item.seller_voices
                        break

        # 8. Enrich all topics
        for topic in ai_topics:
            self._enrich_topic(topic)

        # 9. Merge with manual entries
        print("\n[Phase 8] Merging with manual entries...")
        merged = self.manual.merge(manual_entries, ai_topics, self.TOTAL_TOPICS)
        print(f"  Final count: {len(merged)} topics ({len(manual_entries)} manual + {len(merged) - len(manual_entries)} AI)")

        # 10. Validate, sort, assign ranks
        print("\n[Phase 9] Validating and finalizing...")
        valid_topics = []
        for topic in merged:
            self._enrich_topic(topic)
            if validate_topic(topic):
                valid_topics.append(topic)
            else:
                # Try to fix common issues
                if not topic.get("painPoints"):
                    topic["painPoints"] = ["待分析"]
                if not topic.get("title", "").strip():
                    continue
                # Re-validate
                if validate_topic(topic):
                    valid_topics.append(topic)
                else:
                    print(f"  Warning: Dropping invalid topic: {topic.get('title', 'unknown')[:50]}")

        valid_topics = self._assign_ranks(valid_topics)
        self._ensure_diversity(valid_topics)

        # 11. Write output
        print(f"\n[Phase 10] Writing {self.OUTPUT_FILE}...")
        with open(self.OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(valid_topics, f, ensure_ascii=False, indent=2)

        elapsed = time.time() - self.start_time
        print(f"\n{'=' * 60}")
        print(f"=== VOS AI Pipeline Complete ({elapsed:.1f}s) ===")
        print(f"=== Output: {len(valid_topics)} topics in {self.OUTPUT_FILE} ===")
        print(f"{'=' * 60}")


def main():
    pipeline = VOSPipeline()
    pipeline.run()


if __name__ == "__main__":
    main()
