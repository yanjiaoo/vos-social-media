#!/usr/bin/env python3
"""
VOS AI Pipeline Orchestrator — Main entry point.
Collects seller news from Google News RSS and Value Added Resource,
then uses DeepSeek AI to generate intelligence briefings for Amazon AMs.
"""
import json
import os
import re
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


def title_tokens(text: str) -> set:
    """
    中英文混合分词。
    注意：中文标题没有空格，直接用 .split() 会得到一整串，
    导致关键词重叠永远为 0，防编造校验会把所有中文话题全部拒掉。
    这里对中文按 2 字滑窗切词，英文/数字按单词切。
    """
    t = (text or "").lower()
    tokens = set(re.findall(r'[a-z0-9]{2,}', t))
    for seg in re.findall(r'[\u4e00-\u9fff]+', t):
        for i in range(len(seg) - 1):
            tokens.add(seg[i:i + 2])
    return tokens


def same_url(a: str, b: str) -> bool:
    """URL 归一化比较（忽略 query 和结尾斜杠）"""
    if not a or not b:
        return False
    na = a.split('?')[0].rstrip('/')
    nb = b.split('?')[0].rstrip('/')
    return na == nb or a == b


def is_cjk(text: str) -> bool:
    return len(re.findall(r'[\u4e00-\u9fff]', text or "")) >= 4


# 领域通用词：几乎每条亚马逊卖家资讯都有，用来判断相关性毫无意义。
# 教训：曾经把阈值定成"共享2个字符2元组"，而"亚马逊卖家"本身就产生
# 亚马/马逊/逊卖/卖家 四个组，导致任意两条中文资讯都能通过校验，
# 一条讲资金周转的原文被挂到了讲社媒引流的话题上。
GENERIC_TERMS = (
    "亚马逊卖家跨境电商平台政策规则运营产品店铺账号listing费用成本影响"
    "美国欧洲市场业务服务数据增长变化调整新规上线通知社区反馈情绪"
    "amazon seller sellers marketplace ecommerce platform policy fee fees"
)


def build_generic_set() -> set:
    s = set()
    for w in GENERIC_TERMS.split():
        if re.fullmatch(r'[a-z]+', w):
            s.add(w)
    for seg in re.findall(r'[\u4e00-\u9fff]+', GENERIC_TERMS):
        for i in range(len(seg) - 1):
            s.add(seg[i:i + 2])
    return s


GENERIC_TOKENS = build_generic_set()

# AI 未给 sourceIndex 时，回退匹配所需的最少独特词数
FALLBACK_MIN_SCORE = 3


def distinctive_tokens(text: str, corpus_df: dict = None, n_docs: int = 0) -> set:
    """
    只保留有辨识度的词：剔除领域通用词，以及在素材库里出现过于频繁的词。
    这样"亚马逊""卖家""政策"之类不再贡献相关性分数。
    """
    toks = title_tokens(text) - GENERIC_TOKENS
    if corpus_df and n_docs >= 10:
        cutoff = max(3, int(n_docs * 0.15))
        toks = {t for t in toks if corpus_df.get(t, 0) <= cutoff}
    return toks


def build_corpus_df(rss_items) -> tuple:
    """统计每个词在多少条素材里出现过，用于识别高频通用词"""
    df = {}
    for it in rss_items:
        for t in title_tokens(it.title) | title_tokens(getattr(it, "content", "")[:300]):
            df[t] = df.get(t, 0) + 1
    return df, len(rss_items)


def title_match_score(ai_title: str, item, corpus_df=None, n_docs=0) -> int:
    """AI 标题与素材共享几个有辨识度的词"""
    ai = distinctive_tokens(ai_title, corpus_df, n_docs)
    src = distinctive_tokens(item.title, corpus_df, n_docs) | \
        distinctive_tokens(getattr(item, "content", "")[:400], corpus_df, n_docs)
    return len(ai & src)


def title_match_threshold(ai_title: str, item) -> int:
    """跨语言（中文标题 vs 英文原文）只能靠品牌词/数字对上，阈值放宽到 1"""
    return 2 if is_cjk(ai_title) == is_cjk(item.title) else 1


