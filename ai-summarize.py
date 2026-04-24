#!/usr/bin/env python3
"""VOS AI Summarize - DeepSeek API"""
import json, os, urllib.request

DEEPSEEK_API_URL = 'https://api.deepseek.com/chat/completions'

SYSTEM_PROMPT = """You are a senior Amazon Account Manager. Summarize the following seller community topic in Chinese (100-200 chars).
Include: 1) What happened 2) Who is affected 3) Seller sentiment 4) Implications for Amazon.
Style: factual, no questions/exclamation marks, include specific data if available. Output in Chinese only."""

def call_deepseek(title, content, api_key):
    user_msg = f"Topic: {title}\nContent: {content}\nGenerate summary in Chinese."
    payload = json.dumps({
        'model': 'deepseek-chat',
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': user_msg},
        ],
        'max_tokens': 500, 'temperature': 0.3,
    }).encode()
    req = urllib.request.Request(DEEPSEEK_API_URL, data=payload, headers={
        'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}',
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'  [AI ERROR] {e}')
        return None

def needs_ai(item):
    """Determine if this item needs AI summarization"""
    if item.get('aiSummarized'):
        return False
    # Skip items with rich manual content (comparison tables)
    if item.get('comparison') and len(item.get('comparison', [])) > 0:
        return False
    # All others need AI if summary is short or just repeats title
    summary = item.get('summary', '')
    if len(summary) > 200:
        return False
    return True

def main():
    api_key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not api_key:
        print('DEEPSEEK_API_KEY not set, skipping')
        return
    print('DeepSeek API key found, starting AI summarization...')

    with open('vos-data.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    updated = 0
    for item in data:
        if not needs_ai(item):
            print(f'  SKIP: {item.get("title","")[:40]}')
            continue
        title = item.get('title', '')
        content = item.get('summary', '') or title
        print(f'  AI: {title[:40]}...')
        result = call_deepseek(title, content, api_key)
        if result:
            item['summary'] = result
            item['aiSummarized'] = True
            updated += 1
        if updated >= 15:
            break

    with open('vos-data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'AI summarization done: {updated} items updated')

if __name__ == '__main__':
    main()
