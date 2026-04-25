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
            context_text = "\n\n以下是从Google News和卖家论坛抓取的近期讨论，作为参考素材（请在生成话题时引用对应的URL作为links）：\n"
            for i, item in enumerate(context_items[:30]):
                context_text += f"\n{i+1}. [{item.source_platform}] {item.title}"
                if item.url:
                    context_text += f"\n   URL: {item.url}"
                if item.content:
                    context_text += f"\n   {item.content[:200]}"

        prompt = f"""你是一位资深的亚马逊Account Manager情报分析师。请基于你对亚马逊卖家社区的了解，生成20个当前最热门的亚马逊卖家话题。

**重要限制：所有话题必须与亚马逊（Amazon）平台直接相关。严禁包含eBay、Shopify、沃尔玛、速卖通等其他电商平台的话题，除非该话题直接影响亚马逊卖家的经营决策。**

{context_text}

## 话题分层要求
- Layer 1 - 政策影响 (policy_impact): 10个话题 — 亚马逊内部政策变动及卖家反应（如DD+7资金预留、Prime Day规则变更、广告付费方式变更、GWD上线、FBA费用调整等）
- Layer 2 - 宏观传导 (macro_event): 5个话题 — 外部事件通过供应链、成本、需求渠道影响卖家（如中东冲突影响航运、油价上涨推高物流成本、关税政策变化、汇率波动等）
- Layer 3 - 盲区发现 (emerging_unknown): 5个话题 — 卖家在社媒上活跃讨论但AM可能不知道的问题，代表真正的信息盲区

## 每个话题必须包含以下字段
- title: 中文标题（35-42字），陈述式，核心信息前置。要求：包含具体政策名称/数字/日期/影响范围等关键信息，让读者仅通过标题就能获取最多信息。标题中的数字必须来自参考素材原文，严禁编造数字。不要问句、叹号、"重磅"、"紧急"、"速看"等营销词。示例："亚马逊全球站3月12日执行DD+7资金预留新政，FBM卖家回款周期延长至20-30天"
- summary: **这是最重要的字段**。AM视角的深度影响分析（150-300个中文字符），必须包含以下四个要素：
  (a) 核心事件：发生了什么，具体政策/事件名称、生效日期、适用范围
  (b) 影响分析：谁受影响（哪些站点、哪类卖家、哪些品类），具体影响方式和程度（用数字量化，如费用增加多少、周期延长多少天）
  (c) 卖家反应：卖家社区的主流情绪和典型反馈（如"大量卖家反映现金流吃紧"、"中小卖家考虑转FBM"）
  (d) AM行动建议：对AM日常工作的意义，需要关注什么、如何帮助卖家应对
  **严禁**：摘要不能只是标题的重复或简单扩写，必须包含标题中没有的增量信息
- source: 信息来源平台（从以下选择：知无不言, AMZ123, Amazon Seller Central Forums, Value Added Resource, 卖家之家, 雨果跨境, 微信公众号, PPC Land, 行业媒体）
- topic: 话题分类（advertising/promotion/compliance/brand/returns/tax/logistics/trending）
- layer: 信息层级（policy_impact/macro_event/emerging_unknown）
- effectDate: 必须使用参考素材中的实际发布日期（YYYY-MM-DD格式）。如果话题来自参考素材，直接使用素材的日期。如果是你补充的话题，使用2026-04-01作为默认日期。严禁编造精确日期，严禁使用未来日期。
- sentiment: 卖家主导情绪（negative/neutral/positive）
- painPoints: 1-3个具体卖家痛点（如"现金流压力"、"合规成本增加"）
- alertLevel: 紧急程度（critical/high/normal）
- insightType: 洞察类型（blind_spot/amplifier/confirmation）
- links: 参考链接数组，每个包含label和url。**只能使用参考素材中提供的真实URL**。如果话题来自参考素材，必须引用素材的URL。如果没有对应的素材URL，links设为空数组[]。严禁编造URL，严禁放没有url字段的假链接。

## 摘要质量标准（极其重要）
优秀摘要示例：
"该政策统一全球卖家资金预留规则，要求所有订单货款在商品妥投后需额外冻结7天方可转入卖家可用余额。FBA订单回款周期普遍延长8-9天，FBM订单回款周期最长延长至20-30天，直接加剧卖家现金流压力，月销百万美金卖家额外资金占用超20万美金。"

不合格摘要示例（严禁出现）：
"亚马逊FBA费用调整提前生效  雨果跨境" — 这只是标题重复，没有任何分析价值

每个摘要必须：
- 至少150个中文字符
- 包含具体数字（费用金额、百分比、天数、日期等）
- 包含标题中没有的增量分析信息
- 对于政策变更：包含具体影响、时间线和前后对比
- 对于宏观事件：解释传导机制（如"油价+15% → 头程运费+$0.5/kg → FBA入仓成本上升"）

## 多样性要求
- 20个话题需覆盖至少4个不同的topic分类
- 至少包含3个不同的source来源

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
        prompt = f"""以下是从社媒抓取的亚马逊卖家话题标题，请将每个标题改写为高信息密度的陈述句，要求：
