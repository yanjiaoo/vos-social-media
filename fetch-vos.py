#!/usr/bin/env python3
"""
抓取亚马逊卖家中文社媒热议话题，生成 vos-data.json
仅中文源：知无不言、卖家之家、AMZ123、微信公众号、雨果跨境等
聚焦：亚马逊卖家政策变动、FBA/FBM运营、广告、账号安全、合规、费用等
"""
import urllib.request
import xml.etree.ElementTree as ET
import json
import re
import html
from datetime import datetime, timezone
from urllib.parse import quote

QUERIES = [
    # 知无不言
    {'q': '知无不言 亚马逊 卖家 政策 OR 变动 OR 热议', 'source': '知无不言'},
    {'q': '知无不言 亚马逊 FBA OR 广告 OR 账号 OR 合规', 'source': '知无不言'},
    # 卖家之家
    {'q': '卖家之家 亚马逊 政策 OR 公告 OR 变动', 'source': '卖家之家'},
    {'q': 'site:mjzj.com 亚马逊 卖家 2026', 'source': '卖家之家'},
    # AMZ123
    {'q': 'AMZ123 亚马逊 政策 OR 热议 OR 卖家', 'source': 'AMZ123'},
    {'q': 'site:amz123.com 亚马逊 卖家 2026', 'source': 'AMZ123'},
    # 雨果跨境
    {'q': '雨果跨境 亚马逊 卖家 政策 OR 费用 OR 变动', 'source': '雨果跨境'},
    {'q': 'site:cifnews.com 亚马逊 卖家 2026', 'source': '雨果跨境'},
    # 微信公众号
    {'q': '亚马逊卖家 政策 OR 热议 OR 变动 site:mp.weixin.qq.com', 'source': '公众号'},
    {'q': '亚马逊 FBA OR 广告 OR 费用 调整 site:mp.weixin.qq.com', 'source': '公众号'},
    # 亿邦动力
    {'q': '亿邦动力 亚马逊 卖家 政策 OR 费用', 'source': '亿邦动力'},
    # 综合搜索
    {'q': '亚马逊卖家 热议 OR 吐槽 OR 政策变动 2026', 'source': '行业媒体'},
    {'q': '亚马逊 FBA费用 OR 广告费 OR 佣金 调整 2026', 'source': '行业媒体'},
    {'q': '亚马逊 账号安全 OR 封号 OR 申诉 2026', 'source': '行业媒体'},
    {'q': '亚马逊 物流 OR 仓储 OR 库存 政策 卖家 2026', 'source': '行业媒体'},
    {'q': '亚马逊 促销 OR 秒杀 OR 大促 规则 卖家 2026', 'source': '行业媒体'},
]

RELEVANCE_KEYWORDS = [
    '政策', '规则', '公告', '变动', '调整', '更新', '新规', '通知',
    '费用', '佣金', '成本', '收费', '涨价', '降价',
    'FBA', '物流', '仓储', '库存', '配送', '入仓', '贴标',
    '广告', 'PPC', 'ACoS', '投放', '竞价', '广告费',
    '账号', '封号', '申诉', '合规', '违规', '审核', '侵权',
    '运营', 'listing', '评价', '排名', '转化', 'BSR',
    '促销', '秒杀', '大促', '活动', 'Prime',
    '卖家', '热议', '吐槽', '问题', '困扰', '回款', '资金',
]

EXCLUDE_KEYWORDS = [
    '新手教程', '入门指南', '如何开店', '注册教程',
    '股价', '投资理财', 'Prime Video',
]


def fetch_rss(query, max_items=8):
    url = f'https://news.google.com/rss/search?q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    items = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; VOSBot/1.0)'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        root = ET.fromstring(data)
        for item in root.findall('.//item')[:max_items]:
            title_raw = item.findtext('title', '')
            parts = title_raw.rsplit(' - ', 1)
            title = html.unescape(parts[0].strip())
            source = parts[1].strip() if len(parts) > 1 else ''
            desc = re.sub(r'<[^>]+>', '', html.unescape(item.findtext('description', ''))).strip()[:300]
            pub_date = item.findtext('pubDate', '')
            link = item.findtext('link', '')
            try:
                dt = datetime.strptime(pub_date, '%a, %d %b %Y %H:%M:%S %Z')
            except Exception:
                dt = datetime.now(timezone.utc)
            items.append({
                'title': title, 'content': desc, 'source': source,
                'date': dt.strftime('%Y-%m-%d'), 'url': link,
            })
    except Exception as e:
        print(f'  [WARN] {e}')
    return items


def is_relevant(title, content):
    text = (title + ' ' + content).lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return False
    for kw in RELEVANCE_KEYWORDS:
        if kw in text:
            return True
    return False


def is_chinese(text):
    """检查文本是否主要为中文"""
    cn = len(re.findall(r'[\u4e00-\u9fff]', text))
    return cn > len(text) * 0.2


def main():
    print('=== 抓取 VOS From Social Media ===')
    all_items = []
    seen = set()

    for q_config in QUERIES:
        query = q_config['q']
        source_hint = q_config['source']
        print(f'  [{source_hint}] {query[:50]}...')

        for item in fetch_rss(query, 8):
            if item['title'] in seen:
                continue
            if not is_chinese(item['title']):
                continue
            if not is_relevant(item['title'], item['content']):
                continue
            seen.add(item['title'])
            if not item['source']:
                item['source'] = source_hint
            all_items.append(item)

    all_items.sort(key=lambda x: x['date'], reverse=True)
    all_items = all_items[:20]

    # 转为 topic 格式
    vos_topics = []
    for i, item in enumerate(all_items):
        # 清洗标题
        title = item['title']
        title = re.sub(r'[?？!！]+', '', title)
        title = re.sub(r'^(重磅|突发|独家|最新|速看|必看|震惊|刚刚|快讯)[：:|\s]*', '', title)
        title = re.sub(r'\s*[|丨｜—]\s*[\w\u4e00-\u9fff]+\s*$', '', title)

        vos_topics.append({
            'id': f'vos_{i+1:03d}',
            'rank': i + 1,
            'title': title.strip(),
            'verified': 'unconfirmed',
            'effectDate': item['date'],
            'summary': item['content'],
            'source': item['source'],
            'sellerVoices': [],
            'comparison': [],
            'links': [{'label': item['source'] or '查看原文', 'url': item['url']}],
        })

    with open('vos-data.json', 'w', encoding='utf-8') as f:
        json.dump(vos_topics, f, ensure_ascii=False, indent=2)

    print(f'\n=== 完成！共 {len(vos_topics)} 条卖家热议 ===')


if __name__ == '__main__':
    main()
