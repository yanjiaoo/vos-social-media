#!/usr/bin/env python3
"""
VOS From Social Media v4
第一步：Reddit 评论区深度抓取（评论数+确认词权重）
第三步：Amazon Seller Forums 交叉核实（自动标记 verified）
"""
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime, timezone
from urllib.parse import quote
import time

HEADERS = {'User-Agent': 'VOSv4/1.0 (Seller Learning Hub)'}

# ==================== 议题分类 ====================
TOPICS = {
    'advertising': {'label': '📢 广告', 'keywords': ['广告','PPC','ACoS','ROAS','竞价','投放','advertising','ad spend','sponsored','campaign','广告费','扣款','ad fee','credit card']},
    'promotion': {'label': '🏷️ 促销', 'keywords': ['促销','秒杀','Prime Day','coupon','deal','lightning deal','划线价','折扣','BD','LD','大促','定价','pricing']},
    'compliance': {'label': '⚖️ 合规', 'keywords': ['合规','违规','变体','Vine','评论','侵权','IP','compliance','violation','infringement','审核','review manipulation']},
    'brand': {'label': '🏢 品牌', 'keywords': ['品牌','Brand Registry','品牌备案','品牌审核','brand','transparency','品牌信誉']},
    'returns': {'label': '📦 退货', 'keywords': ['退货','return','APRL','退货标签','退货率','退款','refund','return rate']},
    'tax': {'label': '💰 税务', 'keywords': ['税务','VAT','VCS','增值税','所得税','PN15','tax','报税']},
    'logistics': {'label': '🚚 物流', 'keywords': ['物流','FBA','入仓','仓储','库存','贴标','货代','GWD','fulfillment','配送','头程','low inventory','inbound','placement fee']},
    'trending': {'label': '🔥 趋势', 'keywords': ['选品','趋势','品类','转型','新品','trending','niche']},
}

# 确认词（评论区出现这些词说明话题真实性高）
CONFIRMATION_WORDS = [
    'confirmed', 'happening to me too', 'same here', 'amazon confirmed',
    'can confirm', 'just got this', 'me too', 'same issue', 'also affected',
    'got the same', 'experiencing this', 'just happened', 'verified',
]

EXCLUDE = ['giveaway', 'coupon code', 'how to start', 'beginner', 'stock price', 'Prime Video']

# ==================== Reddit 深度抓取 ====================
def fetch_reddit_with_comments(subreddit, sort='hot', limit=25):
    """抓取 Reddit 帖子 + 评论区分析"""
    url = f'https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}&t=month'
    items = []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for post in data.get('data', {}).get('children', []):
            d = post.get('data', {})
            if d.get('stickied') or d.get('is_self') is False:
                continue

            title = d.get('title', '')
            selftext = (d.get('selftext', '') or '')[:500]
            num_comments = d.get('num_comments', 0)
            score = d.get('score', 0)
            permalink = d.get('permalink', '')
            created = d.get('created_utc', 0)

            # 跳过低互动帖子
            if num_comments < 3 and score < 5:
                continue

            item = {
                'title': title,
                'content': selftext,
                'source': f'Reddit r/{subreddit}',
                'url': f'https://www.reddit.com{permalink}',
                'date': datetime.fromtimestamp(created, tz=timezone.utc).strftime('%Y-%m-%d'),
                'score': score,
                'num_comments': num_comments,
                'confirmation_count': 0,
                'top_comments': [],
                'lang': 'en',
            }

            # 深度抓取评论区（高互动帖子）
            if num_comments >= 10:
                comments_data = fetch_reddit_comments(permalink)
                item['confirmation_count'] = comments_data['confirmations']
                item['top_comments'] = comments_data['top_comments']

            items.append(item)
            time.sleep(0.5)  # 避免被限流

    except Exception as e:
        print(f'  [WARN] Reddit r/{subreddit}: {e}')
    return items


def fetch_reddit_comments(permalink):
    """抓取帖子评论区，统计确认词和提取高赞评论"""
    url = f'https://www.reddit.com{permalink}.json?limit=50&sort=top'
    result = {'confirmations': 0, 'top_comments': []}
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        if len(data) < 2:
            return result

        comments = data[1].get('data', {}).get('children', [])
        for comment in comments[:30]:
            cd = comment.get('data', {})
            body = (cd.get('body', '') or '').lower()
            comment_score = cd.get('score', 0)

            # 统计确认词
            for cw in CONFIRMATION_WORDS:
                if cw in body:
                    result['confirmations'] += 1
                    break

            # 提取高赞评论（作为卖家声音）
            if comment_score >= 5 and len(result['top_comments']) < 3:
                clean_body = cd.get('body', '')[:200]
                if clean_body and len(clean_body) > 20:
                    result['top_comments'].append({
                        'content': clean_body,
                        'score': comment_score,
                    })

    except Exception as e:
        pass  # 评论抓取失败不影响主流程
    return result


