#!/usr/bin/env python3
"""
Manual entry preservation across pipeline runs.
Ensures hand-curated entries (with sellerVoices or comparison data) are not overwritten.
"""
import json
import re


class ManualEntryPreserver:

    def load_existing(self, filepath: str) -> list:
        """Load existing vos-data.json and return all entries."""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            print(f"  [Manual] Warning: {filepath} is not a JSON array, starting fresh")
            return []
        except FileNotFoundError:
            print(f"  [Manual] {filepath} not found, starting fresh")
            return []
        except json.JSONDecodeError as e:
            print(f"  [Manual] Invalid JSON in {filepath}: {e}, starting fresh")
            return []

    @staticmethod
    def identify_manual_entries(entries: list) -> list:
        """Identify entries where sellerVoices or comparison is non-empty."""
        manual = []
        for entry in entries:
            voices = entry.get("sellerVoices", [])
            comparison = entry.get("comparison", [])
            if voices or comparison:
                manual.append(entry)
        return manual

    @staticmethod
    def _extract_key_terms(title: str) -> set:
        """Extract key terms from a title for similarity comparison."""
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', title.lower())
        stop_words = {
            "the", "a", "an", "is", "are", "to", "of", "in", "for", "on",
            "and", "or", "but", "this", "that", "it",
            "的", "了", "在", "是", "我", "有", "和", "就", "不",
            "amazon", "亚马逊", "seller", "卖家",
        }
        terms = set()
        for w in text.split():
            if len(w) > 1 and w not in stop_words:
                terms.add(w)
        chinese_segments = re.findall(r'[\u4e00-\u9fff]{2,}', title)
        for seg in chinese_segments:
            if seg not in stop_words:
                terms.add(seg.lower())
        return terms

    def is_duplicate(self, manual_entry: dict, new_topic: dict) -> bool:
        """Check if a new topic covers the same subject as a manual entry (title similarity)."""
        terms_a = self._extract_key_terms(manual_entry.get("title", ""))
        terms_b = self._extract_key_terms(new_topic.get("title", ""))
        if not terms_a or not terms_b:
            return False
        intersection = terms_a & terms_b
        smaller = min(len(terms_a), len(terms_b))
        if smaller == 0:
            return False
        overlap = len(intersection) / smaller
        return overlap > 0.5

    def merge(self, manual_entries: list, new_topics: list, max_total: int = 20) -> list:
        """Merge manual entries with new topics.
        - Manual entries keep their content unchanged, aiGenerated=false
        - Duplicate new topics (similar title to manual entry) are discarded
        - Fill remaining slots with new topics up to max_total
        """
        # Mark manual entries
        for entry in manual_entries:
            entry["aiGenerated"] = False

        # Filter out new topics that duplicate manual entries
        unique_new = []
        for topic in new_topics:
            is_dup = False
            for manual in manual_entries:
                if self.is_duplicate(manual, topic):
                    is_dup = True
                    break
            if not is_dup:
                topic["aiGenerated"] = True
                unique_new.append(topic)

        # Combine: manual entries first, then fill with new topics
        remaining_slots = max_total - len(manual_entries)
        result = list(manual_entries) + unique_new[:max(0, remaining_slots)]

        # Cap at max_total
        return result[:max_total]
