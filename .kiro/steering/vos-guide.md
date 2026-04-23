---
inclusion: auto
---

# VOS From Social Media 编辑指南

## 项目背景
这是亚马逊内部 Seller Learning Hub 的 VOS（Voice of Seller）模块数据仓库。
数据展示在 https://yanjiaoo.github.io/competitor-study-hub/ 的 "VOS From Social Media" 板块。
你编辑 vos-data.json 后 push，网页会自动加载最新内容。

## 你的职责
维护 vos-data.json 中的卖家热议话题，确保内容：
- 聚焦亚马逊卖家核心关注：政策变动、FBA/FBM运营、广告、账号安全、费用、促销规则
- 数据来源限中文社媒：知无不言、卖家之家、AMZ123、雨果跨境、亿邦动力、微信公众号
- 风格理性客观，新闻播报式，无情绪化字眼

## vos-data.json 数据结构

每个话题是一个 JSON 对象，字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| id | 是 | 唯一ID，格式 vos_001 |
| rank | 是 | 排名，数字越小越靠前 |
| title | 是 | 话题标题，中文，陈述式，无问号叹号 |
| verified | 是 | official（官方已核实）或 unconfirmed（待官方公告）|
| effectDate | 是 | 生效/发生日期，格式 YYYY-MM-DD |
| summary | 是 | 影响说明，200-500字，包含具体数据和影响范围 |
| source | 是 | 信息来源 |
| sellerVoices | 否 | 卖家真实反馈数组，每条含 source 和 content |
| comparison | 否 | Before/After 对比数组，每条含 dimension、before、after |
| links | 否 | 参考链接数组，每条含 label 和 url |

## 操作示例

### 添加新话题
在 vos-data.json 数组开头添加新对象，rank 设为 1，其他话题 rank 依次后移。

### 补充深度内容
找到对应话题，填充 sellerVoices、comparison、links 字段。
sellerVoices 来源标注具体平台（知无不言/卖家之家/AMZ123等）。
comparison 的 after 字段重点标注对卖家的实际影响。

### 修改已有话题
直接编辑对应字段即可。verified 从 unconfirmed 改为 official 时需确保有官方链接。

## 自动抓取
fetch-vos.py 每天自动抓取新话题标题，但只有标题和摘要。
你需要手动补充：sellerVoices（卖家声音）、comparison（Before/After对比）、links（参考链接）。

## 质量标准
- 每个完整话题应包含：影响说明 + 至少2条卖家声音 + Before/After对比表 + 至少1个参考链接
- 标题不超过40字，核心信息前置
- summary 包含具体数字（金额、百分比、时间节点）
- sellerVoices 引用真实卖家原话，标注来源平台
