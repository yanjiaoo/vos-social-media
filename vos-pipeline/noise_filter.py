#!/usr/bin/env python3
"""
Noise filtering and deduplication for the VOS pipeline candidate pool.
"""
import re

from models import RawItem


class NoiseFilter:
    BEGINNER_KEYWORDS = [
        "how to start", "how to open", "how to begin", "getting started",
        "beginner guide", "starter guide", "registration tutorial",
        "新手教程", "入门指南", "如何开店", "注册教程", "新手入门",
        "how do i start", "how to sell on amazon", "how to create",
        "开店流程", "注册流程", "新手卖家",
    ]
    AD_KEYWORDS = [
        "service provider", "agency", "代运营", "服务商推荐",
        "tool recommendation", "software promotion", "免费试用",
        "limited offer", "discount code", "promo code",
        "hire us", "contact us for", "our service",
        "工具推荐", "软件推广", "代理服务",
    ]
    VENT_PATTERNS = [
        r'^(amazon sucks|i hate amazon|screw amazon|fuck amazon)',
        r'^(亚马逊垃圾|亚马逊太坑)',
    ]

    def is_beginner_question(self, item: RawItem) -> bool:
        """Check if item is a beginner/how-to-start question."""
        text = (item.title + " " + item.content).lower()
        return any(kw in text for kw in self.BEGINNER_KEYWORDS)

    def is_service_ad(self, item: RawItem) -> bool:
        """Check if item is a service provider advertisement."""
        text = (item.title + " " + item.content).lower()
        return any(kw in text for kw in self.AD_KEYWORDS)

    def is_pure_vent(self, item: RawItem) -> bool:
        """Check if item is pure emotional venting without substance."""
        text = (item.title + " " + item.content).lower().strip()
        for pattern in self.VENT_PATTERNS:
            if re.match(pattern, text, re.IGNORECASE):
                # Only flag as vent if content is very short (no substance)
                if len(item.content.strip()) < 50:
                    return True
        return False

    @staticmethod
    def _extract_key_terms(title: str) -> set:
        """Extract significant terms from a title for dedup comparison."""
        # Remove common stop words and punctuation
        text = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', title.lower())
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "and", "or", "but", "not", "this", "that", "it", "its",
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这",
        }
        words = text.split()
        # Also split Chinese text into individual characters/bigrams
        chinese_chars = re.findall(r'[\u4e00-\u9fff]{2,}', title)
        terms = set()
        for w in words:
            if len(w) > 1 and w not in stop_words:
                terms.add(w)
        for cc in chinese_chars:
            terms.add(cc)
        return terms

    @staticmethod
    def _term_overlap(terms_a: set, terms_b: set) -> float:
        """Calculate the overlap ratio between two term sets."""
        if not terms_a or not terms_b:
            return 0.0
        intersection = terms_a & terms_b
        smaller = min(len(terms_a), len(terms_b))
        if smaller == 0:
            return 0.0
        return len(intersection) / smaller

    def deduplicate(self, items: list) -> list:
        """Remove duplicates: if two items share >50% key terms, keep higher engagement."""
        if not items:
            return items
        # Pre-compute key terms
        item_terms = [(item, self._extract_key_terms(item.title)) for item in items]
        keep = []
        removed = set()

        for i, (item_a, terms_a) in enumerate(item_terms):
            if i in removed:
                continue
            best = item_a
            best_idx = i
            for j in range(i + 1, len(item_terms)):
                if j in removed:
                    continue
                item_b, terms_b = item_terms[j]
                if self._term_overlap(terms_a, terms_b) > 0.5:
                    # Keep the one with higher engagement
                    if item_b.engagement > best.engagement:
                        removed.add(best_idx)
                        best = item_b
                        best_idx = j
                    else:
                        removed.add(j)
            keep.append(best)
        return keep

    def filter_items(self, items: list) -> list:
        """Apply all noise filters and return clean items."""
        filtered = []
        for item in items:
            if self.is_beginner_question(item):
                continue
            if self.is_service_ad(item):
                continue
            if self.is_pure_vent(item):
                continue
            filtered.append(item)
        # Deduplicate
        return self.deduplicate(filtered)
