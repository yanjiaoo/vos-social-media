#!/usr/bin/env python3
"""
VOS AI Pipeline Orchestrator — Main entry point.
Collects seller news from Google News RSS and Value Added Resource,
then uses DeepSeek AI to generate intelligence briefings for Amazon AMs.
"""
import json
import os
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
from rsshub_fetcher import RSSFetcher
from noise_filter import NoiseFilter
from topic_merger import TopicMerger
from deepseek_client import DeepSeekClient
from manual_entry import ManualEntryPreserver


class VOSPipeline:
    TOTAL_TOPICS = 50  # max topics to keep (accumulative)
    EXECUTION_TIMEOUT = 180  # seconds
    OUTPUT_FILE = "vos-data.json"

    def __init__(self):
        self.start_time = time.time()
        self.rss = RSSFetcher()
        self.noise_filter = NoiseFilter()
        self.merger = TopicMerger()
        self.manual = ManualEntryPreserver()
        self.deepseek = None

    def _check_timeout(self):
        elapsed = time.time() - self.start_time
        if elapsed > self.EXECUTION_TIMEOUT:
            print(f"  [Pipeline] WARNING: Timeout ({elapsed:.0f}s > {self.EXECUTION_TIMEOUT}s)")
            return True
        return False

    def _init_deepseek(self):
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            print("ERROR: DEEPSEEK_API_KEY environment variable is not set or empty.")
            sys.exit(1)
        self.deepseek = DeepSeekClient(api_key)

    def _assign_ranks(self, topics: list) -> list:
        topics.sort(key=lambda t: t.get("effectDate", ""), reverse=True)
        for i, topic in enumerate(topics):
            topic["rank"] = i + 1
            topic["id"] = f"vos_{i + 1:03d}"
        return topics

    def _ensure_diversity(self, topics: list) -> list:
        categories = set(t.get("topic") for t in topics)
        sources = set(t.get("source") for t in topics)
        if len(categories) < 4:
            print(f"  [Pipeline] Warning: Only {len(categories)} distinct categories (need >=4)")
        if len(sources) < 3:
            print(f"  [Pipeline] Warning: Only {len(sources)} distinct sources (need >=3)")
        return topics

    def _enrich_topic(self, topic: dict) -> dict:
        topic.setdefault("sellerVoices", [])
        topic.setdefault("comparison", [])
        topic.setdefault("links", [])
        topic.setdefault("aiGenerated", True)
        topic["topic"] = normalize_category(topic.get("topic", ""))
        topic["topicLabel"] = TOPIC_LABELS.get(topic["topic"], "🔥 趋势")
        topic["source"] = normalize_source(topic.get("source", ""))

        # Strip fake links — only keep links with real http URLs
        if topic.get("links"):
            topic["links"] = [l for l in topic["links"]
                              if l.get("url", "").startswith("http")]

        # For AI-generated topics, force date to pipeline run date
        # DeepSeek cannot reliably produce real publication dates
        if topic.get("aiGenerated") is True:
            from datetime import datetime, timezone
            topic["effectDate"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        return topic

    def run(self) -> None:
        print("=" * 60)
        print("=== VOS AI Pipeline Starting ===")
        print("=" * 60)

        # 1. Load existing data
        print("\n[Phase 1] Loading existing data...")
        existing = self.manual.load_existing(self.OUTPUT_FILE)
        manual_entries = self.manual.identify_manual_entries(existing)
        print(f"  Found {len(manual_entries)} manual entries to preserve")

        # 2. Collect from stable sources: Google News RSS + Value Added Resource
        print("\n[Phase 2] Collecting from sources...")
        rss_items = []
        try:
            rss_items = self.rss.fetch_all()
            print(f"  Total collected: {len(rss_items)} items")
        except Exception as e:
            print(f"  [RSS] Collection failed: {e}")

        if not rss_items:
            print("  No items collected. Preserving existing data.")
            return

        # 3. Filter noise
        print("\n[Phase 3] Filtering noise...")
        clean_items = self.noise_filter.filter_items(rss_items)
        print(f"  After filtering: {len(clean_items)} items (removed {len(rss_items) - len(clean_items)})")

        # 4. Cluster related items
        print("\n[Phase 4] Clustering related items...")
        clusters = self.merger.cluster_items(clean_items)
        print(f"  Found {len(clusters)} topic clusters")

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

        # 6. Optimize titles (remove clickbait)
        if ai_topics and not self._check_timeout():
            print("\n[Phase 6] Optimizing titles...")
            try:
                ai_topics = self.deepseek.optimize_titles(ai_topics)
            except Exception as e:
                print(f"  [DeepSeek] Title optimization failed: {e}")

        # 7. Enrich short summaries
        if ai_topics and not self._check_timeout():
            print("\n[Phase 7] Enriching short summaries...")
            try:
                ai_topics = self.deepseek.enrich_short_summaries(ai_topics)
            except Exception as e:
                print(f"  [DeepSeek] Summary enrichment failed: {e}")

        # 8. Enrich all new topics with required fields + match RSS URLs
        for topic in ai_topics:
            self._enrich_topic(topic)
            if not topic.get("links"):
                title_words = set(topic.get("title", "").lower().split())
                for item in rss_items:
                    if item.url and item.url.startswith("http"):
                        item_words = set(item.title.lower().split())
                        if len(title_words & item_words) >= 3:
                            topic["links"] = [{"label": item.source_platform, "url": item.url}]
                            break

        # 9. INCREMENTAL MERGE: keep ALL existing topics, only add new non-duplicate ones
        print("\n[Phase 8] Incremental merge (preserving existing topics)...")
        existing_titles = set()
        for e in existing:
            existing_titles.add(e.get("title", "").strip().lower())
            # Also add simplified version for fuzzy matching
            simplified = "".join(e.get("title", "").lower().split())
            existing_titles.add(simplified)

        new_topics = []
        for topic in ai_topics:
            title = topic.get("title", "").strip()
            title_lower = title.lower()
            simplified = "".join(title_lower.split())
            # Check for duplicates: exact match or >60% character overlap with any existing
            is_dup = title_lower in existing_titles or simplified in existing_titles
            if not is_dup:
                for et in existing_titles:
                    if len(et) > 5 and len(simplified) > 5:
                        overlap = sum(1 for c in simplified if c in et)
                        if overlap / max(len(simplified), 1) > 0.6:
                            is_dup = True
                            break
            if not is_dup and validate_topic(topic):
                new_topics.append(topic)

        print(f"  Existing: {len(existing)} topics, New unique: {len(new_topics)} topics")

        # Combine: existing first (unchanged), then new topics
        combined = list(existing) + new_topics

        # 10. Sort, assign ranks
        print("\n[Phase 9] Finalizing...")
        valid_topics = []
        for topic in combined:
            if validate_topic(topic):
                valid_topics.append(topic)
            else:
                if not topic.get("painPoints"):
                    topic["painPoints"] = ["待分析"]
                if not topic.get("title", "").strip():
                    continue
                if validate_topic(topic):
                    valid_topics.append(topic)
                else:
                    print(f"  Warning: Dropping invalid topic: {topic.get('title', 'unknown')[:50]}")

        valid_topics = self._assign_ranks(valid_topics)
        self._ensure_diversity(valid_topics)

        # 11. Write output (safety: never overwrite with empty)
        if not valid_topics and existing:
            print("\n[Phase 10] WARNING: No valid topics but existing data exists. Preserving.")
            return

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
