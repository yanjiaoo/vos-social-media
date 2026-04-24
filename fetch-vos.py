#!/usr/bin/env python3
"""
VOS From Social Media - 高质量卖家热议抓取
数据源：
  1. Reddit r/FulfillmentByAmazon (免费JSON API，高质量英文讨论)
  2. Reddit r/AmazonSeller
  3. Google News RSS 索引的中文卖家论坛（知无不言/卖家之家/AMZ123/雨果跨境）
筛选：关键词评分 + 互动量排序 → 只保留真正的热议话题
"""
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# ==================== Reddit 抓取 ====================
REDDIT_SUBS = [
    'FulfillmentByAmazon',
    'AmazonSeller',
]

def fetch_reddit(subreddit, sort='hot', limit=25):
    """从 Reddit 子版块抓取帖子（免费 JSON API）"""
    url = f'https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}&t=month'
    items = []
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'VOSBot/1.0 (Seller Learning Hub)'
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for post in data.get('data', {}).get('children', []):
            d = post.get('data', {})
            if d.get('stickied'):
                continue
            items.append({
                'title': d.get('title', ''),
                'content': (d.get('selftext', '') or '')[:500],
                'source': f'Reddit r/{subreddit}',
                'url': f'https://www.reddit.com{d.get("permalink", "")}',
                'date': datetime.fromtimestamp(d.get('created_utc', 0), tz=timezone.utc).strftime('%Y-%m-%d'),
                'score': d.get('score', 0),
                'comments': d.get('num_comments', 0),
                'upvote_ratio': d.get('upvote_ratio', 0),
                'lang': 'en',
            })
    except Exception as e:
        print(f'  [WARN] Reddit r/{subreddit}: {e}')
    return items


# ==================== 中文社媒 Google News RSS ====================
CN_QUERIES = [
    {'q': '知无不言 亚马逊 卖家 政策 OR 费用 OR 广告 OR 账号', 'source': '知无不言'},
    {'q': '卖家之家 亚马逊 政策 OR 公告 OR 变动 OR 费用', 'source': '卖家之家'},
    {'q': 'AMZ123 亚马逊 卖家 政策 OR 热议 OR 费用', 'source': 'AMZ123'},
    {'q': '雨果跨境 亚马逊 卖家 政策 OR 费用 OR 广告', 'source': '雨果跨境'},
    {'q': '亚马逊卖家 政策 OR 费用 OR 广告 变动 site:mp.weixin.qq.com', 'source': '公众号'},
    {'q': '亚马逊 FBA OR 广告 OR 账号 OR 封号 卖家 2026', 'source': '行业媒体'},
]

def fetch_cn_rss(query, max_items=8):
    url = f'https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    items = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'VOSBot/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        for item in root.findall('.//item')[:max_items]:
            title_raw = item.findtext('title', '')
            parts = title_raw.rsplit(' - ', 1)
            title = html.unescape(parts[0].strip())
            source = parts[1].strip() if len(parts) > 1 else ''
            desc = re.sub(r'<[^>]+>', '', html.unescape(item.findtext('description', ''))).strip()[:500]
            pub_date = item.findtext('pubDate', '')
            link = item.findtext('link', '')
            try:
                dt = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %Z')
            except Exception:
                dt = datetime.now(timezone.utc)
            items.append({
                'title': title, 'content': desc, 'source': source,
                'url': link, 'date': dt.strftime('%Y-%m-%d'),
                'score': 0, 'comments': 0, 'lang': 'zh',
            })
    except Exception as e:
        print(f'  [WARN] CN RSS: {e}')
    return items


# ==================== 热度评分 ====================
HOT_KEYWORDS = {
    # 高权重：政策变动（卖家最关心）
    'policy': 3, 'fee': 3, 'increase': 3, 'change': 3, 'new rule': 3,
    '政策': 3, '费用': 3, '涨价': 3, '变动': 3, '新规': 3, '调整': 3,
    # 高权重：账号安全
    'suspend': 3, 'deactivat': 3, 'appeal': 3, 'banned': 3,
    '封号': 3, '申诉': 3, '违规': 3, '审核': 3,
    # 中权重：运营核心
    'FBA': 2, 'PPC': 2, 'advertising': 2, 'inventory': 2, 'listing': 2,
    '广告': 2, '库存': 2, 'listing': 2, '物流': 2, '仓储': 2,
    # 中权重：行业事件
    'tariff': 2, 'protest': 2, 'lawsuit': 2,
    '关税': 2, '抗议': 2, '诉讼': 2, '合规': 2,
    # 低权重：一般运营
    'review': 1, 'ranking': 1, 'conversion': 1, 'return': 1,
    '评价': 1, '排名': 1, '转化': 1, '退货': 1, '促销': 1,
}

