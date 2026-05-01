"""
기존 크롤링된 JSON 파일에 visit_purpose_summary 필드 추가
"""
import json
import time
import random
from pathlib import Path
from curl_cffi import requests as cf_requests

OUTPUT_DIR = "./data/seoul"

def get_visit_purposes(shop_ref: str) -> list:
    url = f"https://ct-api.catchtable.co.kr/api/review/v1/shops/{shop_ref}/filters"
    headers = {
        "Accept": "application/json",
        "Origin": "https://app.catchtable.co.kr",
        "Referer": f"https://app.catchtable.co.kr/ct/shop/{shop_ref}",
        "x-requested-with": "XMLHttpRequest",
    }
    try:
        r = cf_requests.get(url, headers=headers, impersonate="chrome120", timeout=15)
        if r.status_code != 200:
            return []
        purposes = r.json().get("data", {}).get("filters", {}).get("VISIT_PURPOSE", [])
        return [
            {"purpose": p.get("filterName"), "code": p.get("filterCode"), "count": p.get("reviewCount")}
            for p in purposes if p.get("filterName")
        ]
    except Exception as e:
        print(f"  오류: {e}")
        return []

files = sorted(Path(OUTPUT_DIR).glob("*.json"))
total = len(files)
print(f"총 {total}개 파일 처리")

for i, path in enumerate(files, 1):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if "visit_purpose_summary" in data:
        print(f"[{i}/{total}] 스킵 (이미 존재): {path.name}")
        continue

    shop_ref = data.get("shop_ref", "")
    if not shop_ref:
        continue

    purposes = get_visit_purposes(shop_ref)
    data["visit_purpose_summary"] = purposes

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[{i}/{total}] ✅ {path.name} | 방문목적 {len(purposes)}종")
    time.sleep(random.uniform(0.5, 1.0))

print("\n완료!")
