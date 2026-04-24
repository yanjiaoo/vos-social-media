#!/usr/bin/env python3
"""
VOS From Social Media v3 - 双引擎情报网
引擎1: 社媒雷达（卖家真实声音）
引擎2: 官方情报（政策与工具动态）
自动分类到8个核心议题 + 关键词预警评分
"""
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime, timezone
from urllib.parse import quote

# ==================== 核心议题分类 ====================
TOPICS = {
    'advertising': {'label': '📢 广告', 'keywords': ['广告', 'PPC', 'ACoS', 'ROAS', '竞价', '投放', 'advertising', 'ad spend', 'sponsored', 'campaign', '广告费', '扣款']},
    'promotion': {'label': '🏷️ 促销', 'keywords': ['促销', '秒杀', 'Prime Day', 'coupon', 'deal', 'lightning deal', '划线价', '折扣', 'BD', 'LD', '大促', '定价']},
    'compliance': {'label': '⚖️ 合规', 'keywords': ['合规', '违规', '变体', 'Vine', '评论', '侵权', '知识产权', 'IP', 'compliance', 'violation', 'infringement', '审核', '预警']},
    'brand': {'label': '🏢 品牌', 'keywords': ['品牌', 'Brand Registry', '品牌备案', '品牌审核', '独特性', 'brand', '品牌信誉', '透明计划']},
    'returns': {'label': '📦 退货', 'keywords': ['退货', 'return', 'APRL', '退货标签', '退货率', '退款', 'refund', 'FBM退货', '退货成本']},
    'tax': {'label': '💰 税务', 'keywords': ['税务', 'VAT', 'VCS', '增值税', '所得税', 'PN15', '税', 'tax', '报税', '税务合规', '账户冻结']},
    'logistics': {'label': '🚚 物流', 'keywords': ['物流', 'FBA', '入仓', '仓储', '库存', '贴标', '货代', 'GWD', '智能枢纽', 'fulfillment', '配送', '头程']},
    'trending': {'label': '🔥 选品/趋势', 'keywords': ['选品', '趋势', '消费趋势', '品类', '转型', '新品', 'trending', 'niche', '市场机会']},
}

# ==================== 关键词预警系统 ====================
# 高权重信号词（卖家痛点/抗议/重大变动）
ALERT_KEYWORDS = {
    3: ['revolted', 'suspension', 'unfair', 'impossible', 'reject', 'protest',
        '封号', '不公', '抗议', '暴雷', '炸弹', '噩梦', '冻结', '拒绝', '强制',
        '新规', '新政', '重大调整', '全面执行', '强制执行'],
    2: ['policy change', 'fee increase', 'new rule', 'mandatory', 'deadline',
        '政策变动', '费用上涨', '成本飙升', '紧急', '预警', '调整', '取消',
        '上线', '终止', '升级', '更新'],
    1: ['FBA', 'Prime Day', 'Brand Registry', 'VAT', 'APRL', 'VCS', 'GWD',
        'PPC', 'ACoS', 'listing', 'review', 'inventory',
        '广告', '促销', '合规', '品牌', '退货', '税务', '物流'],
}

# 排除词
EXCLUDE_PATTERNS = [
    'giveaway', 'coupon code', 'promo code', 'how to start',
    'beginner guide', '新手教程', '入门指南', '如何开店',
    'stock price', '股价', 'Prime Video',
]

# ==================== 数据源配置 ====================
# 社媒雷达
SOCIAL_RADAR = [
    # 知无不言
    {'q': '知无不言 亚马逊 卖家 政策 OR 广告 OR 合规 OR 封号', 'source': '知无不言', 'engine': 'social'},
    {'q': '知无不言 亚马逊 FBA OR 退货 OR 品牌 OR 税务', 'source': '知无不言', 'engine': 'social'},
    # 卖家之家
    {'q': '卖家之家 亚马逊 政策 OR 费用 OR 广告 OR 促销', 'source': '卖家之家', 'engine': 'social'},
    {'q': '卖家之家 亚马逊 合规 OR 品牌 OR 退货 OR 物流', 'source': '卖家之家', 'engine': 'social'},
    # AMZ123
    {'q': 'AMZ123 亚马逊 卖家 政策 OR 热议 OR 广告 OR 费用', 'source': 'AMZ123', 'engine': 'social'},
    # 雨果跨境
    {'q': '雨果跨境 亚马逊 卖家 政策 OR 广告 OR 合规 OR FBA', 'source': '雨果跨境', 'engine': 'social'},
    # 公众号
    {'q': '亚马逊卖家 政策 OR 广告 OR 合规 变动 site:mp.weixin.qq.com', 'source': '公众号', 'engine': 'social'},
    {'q': '亚马逊 FBA OR 退货 OR 品牌 OR 税务 卖家 site:mp.weixin.qq.com', 'source': '公众号', 'engine': 'social'},
]

