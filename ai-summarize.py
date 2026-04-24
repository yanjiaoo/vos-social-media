#!/usr/bin/env python3
"""
VOS AI 总结模块 - 使用 DeepSeek API
读取 vos-data.json 中没有 summary 或 summary 质量低的条目，
调用 DeepSeek 生成亚马逊账号经理视角的总结。
需要环境变量 DEEPSEEK_API_KEY
"""
import json
import os
import urllib.request
import sys

DEEPSEEK_API_URL = 'https://api.deepseek.com/chat/completions'

SYSTEM_PROMPT = """你是一位资深的亚马逊账号经理（AM），负责为团队整理卖家社媒热议话题的摘要。

你的任务是根据提供的话题标题和原始内容，生成一段简洁的中文摘要（100-200字），需要包含：
1. 核心事件：发生了什么
2. 影响范围：哪些卖家/站点受影响
3. 卖家情绪：卖家的主要反应和痛点
4. 对Amazon的启示：这对我们的业务意味着什么

风格要求：
- 新闻播报式，理性客观
- 不要用问句、叹号、营销语
- 包含具体数据（如有）
- 中文输出"""


def call_deepseek(title, content, api_key):
    """调用 DeepSeek API 生成总结"""
    user_msg = f"话题标题：{title}\n\n原始内容：{content}\n\n请生成摘要。"

    payload = json.dumps({
        'model': 'deepseek-chat',
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_msg},
        ],
        'max_tokens': 500,
        'temperature': 0.3,
    }).encode()

    req = urllib.request.Request(DEEPSEEK_API_URL, data=payload, headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'  [AI ERROR] {e}')
        return None


def main():
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('DEEPSEEK_API_KEY not set, skipping AI summarization')
        return

    with open('vos-data.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    updated = 0
    for item in data:
        # 跳过已有高质量摘要的手工条目
        if item.get('sellerVoices') and item.get('comparison'):
            continue

        summary = item.get('summary', '')
        title = item.get('title', '')

        # 如果摘要太短或就是标题重复，需要AI总结
        if len(summary) < 50 or summary.startswith(title[:20]):
            print(f'  AI总结: {title[:40]}...')
            ai_summary = call_deepseek(title, summary or title, api_key)
            if ai_summary:
                item['summary'] = ai_summary
                item['aiSummarized'] = True
                updated += 1

        if updated >= 10:  # 每次最多处理10条，控制API用量
            break

    with open('vos-data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f'AI总结完成: {updated} 条已更新')


if __name__ == '__main__':
    main()
