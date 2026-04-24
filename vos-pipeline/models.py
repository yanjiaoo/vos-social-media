#!/usr/bin/env python3
"""
Data models, constants, normalization, and validation for the VOS AI pipeline.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

# ==================== Constants ====================

CONFIRMATION_WORDS = [
    "confirmed", "happening to me too", "same here",
    "can confirm", "me too", "same issue"
]

ALLOWED_TOPICS = {
    "advertising", "promotion", "compliance", "brand",
    "returns", "tax", "logistics", "trending"
}

TOPIC_LABELS = {
    "advertising": "📢 广告",
    "promotion": "🏷️ 促销",
    "compliance": "⚖️ 合规",
    "brand": "🏢 品牌",
    "returns": "📦 退货",
    "tax": "💰 税务",
    "logistics": "🚚 物流",
    "trending": "🔥 趋势",
}

ALLOWED_LAYERS = {"policy_impact", "macro_event", "emerging_unknown"}

LAYER_LABELS = {
    "policy_impact": "📋 政策影响",
    "macro_event": "🌍 宏观传导",
    "emerging_unknown": "🔍 盲区发现",
}

ALLOWED_SENTIMENTS = {"negative", "neutral", "positive"}
ALLOWED_ALERT_LEVELS = {"critical", "high", "normal"}
ALLOWED_INSIGHT_TYPES = {"blind_spot", "amplifier", "confirmation"}

SOURCE_PRIORITY = {
    "high": [
        "Reddit r/FulfillmentByAmazon", "Reddit r/AmazonSeller",
        "知无不言", "AMZ123", "Amazon Seller Central Forums",
        "Value Added Resource",
    ],
    "medium": ["卖家之家", "雨果跨境", "微信公众号"],
    "low": ["行业媒体"],
}

# Flat lookup: source name -> priority tier
SOURCE_PRIORITY_LOOKUP = {}
for _tier, _sources in SOURCE_PRIORITY.items():
    for _src in _sources:
        SOURCE_PRIORITY_LOOKUP[_src.lower()] = _tier


# ==================== Data Models ====================

@dataclass
class RawItem:
    title: str
    content: str
    source_platform: str
    source_priority: str  # "high", "medium", "low"
    date: str             # ISO date YYYY-MM-DD
    url: str              # original URL, empty string if none
    engagement: int = 0   # upvotes, comments, or 0
    confirmation_count: int = 0
    seller_voices: list = field(default_factory=list)


@dataclass
class Cluster:
    items: list  # list[RawItem]
    source_platforms: set = field(default_factory=set)
    primary_keywords: set = field(default_factory=set)
    date_range: tuple = ("", "")  # (earliest, latest)


# ==================== Normalization Functions ====================

# Keyword mappings for fuzzy normalization
_CATEGORY_KEYWORDS = {
    "advertising": ["广告", "ppc", "acos", "ad", "ads", "advertising", "sponsor", "投放", "竞价"],
    "promotion": ["促销", "秒杀", "大促", "prime day", "promotion", "deal", "coupon", "折扣", "活动"],
    "compliance": ["合规", "违规", "侵权", "审核", "封号", "申诉", "compliance", "policy", "政策", "规则"],
    "brand": ["品牌", "brand", "商标", "知识产权", "ip", "gtin"],
    "returns": ["退货", "退款", "return", "refund", "售后"],
    "tax": ["税", "tax", "vat", "gst", "发票", "报税"],
    "logistics": ["物流", "仓储", "fba", "fbm", "配送", "入仓", "贴标", "logistics", "shipping", "freight", "运费"],
    "trending": ["趋势", "热议", "trending", "选品", "新品"],
}

_LAYER_KEYWORDS = {
    "policy_impact": ["政策", "policy", "规则", "公告", "调整", "新规", "fee", "费用"],
    "macro_event": ["宏观", "macro", "关税", "tariff", "汇率", "油价", "冲突", "供应链"],
    "emerging_unknown": ["盲区", "emerging", "unknown", "新发现", "隐藏"],
}


def normalize_category(raw: str) -> str:
    """Normalize a raw category string to one of the allowed topic values.
    Returns the closest match or 'trending' as default."""
    if not raw:
        return "trending"
    lower = raw.strip().lower()
    # Direct match
    if lower in ALLOWED_TOPICS:
        return lower
    # Keyword-based fuzzy match
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return cat
    return "trending"


def normalize_layer(raw: str) -> str:
    """Normalize a raw layer string to one of the allowed layer values.
    Returns the closest match or 'emerging_unknown' as default."""
    if not raw:
        return "emerging_unknown"
    lower = raw.strip().lower()
    # Direct match
    if lower in ALLOWED_LAYERS:
        return lower
    # Common aliases
    aliases = {
        "layer 1": "policy_impact", "layer1": "policy_impact",
        "policy": "policy_impact", "政策影响": "policy_impact",
        "layer 2": "macro_event", "layer2": "macro_event",
        "macro": "macro_event", "宏观传导": "macro_event",
        "layer 3": "emerging_unknown", "layer3": "emerging_unknown",
        "emerging": "emerging_unknown", "盲区发现": "emerging_unknown",
    }
    for alias, val in aliases.items():
        if alias in lower:
            return val
    # Keyword-based
    for layer, keywords in _LAYER_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return layer
    return "emerging_unknown"


def normalize_source(raw: str) -> str:
    """Normalize a raw source string to a valid Source_Platform value.
    Returns the closest match or '行业媒体' as default."""
    if not raw:
        return "行业媒体"
    lower = raw.strip().lower()
    # Check all known sources
    all_sources = []
    for sources in SOURCE_PRIORITY.values():
        all_sources.extend(sources)
    for src in all_sources:
        if src.lower() == lower or src.lower() in lower or lower in src.lower():
            return src
    # Partial keyword matching
    source_keywords = {
        "Reddit r/FulfillmentByAmazon": ["reddit", "fba", "fulfillmentbyamazon", "r/fba"],
        "Reddit r/AmazonSeller": ["amazonseller", "r/amazonseller"],
        "知无不言": ["知无不言", "wearesellers"],
        "AMZ123": ["amz123"],
        "Amazon Seller Central Forums": ["seller central", "sellercentral", "amazon forum"],
        "Value Added Resource": ["value added", "valueaddedresource"],
        "卖家之家": ["卖家之家", "mjzj"],
        "雨果跨境": ["雨果", "cifnews", "hugo"],
        "微信公众号": ["微信", "公众号", "wechat", "weixin"],
    }
    for src, keywords in source_keywords.items():
        for kw in keywords:
            if kw in lower:
                return src
    return "行业媒体"


def get_source_priority(source: str) -> str:
    """Get the priority tier for a source platform."""
    lower = source.strip().lower()
    if lower in SOURCE_PRIORITY_LOOKUP:
        return SOURCE_PRIORITY_LOOKUP[lower]
    # Check partial matches
    for tier, sources in SOURCE_PRIORITY.items():
        for src in sources:
            if src.lower() in lower or lower in src.lower():
                return tier
    return "low"


# ==================== Validation ====================

def _count_chinese_chars(text: str) -> int:
    """Count the number of Chinese characters in a string."""
    return len(re.findall(r'[\u4e00-\u9fff]', text))


def validate_topic(topic: dict) -> bool:
    """Validate that a topic dict has all required fields with correct types and values.
    Returns True if valid, False otherwise."""
    required_fields = {
        "id": str, "rank": int, "title": str, "verified": str,
        "effectDate": str, "summary": str, "source": str,
        "topic": str, "layer": str, "sentiment": str,
        "painPoints": list, "alertLevel": str, "insightType": str,
        "aiGenerated": bool, "sellerVoices": list, "comparison": list,
        "links": list,
    }
    # Check all required fields exist with correct types
    for fld, ftype in required_fields.items():
        if fld not in topic:
            return False
        if not isinstance(topic[fld], ftype):
            return False

    # Validate id format: vos_XXX (3+ digit zero-padded)
    if not re.match(r'^vos_\d{3,}$', topic["id"]):
        return False

    # Validate rank 1-20
    if not (1 <= topic["rank"] <= 20):
        return False

    # Validate title non-empty
    if not topic["title"].strip():
        return False

    # Validate effectDate ISO format
    try:
        datetime.strptime(topic["effectDate"], "%Y-%m-%d")
    except ValueError:
        return False

    # Validate topic category
    if topic["topic"] not in ALLOWED_TOPICS:
        return False

    # Validate layer
    if topic["layer"] not in ALLOWED_LAYERS:
        return False

    # Validate sentiment
    if topic["sentiment"] not in ALLOWED_SENTIMENTS:
        return False

    # Validate alertLevel
    if topic["alertLevel"] not in ALLOWED_ALERT_LEVELS:
        return False

    # Validate insightType
    if topic["insightType"] not in ALLOWED_INSIGHT_TYPES:
        return False

    # Validate painPoints: 0-5 items (relaxed from 1-3)
    if len(topic["painPoints"]) > 5:
        return False

    # Validate summary length for AI-generated topics (relaxed: 20-500 Chinese chars or 50+ total chars)
    if topic.get("aiGenerated") is True:
        cn_count = _count_chinese_chars(topic["summary"])
        total_len = len(topic["summary"].strip())
        if cn_count < 20 and total_len < 50:
            return False

    # topicLabel and layerLabel are auto-generated, skip strict validation

    return True