# ==================== Amazon Seller Forums 核实 ====================
def check_amazon_forums(keywords_list):
    """检查 Amazon Seller Forums 是否有相关官方信息"""
    verified_topics = set()
    for keywords in keywords_list[:5]:  # 限制请求数
        query = f'site:sellercentral.amazon.com {keywords}'
        url = f'https://news.google.com/rss/search?q={quote(query)}&hl=en&gl=US&ceid=US:en'
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            items = root.findall('.//item')
            if len(items) > 0:
                verified_topics.add(keywords)
        except Exception:
            pass
        time.sleep(0.3)
    return verified_topics


# ==================== 中文社媒 ====================
CN_QUERIES = [
    {'q': '知无不言 亚马逊 卖家 政策 OR 广告 OR 合规 OR 封号', 'source': '知无不言'},
    {'q': '卖家之家 亚马逊 政策 OR 费用 OR 广告 OR 促销', 'source': '卖家之家'},
    {'q': 'AMZ123 亚马逊 卖家 政策 OR 热议 OR 广告 OR 费用', 'source': 'AMZ123'},
    {'q': '雨果跨境 亚马逊 卖家 政策 OR 广告 OR 合规 OR FBA', 'source': '雨果跨境'},
    {'q': '亚马逊卖家 政策 OR 广告 OR 合规 变动 site:mp.weixin.qq.com', 'source': '公众号'},
    {'q': '亚马逊 FBA OR 退货 OR 品牌 OR 税务 卖家 2026', 'source': '行业媒体'},
]

def fetch_cn_rss(query, max_items=8):
    url = f'https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    items = []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        for item in root.findall('.//item')[:max_items]:
            title_raw = item.findtext('title', '')
            parts = title_raw.rsplit(' - ', 1)
            title = html.unescape(parts[0].strip())
            source = parts[1].strip() if len(parts) > 1 else ''
            desc = re.sub(r'<[^>]+>', '', html.unescape(item.findtext('description', ''))).strip()[:400]
            link = item.findtext('link', '')
            pub_date = item.findtext('pubDate', '')
            try:
                dt = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %Z')
            except Exception:
                dt = datetime.now(timezone.utc)
            items.append({
                'title': title, 'content': desc, 'source': source,
                'url': link, 'date': dt.strftime('%Y-%m-%d'),
                'score': 0, 'num_comments': 0, 'confirmation_count': 0,
                'top_comments': [], 'lang': 'zh',
            })
    except Exception as e:
        print(f'  [WARN] {e}')
    return items


# ==================== 评分 + 分类 ====================
def classify_topic(title, content):
    text = (title + ' ' + content).lower()
    scores = {}
    for tid, cfg in TOPICS.items():
        s = sum(1 for kw in cfg['keywords'] if kw.lower() in text)
        if s > 0:
            scores[tid] = s
    return max(scores, key=scores.get) if scores else 'trending'


def calc_score(item):
    text = (item['title'] + ' ' + item.get('content', '')).lower()
    for pat in EXCLUDE:
        if pat.lower() in text:
            return -1

    score = 0
    # Reddit 互动量
    score += min(item.get('score', 0) / 20, 5)
    score += min(item.get('num_comments', 0) / 10, 5)
    # 确认词加权（核心创新点）
    score += item.get('confirmation_count', 0) * 3
    # 关键词
    alert_words_3 = ['suspension','unfair','protest','封号','不公','抗议','强制','新规','新政']
    alert_words_2 = ['policy change','fee increase','mandatory','政策变动','费用上涨','紧急','调整']
    for w in alert_words_3:
        if w.lower() in text: score += 3
    for w in alert_words_2:
        if w.lower() in text: score += 2
    return score


def is_chinese(text):
    return len(re.findall(r'[\u4e00-\u9fff]', text)) > len(text) * 0.15


def clean_title(title):
    t = title.strip()
    t = re.sub(r'[?？!！]+$', '', t)
    t = re.sub(r'^(重磅|突发|独家|最新|速看|必看|震惊|刚刚|快讯)[：:|\s]*', '', t)
    t = re.sub(r'【[^】]*】\s*', '', t)
    t = re.sub(r'\s*[|丨｜—]\s*[\w\u4e00-\u9fff]+\s*$', '', t)
    return t.strip()


