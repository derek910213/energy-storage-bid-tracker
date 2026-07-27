import re
import os
import json
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ===== 数据来源配置 =====
# 以后想加新的网站，就在这个列表里再加一项即可
SOURCES = [
    {
        "name": "北极星储能网",
        "list_url": lambda p: "https://chuneng.bjx.com.cn/zb/" if p == 1 else f"https://chuneng.bjx.com.cn/zb/{p}/",
        "link_pattern": r"news\.bjx\.com\.cn/html/\d+/\d+\.shtml",
        "pages": 5,
    },
    {
        "name": "碳索储能网",
        "list_url": lambda p: f"https://cn.solarbe.com/shuju/cnzb?page={p}",
        "link_pattern": r"cn\.solarbe\.com/news/\d+/\d+\.html",
        "pages": 5,
    },
]

DATA_FILE = "docs/data.json"  # 数据存放的位置

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# 标题里出现这些词，才认为可能是"中标/结果类"信息，值得进详情页细读
RESULT_KEYWORDS = ["中标", "成交", "预中标", "候选人"]

# ===== 详情页正文抽取规则 =====
# 招标人 / 业主方
OWNER_PATTERNS = [
    r"受([^\s，,。;；\n]{2,40})的委托",
    r"招标人[（(]?[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"业主单位[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"采购人[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
]

# 中标人 / 中标候选人（优先第一中标候选人，最贴近最终中标结果）
WINNER_PATTERNS = [
    r"第一中标候选人[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"中标人[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"中标单位[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"成交人[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"成交单位[为是：:]\s*([^\s，,。;；\n（(]{2,40})",
    r"中标候选人[为是：:]\s*([^\s，,。;；\n（(]{2,60})",
]

# 价格（元/Wh 单价优先，其次总价）
PRICE_PATTERNS = [
    r"折合单价约?\s*(\d+\.?\d*\s*元/Wh)",
    r"投标单价[为是：:]?\s*(\d+\.?\d*\s*元/Wh)",
    r"中标单价[为是：:]?\s*(\d+\.?\d*\s*元/Wh)",
    r"(\d+\.?\d*\s*元/Wh)",
    r"中标价[为是：:]?\s*([\d,\.]+\s*(?:元|万元|亿元))",
    r"中标金额[为是：:]?\s*([\d,\.]+\s*(?:元|万元|亿元))",
    r"投标总价约?\s*([\d,\.]+\s*(?:元|万元|亿元))",
]


def is_bid_result(title: str) -> bool:
    return any(k in title for k in RESULT_KEYWORDS)


def extract_field(text: str, patterns) -> str:
    """按优先级依次尝试正则，返回第一个匹配到的内容"""
    for p in patterns:
        m = re.search(p, text)
        if m:
            val = m.group(1).strip().strip("《》()（）")
            if val:
                return val
    return ""


def infer_source(link: str) -> str:
    """根据链接域名反推来源网站名字（用于给旧数据补字段）"""
    if "bjx.com.cn" in link:
        return "北极星储能网"
    if "solarbe.com" in link:
        return "碳索储能网"
    return "未知来源"


def fetch_list_page(source: dict, page_num: int):
    """抓取某个数据源某一页的列表，返回 [{title, link, date}, ...]"""
    url = source["list_url"](page_num)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    seen_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(source["link_pattern"], href):
            continue
        title = a.get_text(strip=True)
        if len(title) < 6 or href in seen_links:
            continue
        seen_links.add(href)

        date = ""
        if a.parent:
            parent_text = a.parent.get_text(" ", strip=True)
            m = re.search(r"\d{4}-\d{2}-\d{2}", parent_text)
            if m:
                date = m.group()

        items.append({"title": title, "link": href, "date": date})
    return items


def fetch_detail_text(link: str) -> str:
    """抓取详情页并返回纯文本内容，用于关键词抽取"""
    try:
        resp = requests.get(link, headers=HEADERS, timeout=15)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup.get_text("\n", strip=True)
    except Exception as e:
        print(f"详情页抓取失败 {link}：{e}")
        return ""


def load_existing():
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)
    # 给缺少 source 字段的旧记录自动补上（根据链接域名推断）
    for r in records:
        if not r.get("source"):
            r["source"] = infer_source(r.get("link", ""))
    return records


def save_data(records):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def main():
    existing = load_existing()
    existing_links = {r["link"] for r in existing}
    new_records = []
    skipped_incomplete = 0

    for source in SOURCES:
        for page in range(1, source["pages"] + 1):
            try:
                items = fetch_list_page(source, page)
            except Exception as e:
                print(f"[{source['name']}] 第{page}页抓取失败：{e}")
                continue

            for it in items:
                if not is_bid_result(it["title"]):
                    continue
                if it["link"] in existing_links:
                    continue

                detail_text = fetch_detail_text(it["link"])
                time.sleep(1)  # 礼貌抓取，每篇详情页之间稍作停顿
                if not detail_text:
                    continue

                owner = extract_field(detail_text, OWNER_PATTERNS)
                winner = extract_field(detail_text, WINNER_PATTERNS)
                price = extract_field(detail_text, PRICE_PATTERNS)

                # 必须同时满足：有价格，且至少抓到招标人或中标人/候选人之一
                if not price or not (owner or winner):
                    skipped_incomplete += 1
                    continue

                record = {
                    "date": it["date"],
                    "title": it["title"],
                    "link": it["link"],
                    "owner": owner,
                    "winner": winner,
                    "price": price,
                    "source": source["name"],
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
                new_records.append(record)
                existing_links.add(it["link"])

            time.sleep(1.5)  # 每翻一页列表也稍作停顿

    all_records = new_records + existing
    all_records.sort(key=lambda r: r.get("date", ""), reverse=True)
    save_data(all_records)
    print(
        f"本次新增 {len(new_records)} 条记录（另有 {skipped_incomplete} 条因信息不全被跳过），"
        f"累计共 {len(all_records)} 条"
    )


if __name__ == "__main__":
    main()