# 官方情报
OFFICIAL_INTEL = [
    {'q': 'Amazon seller central announcement policy 2026', 'source': 'Amazon Official', 'engine': 'official', 'lang': 'en'},
    {'q': 'Amazon FBA fee change policy update 2026', 'source': 'Industry Media', 'engine': 'official', 'lang': 'en'},
    {'q': '亚马逊 卖家中心 公告 政策 2026', 'source': '亚马逊官方', 'engine': 'official'},
    {'q': '亚马逊 FBA 费用 政策 调整 2026', 'source': '行业媒体', 'engine': 'official'},
]


def fetch_rss(query, lang='zh', max_items=8):
    hl = 'en' if lang == 'en' else 'zh-CN'
    gl = 'US' if lang == 'en' else 'CN'
    ceid = 'US:en' if lang == 'en' else 'CN:zh-Hans'
    url = f'https://news.google.com/rss/search?q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}'
    items = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'VOSv3/1.0'})
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
            })
    except Exception as e:
        print(f'  [WARN] {e}')
    return items


def classify_topic(title, content):
    """自动分类到核心议题"""
    text = (title + ' ' + content).lower()
    scores = {}
    for topic_id, config in TOPICS.items():
        score = sum(1 for kw in config['keywords'] if kw.lower() in text)
        if score > 0:
            scores[topic_id] = score
    if scores:
        return max(scores, key=scores.get)
    return 'trending'


def calc_alert_score(title, content):
    """关键词预警评分"""
    text = (title + ' ' + content).lower()
    for pat in EXCLUDE_PATTERNS:
        if pat.lower() in text:
            return -1
    score = 0
    for weight, keywords in ALERT_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                score += weight
    return score


def is_chinese(text):
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    return cn > len(text) * 0.15


def clean_title(title):
    t = title.strip()
    t = re.sub(r'[?？!！]+$', '', t)
    t = re.sub(r'^(重磅|突发|独家|最新|速看|必看|震惊|刚刚|快讯)[：:|\s]*', '', t)
    t = re.sub(r'【[^】]*】\s*', '', t)
    t = re.sub(r'\s*[|丨｜—]\s*[\w\u4e00-\u9fff]+\s*$', '', t)
    return t.strip()


def main():
    print('=== VOS From Social Media v3 双引擎抓取 ===\n')
    all_items = []
    seen = set()

    # 社媒雷达
    print('[社媒雷达]')
    for config in SOCIAL_RADAR:
        print(f'  {config["source"]}: {config["q"][:40]}...')
        for item in fetch_rss(config['q'], config.get('lang', 'zh'), 8):
            if item['title'] in seen:
                continue
            if not is_chinese(item['title']) and config.get('lang') != 'en':
                continue
            seen.add(item['title'])
            item['engine'] = 'social'
            if not item['source']:
                item['source'] = config['source']
            all_items.append(item)

    # 官方情报
    print('\n[官方情报]')
    for config in OFFICIAL_INTEL:
        print(f'  {config["source"]}: {config["q"][:40]}...')
        for item in fetch_rss(config['q'], config.get('lang', 'zh'), 6):
            if item['title'] in seen:
                continue
            seen.add(item['title'])
            item['engine'] = 'official'
            if not item['source']:
                item['source'] = config['source']
            all_items.append(item)

    print(f'\n总计抓取: {len(all_items)} 条')

    # 评分+分类
    scored = []
    for item in all_items:
        score = calc_alert_score(item['title'], item.get('content', ''))
        if score <= 0:
            continue
        item['alert_score'] = score
        item['topic'] = classify_topic(item['title'], item.get('content', ''))
        scored.append(item)

    scored.sort(key=lambda x: x['alert_score'], reverse=True)
    print(f'评分筛选后: {len(scored)} 条')

    # 保留手工条目
    existing = []
    try:
        with open('vos-data.json', 'r', encoding='utf-8') as f:
            existing = json.load(f)
    except Exception:
        pass

    manual = [item for item in existing if item.get('sellerVoices') or item.get('comparison')]
    manual_titles = set(item.get('title', '') for item in manual)

    # 合并输出
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
        topic_config = TOPICS.get(item['topic'], {})
        engine_label = '📡 社媒雷达' if item.get('engine') == 'social' else '📋 官方情报'
        final.append({
            'id': f'vos_auto_{rank:03d}',
            'rank': rank,
            'title': title,
            'verified': 'unconfirmed',
            'effectDate': item['date'],
            'summary': item.get('content', '')[:300],
            'source': item['source'],
            'topic': item['topic'],
            'topicLabel': topic_config.get('label', ''),
            'engineLabel': engine_label,
            'alertScore': item['alert_score'],
            'sellerVoices': [],
            'comparison': [],
            'links': [{'label': item['source'], 'url': item['url']}],
        })
        rank += 1

    with open('vos-data.json', 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    # 统计
    topic_counts = {}
    for item in final:
        t = item.get('topic', item.get('topicLabel', 'other'))
        topic_counts[t] = topic_counts.get(t, 0) + 1
    print(f'\n议题分布: {topic_counts}')
    print(f'=== 完成！{len(manual)} 条手工 + {len(final)-len(manual)} 条自动 = {len(final)} 条 ===')


if __name__ == '__main__':
    main()
