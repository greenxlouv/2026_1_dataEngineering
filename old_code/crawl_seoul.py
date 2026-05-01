"""
캐치테이블 서울 전역 크롤러
1단계: 지역 필터 + 페이지네이션으로 서울 식당 URL 전수 수집
2단계: ThreadPoolExecutor(10) 병렬 크롤링 → JSON 저장

실행:
  python3 crawl_seoul.py                          # 서울 전역
  python3 crawl_seoul.py --areas 홍대 합정 강남   # 특정 지역만
  python3 crawl_seoul.py --skip-discover          # URL 재수집 생략 (이미 있을 때)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from curl_cffi import requests as cf_requests

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

API_BASE     = "https://ct-api.catchtable.co.kr"
SEARCH_API   = API_BASE + "/api/v6/search/list"
SHOP_API     = API_BASE + "/api/v4/shops/{shop_ref}"
MENU_API     = API_BASE + "/api/display/v2/shops/{shop_ref}/tabs/menu"
REVIEW_API   = API_BASE + "/api/review/v1/shops/{shop_ref}/reviews"
SHOP_BASE    = "https://app.catchtable.co.kr/ct/shop/"

OUTPUT_DIR       = "./data/seoul"
LOG_DIR          = "./logs"
URLS_FILE        = "./data/seoul_urls.txt"
FAILED_FILE      = "./data/seoul_failed.txt"

PAGE_SIZE        = 20       # 탐색 1회당 식당 수
MAX_DISCOVERY    = 2000     # 지역당 최대 탐색 수 (안전 상한)
MAX_WORKERS      = 10       # 병렬 크롤링 스레드 수
REVIEW_PAGE_SIZE = 12
REVIEW_SORT      = "B"
MAX_REVIEWS      = 999999  # 전체 수집
MAX_RETRY        = 3
RETRY_DELAYS     = [5, 15, 30]
REQUEST_DELAY    = (1.5, 3.0)
SHOP_DELAY       = (2.0, 4.0)

# 캐치테이블 서울 탭 지역 코드 (앱 필터 UI 기준)
SEOUL_AREAS = {
    "강남":           "CAT011001",
    "서초":           "CAT011002",
    "잠실/송파/강동": "CAT011003",
    "영등포/여의도/강서": "CAT011004",
    "건대/성수/왕십리": "CAT011005",
    "종로/중구":      "CAT011006",
    "홍대/합정/마포": "CAT011007",
    "용산/이태원/한남": "CAT011008",
    "성북/노원/중랑": "CAT011009",
    "구로/관악/동작": "CAT011010",
}

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs("./data", exist_ok=True)

log_date = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(f"{LOG_DIR}/seoul_{log_date}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# HTTP 헬퍼
# ──────────────────────────────────────────────
def get_headers(shop_ref: str = "") -> dict:
    referer = f"{SHOP_BASE}{shop_ref}" if shop_ref else "https://app.catchtable.co.kr/"
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Origin": "https://app.catchtable.co.kr",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
    }

def post_json(url: str, body: dict, timeout: int = 15) -> dict | None:
    try:
        r = cf_requests.post(
            url,
            json=body,
            headers={**get_headers(), "Content-Type": "application/json"},
            impersonate="chrome120",
            timeout=timeout,
        )
        if r.status_code == 429:
            log.warning(f"  429 Rate Limited: {url}")
            time.sleep(30)
            return None
        if r.status_code != 200:
            log.warning(f"  HTTP {r.status_code}: {url}")
            return None
        return r.json()
    except Exception as e:
        log.warning(f"  POST 오류: {e}")
        return None

def get_json(url: str, shop_ref: str = "", timeout: int = 15) -> dict | None:
    for attempt in range(MAX_RETRY):
        try:
            r = cf_requests.get(
                url,
                headers=get_headers(shop_ref),
                impersonate="chrome120",
                timeout=timeout,
            )
            if r.status_code == 429:
                log.warning(f"  429 Rate Limited: {url}")
                time.sleep(30)
                return None
            if r.status_code != 200:
                log.warning(f"  HTTP {r.status_code}: {url}")
                return None
            return r.json()
        except Exception as e:
            if attempt < MAX_RETRY - 1:
                wait = RETRY_DELAYS[attempt]
                log.warning(f"  GET 오류 (재시도 {attempt+1}): {e} — {wait}초 후")
                time.sleep(wait)
            else:
                log.warning(f"  GET 최종 실패: {e}")
                return None
    return None

# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def safe_int(val):
    try: return int(val) if val is not None else None
    except: return None

def safe_float(val):
    try:
        v = float(val)
        return v if v != 0.0 else None
    except: return None

def parse_price(pmin, pmax):
    try:
        mn, mx = int(pmin or 0), int(pmax or 0)
        if mn == 0 and mx == 0: return None
        if mn == 0 and mx == 9999: return "1만원 미만"
        if mx >= 1000000: return f"{mn // 10000}만원~"
        return f"{mn // 10000}만원 ~ {mx // 10000}만원"
    except: return None

def extract_shop_ref(url: str) -> str | None:
    m = re.search(r"/ct/shop/([^/?#]+)", url)
    return m.group(1) if m else None

# ──────────────────────────────────────────────
# 1단계: 지역 필터 탐색 (URL 수집)
# ──────────────────────────────────────────────
def discover_area(area_name: str, region_code: str) -> list[str]:
    """
    displayRegionCode 로 검색 API 커서 기반 페이지네이션 → shopRef URL 목록 반환
    """
    urls = []
    seen = set()
    next_offset = "0"
    page = 0

    log.info(f"[탐색] {area_name} ({region_code}) ...")
    while len(urls) < MAX_DISCOVERY:
        body = {
            "paging": {"offset": next_offset, "size": PAGE_SIZE},
            "listType": "GENERAL",
            "reservationParams": {},
            "notUseSpellCorrection": False,
            "sort": {"sortType": "recommended"},
            "userInfo": {"clientGeoPoint": {"lat": 37.5518333, "lon": 126.9887774}},
            "filters": {
                "displayRegionCodes": [region_code],
                "legalDistrictCodes": [],
                "facilityCodes": [],
                "filterTags": [],
            },
            "recommendationModel": "bmk-cwse",
            "useRerank": True,
        }
        resp = post_json(SEARCH_API, body)
        shops = _extract_shops(resp)

        if not shops:
            break

        for shop in shops:
            ref = (shop.get("shopRef") or shop.get("primaryCode")
                   or shop.get("shopMeta", {}).get("shopRef", "")
                   or str(shop.get("id", "")))
            if ref and ref not in seen:
                seen.add(ref)
                urls.append(SHOP_BASE + ref)

        page += 1
        log.info(f"  {area_name}: page={page} → {len(shops)}개, 누적 {len(urls)}개")

        paging = (resp.get("data", {}) or {}).get("paging", {}) or {}
        has_more = paging.get("hasMore", False)
        next_offset = paging.get("nextOffset", "")

        if not has_more or not next_offset:
            break

        time.sleep(random.uniform(1.0, 2.0))

    return urls


def _extract_shops(resp: dict | None) -> list:
    if not resp:
        return []
    data = resp.get("data", {})
    if isinstance(data, dict):
        return (data.get("shops")
                or data.get("shopResults", {}).get("shops", [])
                or data.get("results", [])
                or [])
    if isinstance(data, list):
        return data
    return []


def discover_all(areas: dict) -> list[str]:
    """모든 지역 탐색 → 중복 제거 URL 목록"""
    all_urls = {}  # shop_ref → url (중복 제거)

    for area_name, region_code in areas.items():
        urls = discover_area(area_name, region_code)
        for url in urls:
            ref = extract_shop_ref(url)
            if ref:
                all_urls[ref] = url
        time.sleep(random.uniform(2.0, 3.0))

    result = list(all_urls.values())
    log.info(f"\n[탐색 완료] 총 {len(result)}개 고유 식당 URL")
    return result

# ──────────────────────────────────────────────
# 파싱 함수
# ──────────────────────────────────────────────
def parse_shop_detail(data: dict) -> dict:
    d = data.get("data", {}).get("shopDetailVO", {})
    if not d:
        return {}

    weekly = (d.get("schedule", {}) or {}).get("weeklySchedule", []) or []
    business_hours = [
        {
            "day": day.get("dayOfWeek", ""),
            "open": day.get("openTime", ""),
            "close": day.get("closeTime", ""),
            "closed": day.get("dayOff", False),
            "break_start": day.get("breakTimeStart", ""),
            "break_end": day.get("breakTimeEnd", ""),
            "last_order": day.get("lastOrderTime", ""),
        }
        for day in weekly
    ]

    facilities = [f.get("displayText", "") for f in (d.get("facilities", []) or []) if f.get("displayText")]
    can_reserve = len(d.get("reservationInfoList", []) or []) > 0

    tv_appearances = [
        {
            "program": tv.get("programName", ""),
            "platform": tv.get("platformType", ""),
            "air_date": tv.get("airDate"),
            "episode": tv.get("episodeNumber"),
            "youtube_url": tv.get("contentsUrl", ""),
            "thumbnail": tv.get("thumbnailUrl", ""),
            "title": tv.get("contentTitle", ""),
            "description": tv.get("contentDescription", ""),
        }
        for tv in (d.get("tvAppearances", []) or [])
    ]

    review_summary = d.get("review", {}) or {}
    lat = safe_float(d.get("lat"))
    lon = safe_float(d.get("lon"))
    lunch_price = parse_price(d.get("lunchPriceMin"), d.get("lunchPriceMax"))
    dinner_price = parse_price(d.get("dinnerPriceMin"), d.get("dinnerPriceMax"))
    price_text = d.get("lunchAndDinnerPriceText") or dinner_price or lunch_price
    images = [img.get("imgUrl", "") for img in (d.get("images", []) or []) if img.get("imgUrl")]

    return {
        "basic_info": {
            "name": d.get("shopName", ""),
            "name_en": d.get("shopNameEn", ""),
            "alias": d.get("alias", ""),
            "category": d.get("foodKind", ""),
            "area": d.get("landName", ""),
            "description": d.get("serviceDesc", ""),
            "address": d.get("shopAddress", ""),
            "address_old": d.get("shopAddress2", ""),
            "latitude": lat,
            "longitude": lon,
            "phone": d.get("dispShopPhone", ""),
            "price_range": price_text,
            "sns_url": d.get("url", ""),
            "images": images[:5],
        },
        "schedule": business_hours,
        "facilities": facilities,
        "can_reserve": can_reserve,
        "tv_appearances": tv_appearances,
        "review_summary": {
            "total_count": safe_int(review_summary.get("totalReviewCount")),
            "rating": safe_float(review_summary.get("finalScore")),
            "food_score": safe_float(review_summary.get("foodScore")),
            "ambience_score": safe_float(review_summary.get("ambienceScore")),
            "service_score": safe_float(review_summary.get("serviceScore")),
        },
        "save_count": safe_int((d.get("bookmark") or {}).get("count")),
    }


def fetch_menus(shop_ref: str) -> dict:
    data = get_json(MENU_API.format(shop_ref=shop_ref), shop_ref=shop_ref)
    if not data:
        return {"menus": [], "menu_boards": []}
    raw_menus = data.get("menus", []) or []
    parsed = []
    for cat in raw_menus:
        cat_name = cat.get("categoryName") or "기본"
        for item in (cat.get("items") or []):
            parsed.append({
                "category": cat_name,
                "name": item.get("name", ""),
                "price_min": safe_int(item.get("minPrice")),
                "price_max": safe_int(item.get("maxPrice")),
                "description": item.get("description", ""),
                "image_url": item.get("imageUrl", ""),
            })
    boards = [b.get("imageUrl") for b in (data.get("menuBoards", []) or []) if b.get("imageUrl")]
    return {"menus": parsed, "menu_boards": boards}


def fetch_reviews(shop_ref: str, max_reviews: int = MAX_REVIEWS) -> list:
    reviews = []
    page = 1
    while len(reviews) < max_reviews:
        url = f"{REVIEW_API.format(shop_ref=shop_ref)}?page={page}&size={REVIEW_PAGE_SIZE}&sort={REVIEW_SORT}"
        data = get_json(url, shop_ref=shop_ref)
        if not data:
            break
        inner = data.get("data", {}) or {}
        review_list = inner.get("items") or inner.get("reviews") or inner.get("content") or []
        total = safe_int(inner.get("totalCount") or inner.get("total") or inner.get("totalElements"))
        if not review_list:
            break
        for r in review_list:
            content = r.get("content", {}) or {}
            writer = r.get("writer", {}) or {}
            engagement = r.get("engagement", {}) or {}
            photos = content.get("photos") or []
            try:
                created_at = datetime.fromtimestamp(int(r["regDate"]) / 1000, tz=KST).isoformat() if r.get("regDate") else None
            except:
                created_at = None
            reviews.append({
                "review_id": r.get("reviewRef") or r.get("id"),
                "author": writer.get("displayName") or writer.get("nickName"),
                "rating": safe_float(content.get("totalScore") or content.get("score")),
                "content": content.get("reviewContent") or content.get("text"),
                "visited_at": r.get("visitDate") or content.get("visitDate"),
                "created_at": created_at,
                "like_count": safe_int(engagement.get("likeCnt") or engagement.get("likeCount")),
                "food_score": safe_float(content.get("tasteScore") or content.get("foodScore")),
                "ambience_score": safe_float(content.get("moodScore") or content.get("ambienceScore")),
                "service_score": safe_float(content.get("serviceScore")),
                "images": [
                    p.get("originalUrl") or p.get("url") or p.get("imgUrl")
                    for p in photos if isinstance(p, dict)
                    and (p.get("originalUrl") or p.get("url") or p.get("imgUrl"))
                ],
            })
        if total is not None and len(reviews) >= total:
            break
        if len(review_list) < REVIEW_PAGE_SIZE:
            break
        page += 1
        time.sleep(random.uniform(*REQUEST_DELAY))
    return reviews[:max_reviews]

# ──────────────────────────────────────────────
# 2단계: 단일 식당 크롤링 (스레드 실행 단위)
# ──────────────────────────────────────────────
def crawl_one(url: str, max_reviews: int = MAX_REVIEWS) -> tuple[str, bool]:
    """(url, 성공여부) 반환"""
    shop_ref = extract_shop_ref(url)
    if not shop_ref:
        return url, False

    out_path = Path(OUTPUT_DIR) / f"catchtable_{shop_ref}.json"
    if out_path.exists():
        log.info(f"  ⏭️  스킵 (이미 존재): {out_path.name}")
        return url, True

    crawled_at = datetime.now(KST).isoformat()
    start = time.time()

    shop_data = get_json(SHOP_API.format(shop_ref=shop_ref), shop_ref=shop_ref)
    if not shop_data or not shop_data.get("data", {}).get("shopDetailVO"):
        log.warning(f"  ❌ 기본 정보 없음: {url}")
        return url, False

    parsed = parse_shop_detail(shop_data)
    shop_name = parsed.get("basic_info", {}).get("name", shop_ref)
    log.info(f"  [{shop_name}] 기본 정보 완료")

    time.sleep(random.uniform(*REQUEST_DELAY))
    menu_data = fetch_menus(shop_ref)

    time.sleep(random.uniform(*REQUEST_DELAY))
    total_reviews = parsed.get("review_summary", {}).get("total_count") or 0
    reviews = fetch_reviews(shop_ref, max_reviews=max_reviews) if total_reviews > 0 else []
    log.info(f"  [{shop_name}] 리뷰 {len(reviews)}개")

    result = {
        "restaurant_id": f"catchtable_{shop_ref}",
        "shop_ref": shop_ref,
        "crawled_at": crawled_at,
        "source_url": url,
        **parsed,
        **menu_data,
        "reviews": reviews,
        "summary": {
            "total_reviews_collected": len(reviews),
            "crawl_duration_seconds": round(time.time() - start, 1),
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log.info(f"  ✅ {out_path.name} | 리뷰 {len(reviews)}개 | {result['summary']['crawl_duration_seconds']}초")
    return url, True


def crawl_parallel(urls: list[str], workers: int = MAX_WORKERS, max_reviews: int = MAX_REVIEWS) -> dict:
    stats = {"done": 0, "skip": 0, "fail": 0}
    total = len(urls)
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(crawl_one, url, max_reviews): url for url in urls}
        for future in as_completed(futures):
            url, ok = future.result()
            completed += 1
            if ok:
                out_path = Path(OUTPUT_DIR) / f"catchtable_{extract_shop_ref(url)}.json"
                if out_path.exists():
                    stats["done"] += 1
                else:
                    stats["done"] += 1
            else:
                stats["fail"] += 1
                with open(FAILED_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now(KST).isoformat()}\t{url}\n")
            log.info(f"[진행] {completed}/{total} | 완료:{stats['done']} 실패:{stats['fail']}")

    return stats

# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="CatchTable 서울 전역 크롤러")
    parser.add_argument("--areas", nargs="+", default=None,
                        help="크롤링할 지역명 (기본: 서울 전체)")
    parser.add_argument("--skip-discover", action="store_true",
                        help=f"URL 탐색 생략 — {URLS_FILE} 를 그대로 사용")
    parser.add_argument("--max-reviews", type=int, default=MAX_REVIEWS,
                        help="매장당 최대 리뷰 수 (기본 500)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help="병렬 스레드 수 (기본 10)")
    args = parser.parse_args()

    if args.areas:
        areas = {name: SEOUL_AREAS[name] for name in args.areas if name in SEOUL_AREAS}
        unknown = [n for n in args.areas if n not in SEOUL_AREAS]
        if unknown:
            log.warning(f"알 수 없는 지역명 (무시됨): {unknown}")
            log.warning(f"사용 가능한 지역: {list(SEOUL_AREAS.keys())}")
    else:
        areas = SEOUL_AREAS

    # ── 1단계: URL 탐색
    if args.skip_discover and Path(URLS_FILE).exists():
        with open(URLS_FILE, encoding="utf-8") as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        log.info(f"[탐색 생략] {URLS_FILE} 에서 {len(urls)}개 URL 로드")
    else:
        urls = discover_all(areas)
        Path(URLS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(URLS_FILE, "w", encoding="utf-8") as f:
            for url in urls:
                f.write(url + "\n")
        log.info(f"URL 목록 저장: {URLS_FILE}")

    if not urls:
        log.error("수집된 URL이 없습니다. API 파라미터를 확인하세요.")
        return

    # ── 2단계: 병렬 크롤링
    log.info(f"\n[크롤링 시작] {len(urls)}개 식당 / {args.workers}스레드")
    stats = crawl_parallel(urls, workers=args.workers, max_reviews=args.max_reviews)

    log.info(f"\n[완료] 성공:{stats['done']} 실패:{stats['fail']}")
    log.info(f"출력: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
