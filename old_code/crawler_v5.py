"""
캐치테이블 전국 크롤러 v5 (speed-tuned, safe)
1단계: dining/waiting/pickup 3개 엔드포인트 + notAvailableShopRefs로 전수 ref 수집
2단계: ThreadPoolExecutor 병렬 크롤링 → JSON 저장
+ 방문목적태그 summary
+ 메뉴보드 URL 저장
+ 리뷰 전량 수집 유지

실행:
  python3 crawler_v5.py --areas 광주/이천/여주 --workers 16
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

from curl_cffi import requests as cf_requests

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
KST = timezone(timedelta(hours=9))

API_BASE = "https://ct-api.catchtable.co.kr"
SEARCH_ENDPOINTS = {
    "dining": API_BASE + "/api/v6/search/dining/list",
    "waiting": API_BASE + "/api/v6/search/waiting/list",
    "pickup": API_BASE + "/api/v6/search/pickup/list",
}
SHOP_API = API_BASE + "/api/v4/shops/{shop_ref}"
MENU_API = API_BASE + "/api/display/v2/shops/{shop_ref}/tabs/menu"
REVIEW_API = API_BASE + "/api/review/v1/shops/{shop_ref}/reviews"
SHOP_BASE = "https://app.catchtable.co.kr/ct/shop/"

OUTPUT_DIR = "./data/seoul"
LOG_DIR = "./logs"
URLS_FILE = "./data/seoul_urls.txt"
FAILED_FILE = "./data/seoul_failed.txt"

PAGE_SIZE = 20
MAX_PAGES = 300

# 너무 공격적이지 않게 약간만 상향
MAX_WORKERS = 16

REVIEW_PAGE_SIZE = 12
REVIEW_SORT = "B"
MAX_REVIEWS = 999999

MAX_RETRY = 3
RETRY_DELAYS = [5, 15, 30]

# 기존 (1.5, 3.0) -> 보수적으로만 축소
REQUEST_DELAY = (0.35, 0.8)

# discover 단계도 약간만 줄임
DISCOVER_PAGE_DELAY = (0.25, 0.6)
DISCOVER_AREA_DELAY = (0.7, 1.2)
DISCOVER_ENDPOINT_DELAY = (0.5, 1.0)

# ★ 여기에 브라우저에서 복사한 쿠키 붙여넣기
COOKIE_STRING = "_gcl_au=1.1.1979824641.1775209724; _hackle_hid=28f588a3-142c-4825-acf2-73fbe167947b; _hackle_did_7dQgTKfweH0n436c9aJLVh84yOncuWxD=28f588a3-142c-4825-acf2-73fbe167947b; _hackle_mkt_7dQgTKfw=%7B%7D; ab180ClientId=105817b5-9aa5-4c27-a7fb-f818cd04a9a0; _gid=GA1.3.1016300169.1776769875; airbridge_migration_metadata__catchtable=%7B%22version%22%3A%221.11.6%22%7D; AMP_MKTG_948acc4216=JTdCJTIycmVmZXJyZXIlMjIlM0ElMjJodHRwcyUzQSUyRiUyRmFjY291bnRzLmtha2FvLmNvbSUyRiUyMiUyQyUyMnJlZmVycmluZ19kb21haW4lMjIlM0ElMjJhY2NvdW50cy5rYWthby5jb20lMjIlN0Q=; x-ct-a=AACghOy8r-uQhuwAAAAKAG1uAgBCAAAAAgBwYQIBp3GUAHF1YxAA13rTAHF1EABLAAAAAgBwYWwCAEsAAAACAHRqAgA3Njg2MV-AsOqZsuycsOognJXttbPqsYTsAAAAGgBuZAIAAAGdtkW4PQB0QXB4ZRIAbGFlcgAAAAUAcGwCAHBwCgAAAIkSL5nX0Omn32B7FvyEwFnQijZtyY=; _hackle_uid_7dQgTKfweH0n436c9aJLVh84yOncuWxD=DxT7xBqpaBWq3xWMvv4u2A; airbridge_user__catchtable=%7B%22attributes%22%3A%7B%22deviceType%22%3A%22Web-PC%22%2C%22isNativeApp%22%3Afalse%2C%22buildVersion%22%3A20260421093651%2C%22grade%22%3A%22A%22%2C%22userName%22%3A%22%uC190%uBBFC%uC120%22%2C%22userNickname%22%3A%22%uC131%uACF5%uD55C%20%uAC1C%uCC99%uAC00_16867%22%2C%22marketingAgreeYn%22%3A%22Y%22%2C%22isMember%22%3A%22Y%22%2C%22ctRegisterDate%22%3A%222024-12-05%22%2C%22loginChannel%22%3A%22Kakao%22%2C%22lastLoginDate%22%3A%222026-04-22%22%7D%2C%22externalUserID%22%3A%22DxT7eEJxcGFCV3EzeFdNdnY0dTJBJTIyJTJDJTIyc2Vzc2lvbklkJTIyJTNBMTc3NjgzOTI4MTkyMyUyQyUyMm9wdE91dCUyMiUzQWZhbHNlJTJDJTIybGFzdEV2ZW50VGltZSUyMiUzQTE3NzY4NDE2Njk5NjElMkMlMjJsYXN0RXZlbnRJZCUyMiUzQTQxMzklMkMlMjJwYWdlQ291bnRlciUyMiUzQTMlMkMlMjJjb29raWVEb21haW4lMjIlM0ElMjIuY2F0Y2h0YWJsZS5jby5rciUyMiU3RA==; _hackle_last_event_ts_eH0n436c9aJLVh84yOncuWxD=1776841670081"


SEARCH_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "origin": "https://app.catchtable.co.kr",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    "x-requested-with": "XMLHttpRequest",
    "content-type": "application/json",
    "cookie": COOKIE_STRING,
}

REGION_CODE_MAP = {
    # 서울
    "강남": "CAT011001",
    "서초": "CAT011002",
    "잠실/송파/강동": "CAT011003",
    "영등포/여의도/강서": "CAT011004",
    "건대/성수/왕십리": "CAT011005",
    "종로/중구": "CAT011006",
    "홍대/합정/마포": "CAT011007",
    "용산/이태원/한남": "CAT011008",
    "성북/노원/중랑": "CAT011009",
    "구로/관악/동작": "CAT011010",

    # 경기
    "성남시(분당/판교/성남)": "CAT041001",
    "수원": "CAT041002",
    "용인/화성(동탄)": "CAT041003",
    "안양/과천": "CAT041004",
    "군포/의왕": "CAT041005",
    "부천/안산/시흥/광명": "CAT041006",
    "평택/오산/안성": "CAT041007",
    "고양/파주": "CAT041008",
    "김포": "CAT041009",
    "가평/양평": "CAT041010",
    "광주/이천/여주": "CAT041011",
    "남양주/의정부": "CAT041012",
    "하남/구리": "CAT041013",
    "포천/양주/동두천/연천": "CAT041014",

    # 인천
    "인천": "CAT028",

    # 부산/제주 등
    "부산": "CAT026",
    "제주": "CAT050",
    "울산": "CAT031",
    "경남": "CAT048",
    "대구": "CAT027",
    "경북": "CAT047",
    "강원": "CAT042",
    "대전": "CAT030",
    "충남": "CAT044",
    "충북": "CAT043",
    "세종": "CAT036001",
    "광주": "CAT029",
    "전북": "CAT045",
    "전남": "CAT046",
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
# Session 재사용 (thread-local)
# ──────────────────────────────────────────────
_thread_local = threading.local()


def get_session():
    if not hasattr(_thread_local, "session"):
        session = cf_requests.Session()
        _thread_local.session = session
    return _thread_local.session


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
        "cookie": COOKIE_STRING,
    }


def post_search(url: str, body: dict, endpoint: str, tx_id: int) -> dict | None:
    headers = dict(SEARCH_HEADERS)
    headers["referer"] = f"https://app.catchtable.co.kr/ct/search/list?service={endpoint}"
    headers["x-transaction-id"] = str(tx_id)
    headers["search-list-page-visit-id"] = str(int(time.time() * 1000))

    session = get_session()

    for attempt in range(MAX_RETRY):
        try:
            r = session.post(
                url,
                json=body,
                headers=headers,
                impersonate="chrome120",
                timeout=30,
            )

            if r.status_code == 429:
                wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                log.warning(f"  429 Rate Limited — {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue

            if r.status_code != 200:
                log.warning(f"  HTTP {r.status_code}: {url}")
                return None

            return r.json()

        except Exception as e:
            if attempt < MAX_RETRY - 1:
                wait = RETRY_DELAYS[attempt]
                log.warning(f"  POST 오류 (재시도 {attempt + 1}): {e} — {wait}초 후")
                time.sleep(wait)
            else:
                log.warning(f"  POST 최종 실패: {e}")

    return None


def get_json(url: str, shop_ref: str = "", timeout: int = 15) -> dict | None:
    session = get_session()

    for attempt in range(MAX_RETRY):
        try:
            r = session.get(
                url,
                headers=get_headers(shop_ref),
                impersonate="chrome120",
                timeout=timeout,
            )

            if r.status_code == 429:
                wait = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                log.warning(f"  429 Rate Limited — {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue

            if r.status_code != 200:
                log.warning(f"  HTTP {r.status_code}: {url}")
                return None

            return r.json()

        except Exception as e:
            if attempt < MAX_RETRY - 1:
                wait = RETRY_DELAYS[attempt]
                log.warning(f"  GET 오류 (재시도 {attempt + 1}): {e} — {wait}초 후")
                time.sleep(wait)
            else:
                log.warning(f"  GET 최종 실패: {e}")

    return None


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def safe_int(val):
    try:
        return int(val) if val is not None and val != "" else None
    except Exception:
        return None


def safe_float(val):
    try:
        v = float(val)
        return v if v != 0.0 else None
    except Exception:
        return None


def parse_price(pmin, pmax):
    try:
        mn, mx = int(pmin or 0), int(pmax or 0)
        if mn == 0 and mx == 0:
            return None
        if mn == 0 and mx == 9999:
            return "1만원 미만"
        if mx >= 1000000:
            return f"{mn // 10000}만원~"
        return f"{mn // 10000}만원 ~ {mx // 10000}만원"
    except Exception:
        return None


def extract_shop_ref_from_url(url: str) -> str | None:
    m = re.search(r"/ct/shop/([^/?#]+)", url)
    return m.group(1) if m else None


def extract_ref_from_shop(shop: dict) -> str | None:
    for v in [
        shop.get("shopRef"),
        shop.get("primaryCode"),
        (shop.get("shopMeta") or {}).get("shopRef"),
        shop.get("id"),
    ]:
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


# ──────────────────────────────────────────────
# 1단계: ref 수집
# ──────────────────────────────────────────────
def discover_area_by_endpoint(
    area_name: str,
    region_code: str,
    endpoint_name: str,
    endpoint_url: str,
    seen: set,
    tx_seed: int = 100,
) -> list[str]:
    """단일 엔드포인트(dining/waiting/pickup)로 한 지역 전수 수집"""
    refs = []
    offset = "0"
    page = 0

    while page < MAX_PAGES:
        body = {
            "paging": {"offset": offset, "size": PAGE_SIZE},
            "listType": "GENERAL",
            "reservationParams": {},
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

        resp = post_search(endpoint_url, body, endpoint_name, tx_seed + page)
        if not resp:
            break

        data = resp.get("data", {}) or {}

        shops = data.get("shopResults", {}).get("shops", []) or []
        new_shops = 0
        for shop in shops:
            ref = extract_ref_from_shop(shop)
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
                new_shops += 1

        not_available = data.get("shopResults", {}).get("notAvailableShopRefs", []) or []
        new_na = 0
        for ref in not_available:
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
                new_na += 1

        paging = data.get("paging", {}) or {}
        has_more = bool(paging.get("hasMore", False))
        next_offset = paging.get("nextOffset", "")

        page += 1
        log.info(
            f"  [{area_name}/{endpoint_name}] page={page} "
            f"shops+{new_shops} na+{new_na} "
            f"누적={len(refs)} has_more={has_more}"
        )

        if not has_more or not next_offset or next_offset == "0":
            break

        offset = str(next_offset)
        time.sleep(random.uniform(*DISCOVER_PAGE_DELAY))

    return refs


def discover_area(area_name: str, region_code: str) -> list[str]:
    """3개 엔드포인트 전부 돌려서 지역 전체 ref 수집"""
    seen: set = set()
    all_refs: list[str] = []

    for idx, (ep_name, ep_url) in enumerate(SEARCH_ENDPOINTS.items()):
        refs = discover_area_by_endpoint(
            area_name,
            region_code,
            ep_name,
            ep_url,
            seen,
            tx_seed=100 + idx * 200,
        )
        all_refs.extend(refs)
        log.info(f"  [{area_name}/{ep_name}] 완료: {len(refs)}개 (지역 누적 {len(all_refs)}개)")
        time.sleep(random.uniform(*DISCOVER_ENDPOINT_DELAY))

    log.info(f"[{area_name}] 최종 {len(all_refs)}개 고유 ref")
    return [SHOP_BASE + ref for ref in all_refs]


def discover_all(areas: dict) -> list[str]:
    all_urls: dict[str, str] = {}
    for area_name, region_code in areas.items():
        urls = discover_area(area_name, region_code)
        for url in urls:
            ref = extract_shop_ref_from_url(url)
            if ref:
                all_urls[ref] = url
        time.sleep(random.uniform(*DISCOVER_AREA_DELAY))

    result = list(all_urls.values())
    log.info(f"\n[탐색 완료] 총 {len(result)}개 고유 매장")
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

    facilities = [
        f.get("displayText", "")
        for f in (d.get("facilities", []) or [])
        if f.get("displayText")
    ]
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
            "main_service": d.get("mainService", ""),
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
        return {"menus": [], "menu_boards": [], "menu_detail_info": {}}

    raw_menus = data.get("menus", []) or []
    parsed = []

    for category in raw_menus:
        if category.get("type") == "category":
            cat_name = category.get("name", "기본")
            for item in category.get("items") or []:
                if item.get("type") != "menu":
                    continue
                parsed.append({
                    "category": cat_name,
                    "name": item.get("name", ""),
                    "price_min": safe_int(item.get("minPrice")),
                    "price_max": safe_int(item.get("maxPrice")),
                    "description": item.get("description", ""),
                    "image_url": item.get("imageUrl", ""),
                    "is_representative": item.get("isRepresentative", False),
                    "is_recommended": item.get("isRecommended", False),
                    "is_new": item.get("isNew", False),
                })

        elif category.get("type") == "menu":
            parsed.append({
                "category": "기본",
                "name": category.get("name", ""),
                "price_min": safe_int(category.get("minPrice")),
                "price_max": safe_int(category.get("maxPrice")),
                "description": category.get("description", ""),
                "image_url": category.get("imageUrl", ""),
                "is_representative": category.get("isRepresentative", False),
                "is_recommended": category.get("isRecommended", False),
                "is_new": category.get("isNew", False),
            })

    boards = [
        b.get("imageUrl")
        for b in (data.get("menuBoards", []) or [])
        if b.get("imageUrl")
    ]

    detail = data.get("menuDetailInfo", {}) or {}
    menu_detail_info = {
        "is_kids_menu": detail.get("isKidsMenu"),
        "is_vegan": detail.get("isVeganMenuSubstitute"),
        "is_allergy_substitute": detail.get("isAllergyMenuSubstitute"),
        "alcohol_required": detail.get("isAlcoholOrderRequired"),
        "corkage_guide": detail.get("corkChargeGuide", ""),
        "last_updated": detail.get("lastMenuUpdateDateTime", ""),
    }

    return {
        "menus": parsed,
        "menu_boards": boards,
        "menu_detail_info": menu_detail_info,
    }


def fetch_visit_purposes(shop_ref: str) -> list:
    url = f"https://ct-api.catchtable.co.kr/api/review/v1/shops/{shop_ref}/filters"
    data = get_json(url, shop_ref=shop_ref)
    if not data:
        return []

    purposes = data.get("data", {}).get("filters", {}).get("VISIT_PURPOSE", [])
    return [
        {
            "purpose": p.get("filterName"),
            "code": p.get("filterCode"),
            "count": safe_int(p.get("reviewCount")),
        }
        for p in purposes if p.get("filterName")
    ]


def fetch_reviews(shop_ref: str, max_reviews: int = MAX_REVIEWS) -> list:
    reviews = []
    page = 1

    while len(reviews) < max_reviews:
        url = (
            f"{REVIEW_API.format(shop_ref=shop_ref)}"
            f"?page={page}&size={REVIEW_PAGE_SIZE}&sort={REVIEW_SORT}"
        )
        data = get_json(url, shop_ref=shop_ref)
        if not data:
            break

        inner = data.get("data", {}) or {}
        review_list = (
            inner.get("items")
            or inner.get("reviews")
            or inner.get("content")
            or []
        )
        total = safe_int(
            inner.get("totalCount")
            or inner.get("total")
            or inner.get("totalElements")
        )

        if not review_list:
            break

        for r in review_list:
            content = r.get("content", {}) or {}
            writer = r.get("writer", {}) or {}
            engagement = r.get("engagement", {}) or {}
            photos = content.get("photos") or []

            try:
                created_at = (
                    datetime.fromtimestamp(int(r["regDate"]) / 1000, tz=KST).isoformat()
                    if r.get("regDate")
                    else None
                )
            except Exception:
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
                    for p in photos
                    if isinstance(p, dict)
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
# 2단계: 단일 매장 크롤링
# ──────────────────────────────────────────────
def crawl_one(url: str, max_reviews: int = MAX_REVIEWS) -> tuple[str, bool]:
    shop_ref = extract_shop_ref_from_url(url)
    if not shop_ref:
        return url, False

    out_path = Path(OUTPUT_DIR) / f"catchtable_{shop_ref}.json"
    if out_path.exists():
        log.info(f"  ⏭️  스킵: {out_path.name}")
        return url, True

    crawled_at = datetime.now(KST).isoformat()
    start = time.time()

    shop_data = get_json(SHOP_API.format(shop_ref=shop_ref), shop_ref=shop_ref)
    if not shop_data or not shop_data.get("data", {}).get("shopDetailVO"):
        log.warning(f"  ❌ 기본 정보 없음: {url}")
        return url, False

    parsed = parse_shop_detail(shop_data)
    shop_name = parsed.get("basic_info", {}).get("name", shop_ref)
    log.info(f"  [{shop_name}] 기본정보 완료")

    menu_data = fetch_menus(shop_ref)
    log.info(f"  [{shop_name}] 메뉴 {len(menu_data['menus'])}개")

    visit_purposes = fetch_visit_purposes(shop_ref)
    log.info(f"  [{shop_name}] 방문목적 {len(visit_purposes)}개")

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
        "visit_purpose_summary": visit_purposes,
        "reviews": reviews,
        "summary": {
            "total_reviews_collected": len(reviews),
            "crawl_duration_seconds": round(time.time() - start, 1),
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log.info(
        f"  ✅ {out_path.name} | "
        f"메뉴:{len(menu_data['menus'])}개 리뷰:{len(reviews)}개 | "
        f"{result['summary']['crawl_duration_seconds']}초"
    )
    return url, True


def crawl_parallel(
    urls: list[str], workers: int = MAX_WORKERS, max_reviews: int = MAX_REVIEWS
) -> dict:
    stats = {"done": 0, "fail": 0}
    total = len(urls)
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(crawl_one, url, max_reviews): url for url in urls}
        for future in as_completed(futures):
            try:
                url, ok = future.result()
            except Exception as e:
                url = futures[future]
                ok = False
                log.warning(f"  작업 예외: {url} | {e}")

            completed += 1
            if ok:
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
    parser = argparse.ArgumentParser(description="CatchTable 전국 크롤러 v4 (speed-tuned)")
    parser.add_argument(
        "--areas",
        nargs="+",
        default=None,
        help=f"크롤링할 지역명. 가능한 값: {list(REGION_CODE_MAP.keys())}"
    )
    parser.add_argument(
        "--skip-discover",
        action="store_true",
        help=f"URL 탐색 생략 — {URLS_FILE} 재사용"
    )
    parser.add_argument(
        "--max-reviews",
        type=int,
        default=MAX_REVIEWS,
        help="매장당 최대 리뷰 수"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help="병렬 스레드 수 (기본 16)"
    )
    args = parser.parse_args()

    if args.areas:
        areas = {n: REGION_CODE_MAP[n] for n in args.areas if n in REGION_CODE_MAP}
        unknown = [n for n in args.areas if n not in REGION_CODE_MAP]
        if unknown:
            log.warning(f"알 수 없는 지역명 (무시됨): {unknown}")
            log.warning(f"사용 가능한 지역: {list(REGION_CODE_MAP.keys())}")
    else:
        areas = REGION_CODE_MAP

    if args.skip_discover and Path(URLS_FILE).exists():
        with open(URLS_FILE, encoding="utf-8") as f:
            urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        log.info(f"[탐색 생략] {len(urls)}개 URL 로드")
    else:
        urls = discover_all(areas)
        Path(URLS_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(URLS_FILE, "w", encoding="utf-8") as f:
            for url in urls:
                f.write(url + "\n")
        log.info(f"URL 목록 저장: {URLS_FILE} ({len(urls)}개)")

    if not urls:
        log.error("수집된 URL이 없습니다.")
        return

    log.info(f"\n[크롤링 시작] {len(urls)}개 매장 / {args.workers}스레드")
    stats = crawl_parallel(urls, workers=args.workers, max_reviews=args.max_reviews)
    log.info(f"\n[완료] 성공:{stats['done']} 실패:{stats['fail']}")
    log.info(f"출력: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()