- 控制在35-42个中文字符
- 核心信息前置，包含具体政策名称、数字、日期、影响范围等关键信息
- 标题中的数字必须来自原标题，严禁编造数字
- 让读者仅通过标题就能获取最多信息
- 去掉问句、叹号、"重磅"、"紧急"、"速看"等营销词
- 中文输出
- 示例："亚马逊全球站3月12日执行DD+7资金预留新政，FBM卖家回款周期延长至20-30天"

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

    def enrich_short_summaries(self, topics: list, min_length: int = 80) -> list:
        """Use DeepSeek to rewrite short/low-quality summaries into detailed AM-perspective analysis."""
        needs_enrichment = []
        indices = []
        for i, t in enumerate(topics):
            summary = t.get("summary", "")
            if len(summary) < min_length:
                needs_enrichment.append(t)
                indices.append(i)

        if not needs_enrichment:
            print("  [DeepSeek] All summaries already high quality, skipping enrichment")
            return topics

        print(f"  [DeepSeek] Enriching {len(needs_enrichment)} short summaries...")

        topics_text = ""
        for i, item in enumerate(needs_enrichment):
            topics_text += f"\n{i+1}. 标题: {item.get('title', '')}\n   来源: {item.get('source', '')}\n   日期: {item.get('effectDate', '')}\n   分类: {item.get('topicLabel', '')}\n   当前摘要: {item.get('summary', '')}\n"

        prompt = f"""以下亚马逊卖家话题的摘要质量不够（太短或只是标题重复），请重写每个摘要。

要求：
1. 每个摘要150-300个中文字符
2. 从AM（Account Manager）视角分析，包含：发生了什么、谁受影响及如何影响（具体数字）、卖家情绪反应、对AM工作的意义
3. 包含具体数字、时间线、政策名称等关键信息
4. 严禁只是标题的重复或简单扩写，必须包含增量分析信息
5. 陈述式语气

需要重写的话题：
{topics_text}

请以JSON格式返回：{{"summaries": ["摘要1", "摘要2", ...]}}
顺序必须与输入一一对应。"""

        messages = [
            {"role": "system", "content": "你是亚马逊卖家社区情报分析专家，擅长从AM视角撰写高质量的政策影响分析摘要。严格返回JSON格式。"},
            {"role": "user", "content": prompt},
        ]

        try:
            result = self._call_api(messages)
            summaries = result.get("summaries", [])
            updated = 0
            for j, idx in enumerate(indices):
                if j < len(summaries) and summaries[j] and len(summaries[j]) > 50:
                    topics[idx]["summary"] = summaries[j]
                    updated += 1
            print(f"  [DeepSeek] Enriched {updated} summaries")
        except Exception as e:
            print(f"  [DeepSeek] Summary enrichment failed: {e}, keeping original summaries")

        return topics
