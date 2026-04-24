#!/usr/bin/env python3
"""
DeepSeek API client for topic generation and intelligence briefing synthesis.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

from models import (
    ALLOWED_TOPICS, ALLOWED_LAYERS, ALLOWED_SENTIMENTS,
    ALLOWED_ALERT_LEVELS, ALLOWED_INSIGHT_TYPES,
    TOPIC_LABELS, LAYER_LABELS,
    normalize_category, normalize_layer, normalize_source,
    get_source_priority,
)

API_URL = "https://api.deepseek.com/chat/completions"
MODEL_NAME = "deepseek-chat"


class DeepSeekClient:
    MAX_RETRIES = 3
    BACKOFF_DELAYS = [2, 4, 8]  # seconds

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        if not self.api_key:
            print("ERROR: DEEPSEEK_API_KEY environment variable is not set or empty.")
            sys.exit(1)

    def _call_api(self, messages: list, retry_count: int = 0) -> dict:
        """Call DeepSeek API with retry logic (exponential backoff)."""
        payload = json.dumps({
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 8000,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                print(f"  [DeepSeek] Malformed JSON response, retrying with simplified prompt...")
                if retry_count < self.MAX_RETRIES:
                    time.sleep(self.BACKOFF_DELAYS[min(retry_count, len(self.BACKOFF_DELAYS) - 1)])
                    # Simplify: add explicit JSON instruction
                    simplified = messages + [{"role": "user", "content": "请严格返回合法JSON格式，不要包含任何其他文字。"}]
                    return self._call_api(simplified, retry_count + 1)
                return {}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            print(f"  [DeepSeek] API error (attempt {retry_count + 1}): {e}")
            if retry_count < self.MAX_RETRIES:
                delay = self.BACKOFF_DELAYS[min(retry_count, len(self.BACKOFF_DELAYS) - 1)]
                print(f"  [DeepSeek] Retrying in {delay}s...")
                time.sleep(delay)
                return self._call_api(messages, retry_count + 1)
            raise

    def generate_topics(self, context_items: list = None) -> list:
        """Generate 20 curated seller hot topics using DeepSeek.
        context_items: list of RawItem used as grounding context."""
        # Build context from scraped items
        context_text = ""
        if context_items:
            context_text = "\n\n以下是从Reddit和中文卖家论坛抓取的近期讨论，作为参考素材：\n"
            for i, item in enumerate(context_items[:30]):
                context_text += f"\n{i+1}. [{item.source_platform}] {item.title}"
                if item.content:
                    context_text += f"\n   {item.content[:200]}"

        prompt = f"""你是一位资深的亚马逊Account Manager情报分析师。请基于你对亚马逊卖家社区的了解，生成20个当前最热门的亚马逊卖家话题。

{context_text}

## 话题分层要求
- Layer 1 - 政策影响 (policy_impact): 10个话题 — 亚马逊内部政策变动及卖家反应（如DD+7资金预留、Prime Day规则变更、广告付费方式变更、GWD上线、FBA费用调整等）
- Layer 2 - 宏观传导 (macro_event): 5个话题 — 外部事件通过供应链、成本、需求渠道影响卖家（如中东冲突影响航运、油价上涨推高物流成本、关税政策变化、汇率波动等）
- Layer 3 - 盲区发现 (emerging_unknown): 5个话题 — 卖家在社媒上活跃讨论但AM可能不知道的问题，代表真正的信息盲区

## 每个话题必须包含以下字段
- title: 中文标题，陈述式，核心信息前置
- summary: AM视角的影响分析摘要（100-400个中文字符），需包含：(a)发生了什么 (b)谁受影响、如何影响 (c)卖家情绪反应和具体抱怨 (d)对AM日常工作的意义
- source: 信息来源平台（从以下选择：Reddit r/FulfillmentByAmazon, Reddit r/AmazonSeller, 知无不言, AMZ123, Amazon Seller Central Forums, Value Added Resource, 卖家之家, 雨果跨境, 微信公众号）
- topic: 话题分类（advertising/promotion/compliance/brand/returns/tax/logistics/trending）
- layer: 信息层级（policy_impact/macro_event/emerging_unknown）
- effectDate: 相关日期（YYYY-MM-DD格式）
- sentiment: 卖家主导情绪（negative/neutral/positive）
- painPoints: 1-3个具体卖家痛点（如"现金流压力"、"合规成本增加"）
- alertLevel: 紧急程度（critical=大规模卖家抗议/账号封禁潮/系统宕机/突然费用大涨, high=有截止日期的政策变更/重大费用调整/合规要求变更, normal=一般话题）
- insightType: 洞察类型（blind_spot=内部渠道不可见, amplifier=内部已知但社媒揭示更严重, confirmation=与内部信号一致）
- links: 参考链接数组，每个包含label和url。如果没有可验证的链接，只放来源平台名称不放URL。严禁编造虚假URL。

## 质量标准
- 摘要质量参考："该政策统一全球卖家资金预留规则，要求所有订单货款在商品妥投后需额外冻结7天方可转入卖家可用余额。FBA订单回款周期普遍延长8-9天，FBM订单回款周期最长延长至20-30天，直接加剧卖家现金流压力，月销百万美金卖家额外资金占用超20万美金。"
- 对于政策变更话题，摘要需包含具体的政策影响、时间线和前后对比
- 对于宏观事件话题，需解释传导机制（如"油价+15% → 头程运费+$0.5/kg → FBA入仓成本上升"）
- 所有话题必须引用真实的、可验证的卖家社区讨论，不得编造内容
- 排除新手问题、服务商广告、纯情绪宣泄

