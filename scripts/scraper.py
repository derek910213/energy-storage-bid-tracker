import re
import os
import json
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# ===== 基本配置 =====
LIST_URL_TEMPLATE = "https://chuneng.bjx.com.cn/zb/{}/"
PAGES_TO_FETCH = 5           # 每次抓取列表的前几页(每页约60条),数字越大抓得越全,但越慢
DATA_FILE = "docs/data.json" # 数据存放的位置

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

# 标题里出现这些词，才认为是"中标/结果类"信息（而不是单纯的招标公告）
RESULT_KEYWORDS = ["中标", "成交", "预中标", "候选人", "入围", "签订", "遴选结果", "优选结果"]


def is_bid_result(title: str) -> bool:
    return any(k in title for k in RESULT_KEYWORDS)


def extract_price(title: str) -> str:
    """尝试从标题里提取价格信息，例如 0.85元/Wh、1.8亿元 等"""
    patterns = [
        r"\d+\.?\d*\s*[-~]\s*\d+\.?\d*\s*元/Wh",
        r"\d+\.?\d*\s*元/Wh",
        r"\d+\.?\d*\s*亿元?",
        r"\d+\.?\d*\s*万元",
    ]
    for p in patterns:
        m = re.search(p, title)
        if m:
            return m.group()
    return ""


def fetch_list_page(page_num: int):
    """抓取某一页的招标资讯列表，返回 [{title, link, date}, ...]"""
    url = LIST_URL_TEMPLATE.format(page_num) if page_num > 1 else "https://chuneng.bjx.com.cn/zb/"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    seen_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"news\.bjx\.com\.cn/html/\d+/\d+\.shtml", href):
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


def load_existing():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_data(records):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def main():
    existing = load_existing()
    existing_links = {r["link"] for r in existing}
    new_records = []

    for page in range(1, PAGES_TO_FETCH + 1):
        try:
            items = fetch_list_page(page)
        except Exception as e:
            print(f"第{page}页抓取失败：{e}")
            continue

        for it in items:
            if not is_bid_result(it["title"]):
                continue
            if it["link"] in existing_links:
                continue
            record = {
                "date": it["date"],
                "title": it["title"],
                "link": it["link"],
                "price": extract_price(it["title"]),
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            new_records.append(record)
            existing_links.add(it["link"])

        time.sleep(1.5)  # 礼貌抓取，避免请求过快给对方服务器压力

    all_records = new_records + existing
    all_records.sort(key=lambda r: r.get("date", ""), reverse=True)
    save_data(all_records)
    print(f"本次新增 {len(new_records)} 条记录，累计共 {len(all_records)} 条")


if __name__ == "__main__":
    main()