def main():
    print('=== VOS v4: Reddit深度抓取 + 官方核实 ===\n')
    all_items = []
    seen = set()

    # 1. Reddit 深度抓取
    print('[Reddit 深度抓取（含评论区）]')
    for sub in ['FulfillmentByAmazon', 'AmazonSeller']:
        print(f'  r/{sub}...')
        posts = fetch_reddit_with_comments(sub, 'hot', 25)
        for p in posts:
            if p['title'] not in seen:
                seen.add(p['title'])
                p['engine'] = 'social'
                all_items.append(p)
        print(f'    {len(posts)} 条（含评论区分析）')

    # 2. 中文社媒
    print('\n[中文社媒雷达]')
    for cfg in CN_QUERIES:
        print(f'  {cfg["source"]}...')
        for item in fetch_cn_rss(cfg['q'], 8):
            if item['title'] not in seen and is_chinese(item['title']):
                seen.add(item['title'])
                item['engine'] = 'social'
                if not item['source']:
                    item['source'] = cfg['source']
                all_items.append(item)

    print(f'\n总计: {len(all_items)} 条')

    # 3. 评分+分类+过滤
    scored = []
    for item in all_items:
        s = calc_score(item)
        if s <= 0:
            continue
        # 只保留2026年
        if not item.get('date', '').startswith('2026'):
            continue
        item['alert_score'] = s
        item['topic'] = classify_topic(item['title'], item.get('content', ''))
        scored.append(item)

    scored.sort(key=lambda x: x['alert_score'], reverse=True)
    print(f'评分筛选后: {len(scored)} 条')

    # 4. Amazon Seller Forums 核实
    print('\n[Amazon Seller Forums 核实]')
    # 提取高分话题的关键词去核实
    verify_queries = []
    for item in scored[:10]:
        words = item['title'].split()[:5]
        if item['lang'] == 'en':
            verify_queries.append(' '.join(words))
    verified_set = check_amazon_forums(verify_queries)
    print(f'  核实到 {len(verified_set)} 个话题有官方信息')

    # 5. 保留手工条目
    existing = []
    try:
        with open('vos-data.json', 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        pass
    manual = [item for item in existing if item.get('sellerVoices') or item.get('comparison')]
    manual_titles = set(item.get('title', '') for item in manual)

    # 6. 合并输出
    final = []
    rank = 1
    for item in manual:
        item['rank'] = rank
        final.append(item)
        rank += 1

    for item in scored[:20]:
        title = clean_title(item['title'])
        if title in manual_titles or len(title) < 5:
            continue

        topic_cfg = TOPICS.get(item['topic'], {})
        engine_label = '📡 社媒雷达' if item.get('engine') == 'social' else '📋 官方情报'

        # 核实标记
        verified = 'unconfirmed'
        for vq in verified_set:
            if any(w.lower() in title.lower() for w in vq.split()[:3]):
                verified = 'official'
                break

        # 构建卖家声音（来自 Reddit 高赞评论）
        seller_voices = []
        for tc in item.get('top_comments', [])[:3]:
            seller_voices.append({
                'source': f'Reddit (👍{tc["score"]})',
                'content': tc['content'],
            })

        # 确认度标注
        conf = item.get('confirmation_count', 0)
        conf_label = ''
        if conf >= 5:
            conf_label = f'🔥 {conf}人确认'
        elif conf >= 2:
            conf_label = f'✓ {conf}人确认'

        final.append({
            'id': f'vos_auto_{rank:03d}',
            'rank': rank,
            'title': title,
            'verified': verified,
            'effectDate': item['date'],
            'summary': item.get('content', '')[:300],
            'source': item['source'],
            'topic': item['topic'],
            'topicLabel': topic_cfg.get('label', ''),
            'engineLabel': engine_label,
            'alertScore': item['alert_score'],
            'confirmationLabel': conf_label,
            'sellerVoices': seller_voices,
            'comparison': [],
            'links': [{'label': item['source'], 'url': item['url']}],
        })
        rank += 1

    
    # Preserve AI summaries from existing data
    existing_ai = {}
    try:
        with open('vos-data.json', 'r', encoding='utf-8') as f:
            old_data = json.load(f)
        for old_item in old_data:
            if old_item.get('aiSummarized'):
                existing_ai[old_item.get('title', '')] = old_item.get('summary', '')
    except Exception:
        pass

    # Apply preserved AI summaries to matching items
    for item in final:
        title = item.get('title', '')
        if title in existing_ai and not item.get('aiSummarized'):
            item['summary'] = existing_ai[title]
            item['aiSummarized'] = True

    with open('vos-data.json', 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    print(f'\n=== 完成！{len(manual)} 手工 + {len(final)-len(manual)} 自动 = {len(final)} 条 ===')


if __name__ == '__main__':
    main()