## 多样性要求
- 20个话题需覆盖至少4个不同的topic分类
- 至少包含3个不同的source来源，其中至少1个高优先级来源和1个中优先级来源

请以JSON格式返回，结构为：{{"topics": [...]}}"""

        messages = [
            {"role": "system", "content": "你是亚马逊卖家社区情报分析专家，擅长从社交媒体中提取对Account Manager有价值的洞察。请严格按照JSON格式返回结果。"},
            {"role": "user", "content": prompt},
        ]

        print("  [DeepSeek] Generating 20 topics...")
        result = self._call_api(messages)
        topics = result.get("topics", [])

        if len(topics) < 20:
            print(f"  [DeepSeek] Warning: got {len(topics)} topics instead of 20")

        # Normalize and enrich each topic
        enriched = []
        for i, t in enumerate(topics):
            topic_cat = normalize_category(t.get("topic", ""))
            layer = normalize_layer(t.get("layer", ""))
            source = normalize_source(t.get("source", ""))

            enriched.append({
                "title": t.get("title", f"话题 {i+1}"),
                "summary": t.get("summary", ""),
                "source": source,
                "topic": topic_cat,
                "topicLabel": TOPIC_LABELS.get(topic_cat, "🔥 趋势"),
                "layer": layer,
                "layerLabel": LAYER_LABELS.get(layer, "🔍 盲区发现"),
                "effectDate": t.get("effectDate", ""),
                "sentiment": t.get("sentiment", "neutral") if t.get("sentiment") in ALLOWED_SENTIMENTS else "neutral",
                "painPoints": t.get("painPoints", ["待分析"])[:3] or ["待分析"],
                "alertLevel": t.get("alertLevel", "normal") if t.get("alertLevel") in ALLOWED_ALERT_LEVELS else "normal",
                "insightType": t.get("insightType", "confirmation") if t.get("insightType") in ALLOWED_INSIGHT_TYPES else "confirmation",
                "aiGenerated": True,
                "links": t.get("links", []),
                "sellerVoices": [],
                "comparison": [],
                "verified": "unconfirmed",
            })

        print(f"  [DeepSeek] Generated {len(enriched)} valid topics")
        return enriched

    def generate_briefing(self, cluster_items: list) -> dict:
        """Generate a consolidated intelligence briefing for a multi-source cluster."""
        context = "\n".join([
            f"[{item.source_platform}] {item.title}\n{item.content[:200]}"
            for item in cluster_items
        ])
        sources = list(set(item.source_platform for item in cluster_items))

        prompt = f"""以下是来自不同平台关于同一话题的讨论：

{context}

涉及平台：{', '.join(sources)}

请生成一份综合情报简报，包含：
1. headline: 中性、事实性的标题，概括核心事件
2. briefing: 200-400字的AM视角综合摘要，融合所有来源的洞察
3. sourceBreakdown: 每个平台的独特视角（JSON对象，key为平台名，value为该平台的独特角度描述）
4. sellerConsensus: 跨平台卖家是否对影响达成共识，还是存在分歧观点

请以JSON格式返回。"""

        messages = [
            {"role": "system", "content": "你是跨平台卖家情报分析专家。请严格按照JSON格式返回结果。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = self._call_api(messages)
            return {
                "headline": result.get("headline", ""),
                "briefing": result.get("briefing", ""),
                "sourceBreakdown": result.get("sourceBreakdown", {}),
                "sellerConsensus": result.get("sellerConsensus", ""),
            }
        except Exception as e:
            print(f"  [DeepSeek] Briefing generation failed: {e}")
            return {
                "headline": cluster_items[0].title if cluster_items else "",
                "briefing": "",
                "sourceBreakdown": {},
                "sellerConsensus": "",
            }

    def optimize_titles(self, topics: list) -> list:
        """Use DeepSeek to rewrite clickbait titles into factual news-style titles."""
        titles = [t.get("title", "") for t in topics if t.get("title")]
        if not titles:
            return topics

        titles_text = "\n".join([f"{i+1}. {t}" for i, t in enumerate(titles)])
        prompt = f"""以下是从社媒抓取的亚马逊卖家话题标题，很多使用了博眼球的用词（问句、叹号、夸张语气）。
请将每个标题改写为新闻播报式的陈述句，要求：
- 核心信息前置
- 去掉问句、叹号、"重磅"、"紧急"、"速看"等营销词
- 保留具体的政策名称、数字、日期等关键信息
- 中文输出

原始标题：
{titles_text}

请以JSON格式返回：{{"titles": ["改写后的标题1", "改写后的标题2", ...]}}"""

        messages = [
            {"role": "system", "content": "你是专业的新闻编辑，擅长将社媒标题改写为客观、信息密度高的新闻标题。严格返回JSON格式。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = self._call_api(messages)
            new_titles = result.get("titles", [])
            if len(new_titles) == len(topics):
                for i, topic in enumerate(topics):
                    if new_titles[i] and len(new_titles[i]) > 5:
                        topic["title"] = new_titles[i]
                print(f"  [DeepSeek] Optimized {len(new_titles)} titles")
            else:
                print(f"  [DeepSeek] Title count mismatch ({len(new_titles)} vs {len(topics)}), skipping optimization")
        except Exception as e:
            print(f"  [DeepSeek] Title optimization failed: {e}, keeping original titles")

        return topics
