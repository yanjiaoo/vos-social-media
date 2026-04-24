#!/usr/bin/env python3
"""
One-time script: Use DeepSeek to enrich short/low-quality summaries in vos-data.json.
Summaries shorter than MIN_QUALITY_LENGTH will be rewritten with AM-perspective analysis.

Usage:
  set DEEPSEEK_API_KEY=sk-xxx
  python vos-pipeline/enrich_summaries.py
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MIN_QUALITY_LENGTH = 80  # summaries shorter than this get rewritten

def call_deepseek(api_key: str, messages: list, retries: int = 3) -> dict:
    payload = json.dumps({
        "model": MODEL,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 6000,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }, method="POST")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            print(f"  Attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
    return {}

def main():
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: Set DEEPSEEK_API_KEY environment variable first.")
        sys.exit(1)

    with open("vos-data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Find topics needing enrichment
    needs_enrichment = []
    for item in data:
        summary_len = len(item.get("summary", ""))
        if summary_len < MIN_QUALITY_LENGTH:
            needs_enrichment.append(item)

    if not needs_enrichment:
        print("All summaries are already high quality. Nothing to do.")
        return

    print(f"Found {len(needs_enrichment)} topics needing summary enrichment:")
    for item in needs_enrichment:
        print(f"  TOP{item['rank']} [{len(item['summary'])}c] {item['title'][:50]}")

    # Build batch prompt
    topics_text = ""
    for i, item in enumerate(needs_enrichment):
        topics_text += f"\n{i+1}. 标题: {item['title']}\n   来源: {item.get('source', '')}\n   日期: {item.get('effectDate', '')}\n   分类: {item.get('topicLabel', '')}\n   当前摘要: {item.get('summary', '')}\n"

    prompt = f"""你是一位资深的亚马逊Account Manager情报分析师。以下话题的"影响说明"摘要质量不够，需要你重写。

要求：
1. 每个摘要150-300个中文字符（英文话题也用中文写摘要）
2. 从AM（Account Manager）视角分析，包含：
   - 发生了什么（核心事件/政策变动）
   - 谁受影响、如何影响（具体到卖家类型、站点、品类）
   - 卖家情绪和反应（基于你对卖家社区的了解）
   - 对AM日常工作的意义（需要关注什么、如何应对）
3. 包含具体数字、时间线、政策名称等关键信息
4. 陈述式语气，不要问句、感叹号
5. 参考质量标准："该政策统一全球卖家资金预留规则，要求所有订单货款在商品妥投后需额外冻结7天方可转入卖家可用余额。FBA订单回款周期普遍延长8-9天，FBM订单回款周期最长延长至20-30天，直接加剧卖家现金流压力，月销百万美金卖家额外资金占用超20万美金。"

需要重写的话题：
{topics_text}

请以JSON格式返回：{{"summaries": ["摘要1", "摘要2", ...]}}
摘要顺序必须与输入话题顺序一一对应。"""

    messages = [
        {"role": "system", "content": "你是亚马逊卖家社区情报分析专家，擅长从AM视角撰写高质量的政策影响分析摘要。严格返回JSON格式。"},
        {"role": "user", "content": prompt},
    ]

    print("\nCalling DeepSeek to generate enriched summaries...")
    result = call_deepseek(api_key, messages)
    summaries = result.get("summaries", [])

    if len(summaries) != len(needs_enrichment):
        print(f"ERROR: Expected {len(needs_enrichment)} summaries, got {len(summaries)}")
        if not summaries:
            sys.exit(1)

    # Apply enriched summaries
    updated = 0
    for i, item in enumerate(needs_enrichment):
        if i < len(summaries) and summaries[i] and len(summaries[i]) > 50:
            old_len = len(item["summary"])
            item["summary"] = summaries[i]
            new_len = len(item["summary"])
            print(f"  TOP{item['rank']}: {old_len}c -> {new_len}c")
            updated += 1

    # Write back
    with open("vos-data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Updated {updated} summaries in vos-data.json")

if __name__ == "__main__":
    main()
