#!/usr/bin/env python3
"""
Cross-source topic merging and clustering for the VOS pipeline.
Groups related items from different sources into unified topic clusters.
"""
import re
from datetime import datetime

from models import RawItem, Cluster


class TopicMerger:
    KEYWORD_OVERLAP_THRESHOLD = 0.4  # 40% significant term overlap
    TEMPORAL_WINDOW_DAYS = 7

    # Common Amazon policy/program terms to boost entity matching
    ENTITY_TERMS = {
        "fba", "fbm", "prime", "prime day", "buy box", "brand registry",
        "gtin", "fnsku", "asin", "sku", "ppc", "acos",
        "dd+7", "资金预留", "共享库存", "贴标", "秒杀",
        "bsa", "agent policy", "vat", "gst",
    }

    @staticmethod
    def extract_significant_terms(text: str) -> set:
        """Extract nouns, proper nouns, policy names from text."""
        if not text:
            return set()
        text_lower = text.lower()
        # Remove common punctuation
        cleaned = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', text_lower)

        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "and", "or", "but", "not", "this", "that", "it", "its",
            "has", "have", "had", "will", "would", "can", "could",
            "should", "may", "might", "do", "does", "did",
            "的", "了", "在", "是", "我", "有", "和", "就", "不",
            "都", "一", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这",
            "amazon", "亚马逊", "seller", "卖家",  # too common
        }

        terms = set()
        # English words
        for word in cleaned.split():
            if len(word) > 1 and word not in stop_words:
                terms.add(word)
        # Chinese bigrams and longer
        chinese_segments = re.findall(r'[\u4e00-\u9fff]{2,}', text)
        for seg in chinese_segments:
            if seg not in stop_words:
                terms.add(seg.lower())
        return terms

    def _has_entity_match(self, terms_a: set, terms_b: set) -> bool:
        """Check if two term sets share a common Amazon entity term."""
        shared = terms_a & terms_b
        return bool(shared & self.ENTITY_TERMS)

    @staticmethod
    def _within_temporal_window(date_a: str, date_b: str, days: int = 7) -> bool:
        """Check if two dates are within the specified number of days."""
        try:
            dt_a = datetime.strptime(date_a, "%Y-%m-%d")
            dt_b = datetime.strptime(date_b, "%Y-%m-%d")
            return abs((dt_a - dt_b).days) <= days
        except (ValueError, TypeError):
            return True  # If dates can't be parsed, don't exclude on temporal grounds

    def calculate_similarity(self, item_a: RawItem, item_b: RawItem) -> float:
        """Calculate similarity score based on keyword overlap, entity matching,
        and temporal proximity. Returns a score between 0 and 1."""
        terms_a = self.extract_significant_terms(item_a.title + " " + item_a.content)
        terms_b = self.extract_significant_terms(item_b.title + " " + item_b.content)

        if not terms_a or not terms_b:
            return 0.0

        # Keyword overlap
        intersection = terms_a & terms_b
        smaller = min(len(terms_a), len(terms_b))
        overlap = len(intersection) / smaller if smaller > 0 else 0.0

        # Entity match bonus
        entity_bonus = 0.15 if self._has_entity_match(terms_a, terms_b) else 0.0

        # Temporal proximity check
        if not self._within_temporal_window(item_a.date, item_b.date, self.TEMPORAL_WINDOW_DAYS):
            return 0.0  # Too far apart in time

        return min(overlap + entity_bonus, 1.0)

    def cluster_items(self, items: list) -> list:
        """Group items into clusters using agglomerative approach.
        Items with >40% keyword overlap + temporal proximity are clustered together."""
        if not items:
            return []

        # Each item starts as its own cluster
        clusters = []
        assigned = set()

        for i, item_a in enumerate(items):
            if i in assigned:
                continue
            cluster_items = [item_a]
            cluster_platforms = {item_a.source_platform}
            assigned.add(i)

            for j in range(i + 1, len(items)):
                if j in assigned:
                    continue
                item_b = items[j]
                # Check similarity against any item in the cluster
                for cluster_item in cluster_items:
                    sim = self.calculate_similarity(cluster_item, item_b)
                    if sim >= self.KEYWORD_OVERLAP_THRESHOLD:
                        cluster_items.append(item_b)
                        cluster_platforms.add(item_b.source_platform)
                        assigned.add(j)
                        break

            # Build cluster
            dates = [it.date for it in cluster_items if it.date]
            dates.sort()
            all_terms = set()
            for it in cluster_items:
                all_terms |= self.extract_significant_terms(it.title)

            cluster = Cluster(
                items=cluster_items,
                source_platforms=cluster_platforms,
                primary_keywords=all_terms,
                date_range=(dates[0] if dates else "", dates[-1] if dates else ""),
            )
            clusters.append(cluster)

        return clusters

    @staticmethod
    def get_cross_source_count(cluster: Cluster) -> int:
        """Get the number of distinct source platforms in a cluster."""
        return len(cluster.source_platforms)