EXCLUDE_PATTERNS = [
    r'how to (start|sell|begin)', r'beginner', r'newbie',
    r'新手', r'入门', r'教程', r'如何开店',
    r'stock price', r'投资理财', r'Prime Video',
]

def calc_relevance_score(item):
    """计算话题热度评分"""
    text = (item['title'] + ' ' + item.get('content', '')).lower()

    # 排除无关内容
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return -1

    score = 0
    # 关键词评分
    for kw, weight in HOT_KEYWORDS.items():
        if kw.lower() in text:
            score += weight

    # Reddit 互动量加分
    score += min(item.get('score', 0) / 50, 5)  # 最多加5分
    score += min(item.get('comments', 0) / 20, 3)  # 最多加3分

    return score


def is_chinese(text):
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    return cn > len(text) * 0.2


def clean_title(title):
    t = title.strip()
    t = re.sub(r'[?？!！]+$', '', t)
    t = re.sub(r'^(重磅|突发|独家|最新|速看|必看|震惊|刚刚|快讯)[：:|\s]*', '', t)
    t = re.sub(r'【[^】]*】\s*', '', t)
    t = re.sub(r'\s*[|丨｜—]\s*[\w\u4e00-\u9fff]+\s*$', '', t)
    return t.strip()


def main():
    print('=== VOS From Social Media 高质量抓取 ===\n')
    all_items = []
    seen = set()

    # 1. Reddit 抓取
    for sub in REDDIT_SUBS:
        print(f'[Reddit r/{sub}]')
        posts = fetch_reddit(sub, 'hot', 25)
        print(f'  抓取 {len(posts)} 条')
        for p in posts:
            if p['title'] not in seen:
                seen.add(p['title'])
                all_items.append(p)

    # 2. 中文社媒抓取
    for q_config in CN_QUERIES:
        print(f'[{q_config["source"]}] {q_config["q"][:40]}...')
        items = fetch_cn_rss(q_config['q'], 8)
        for item in items:
            if item['title'] not in seen and is_chinese(item['title']):
                seen.add(item['title'])
                if not item['source']:
                    item['source'] = q_config['source']
                all_items.append(item)

    print(f'\n总计抓取: {len(all_items)} 条')

    # 3. 评分排序
    scored = []
    for item in all_items:
        score = calc_relevance_score(item)
        if score > 0:
            item['relevance_score'] = score
            scored.append(item)

    scored.sort(key=lambda x: x['relevance_score'], reverse=True)
    top_items = scored[:15]
    print(f'评分筛选后: {len(top_items)} 条\n')

    # 4. 读取现有手工数据（保留高质量手工内容）
    existing = []
    try:
        with open('vos-data.json', 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        pass

    # 保留有 sellerVoices 或 comparison 的手工条目
    manual_items = [item for item in existing if item.get('sellerVoices') or item.get('comparison')]
    manual_titles = set(item.get('title', '') for item in manual_items)

    # 5. 合并：手工条目优先 + 自动抓取补充
    final = []
    rank = 1

    # 先放手工条目
    for item in manual_items:
        item['rank'] = rank
        final.append(item)
        rank += 1

    # 再放自动抓取的（去掉与手工重复的）
    for item in top_items:
        title = clean_title(item['title'])
        if title in manual_titles or len(title) < 5:
            continue
        final.append({
            'id': f'vos_auto_{rank:03d}',
            'rank': rank,
            'title': title,
            'verified': 'unconfirmed',
            'effectDate': item['date'],
            'summary': item.get('content', '')[:300],
            'source': item['source'],
            'sellerVoices': [],
            'comparison': [],
            'links': [{'label': item['source'], 'url': item['url']}],
        })
        rank += 1

    # 6. 写入
    with open('vos-data.json', 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    print(f'=== 完成！{len(manual_items)} 条手工 + {len(final) - len(manual_items)} 条自动 = {len(final)} 条 ===')


if __name__ == '__main__':
    main()