def load_deleted_titles() -> list:
    """读取人工删除名单（deleted-topics.json），这些标题不再收录"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "deleted-topics.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            titles = json.load(f).get("titles", [])
        if titles:
            print(f"  [Blocklist] 已加载 {len(titles)} 条人工删除记录")
        return titles
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"  [WARN] 删除名单读取失败: {e}")
        return []


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
        # 关键：喂给 AI 编号的列表 和 下游按 sourceIndex 取 URL 的列表，必须是同一个对象，
        # 否则编号会错位（曾因一个是 clean_items[:30]、一个是过滤后的 rss_items 而全部对不上）
        source_items = [it for it in clean_items if it.url and it.url.startswith("http")][:30]
        print(f"\n[Phase 5] Generating topics via DeepSeek... (可引用素材 {len(source_items)} 条)")
        self._init_deepseek()
        ai_topics = []
        try:
            ai_topics = self.deepseek.generate_topics(source_items)
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

        # 8. 用 AI 声明的 sourceIndex 取链接（不再靠关键词猜哪条素材）
        corpus_df, n_docs = build_corpus_df(rss_items)
        indexable = source_items          # 必须与喂给 AI 编号的列表完全一致
        linked_by_index = linked_by_fallback = 0
        for topic in ai_topics:
            self._enrich_topic(topic)
            topic["links"] = []          # 一律重建，不采纳 AI 自己写的 URL
            idx = topic.get("sourceIndex")
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = 0
            ai_title = topic.get("title", "")
            if 1 <= idx <= len(indexable):
                src = indexable[idx - 1]
                # 即便 AI 给了编号，也要复核内容是否真的对得上
                score = title_match_score(ai_title, src, corpus_df, n_docs)
                if score >= title_match_threshold(ai_title, src):
                    topic["links"] = [{"label": src.title, "url": src.url}]
                    linked_by_index += 1
                    continue
                print(f"  Skipping link (sourceIndex {idx} 内容对不上, 独特词×{score})")
                print(f"      话题: {ai_title[:40]}")
                print(f"      素材: {src.title[:40]}")
                continue
            if idx > len(indexable):
                print(f"  [WARN] sourceIndex {idx} 超出素材范围 1-{len(indexable)}: {ai_title[:36]}")

            # 兜底：AI 没给编号时，用独特词回退匹配。
            # 阈值取得比较严，且要求最优明显优于次优，避免又出现"随便挂一条"的情况。
            scored = sorted(
                ((title_match_score(ai_title, s, corpus_df, n_docs), s) for s in indexable),
                key=lambda x: -x[0])
            if scored and scored[0][0] >= FALLBACK_MIN_SCORE:
                runner_up = scored[1][0] if len(scored) > 1 else 0
                if scored[0][0] >= runner_up + 2:
                    topic["links"] = [{"label": scored[0][1].title, "url": scored[0][1].url}]
                    linked_by_fallback += 1

        print(f"  [Link] sourceIndex 命中 {linked_by_index} 条，独特词兜底 {linked_by_fallback} 条 "
              f"/ 共 {len(ai_topics)} 条")

        # 9. INCREMENTAL MERGE: keep ALL existing topics, only add new non-duplicate ones
        print("\n[Phase 8] Incremental merge (preserving existing topics)...")
        existing_titles = set()
        for e in existing:
            existing_titles.add(e.get("title", "").strip().lower())
            # Also add simplified version for fuzzy matching
            simplified = "".join(e.get("title", "").lower().split())
            existing_titles.add(simplified)

        # 人工删除过的话题：种进去重集合，防止下次又被生成出来
        for t in load_deleted_titles():
            existing_titles.add(t.strip().lower())
            existing_titles.add("".join(t.lower().split()))

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
                # Anti-fabrication: topic must have a real link
                has_links = topic.get("links") and any(
                    l.get("url", "").startswith("http") for l in topic["links"]
                )
                if not has_links:
                    print(f"  Skipping (no links): {title[:40]}")
                    continue

                # Anti-fabrication: URL 必须在素材中，且内容有辨识度地对得上
                topic_url = next((l["url"] for l in topic.get("links", []) if l.get("url", "").startswith("http")), "")
                url_verified = False
                matched_url = False
                for item in rss_items:
                    if same_url(item.url, topic_url):
                        matched_url = True
                        if title_match_score(title, item, corpus_df, n_docs) >= title_match_threshold(title, item):
                            url_verified = True
                        break

                topic.pop("_srcTitle", None)
                topic.pop("sourceIndex", None)
                if url_verified:
                    new_topics.append(topic)
                elif not matched_url:
                    print(f"  Skipping (URL not in RSS material): {title[:40]}")
                else:
                    print(f"  Skipping (内容与原文不相关): {title[:40]}")

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
