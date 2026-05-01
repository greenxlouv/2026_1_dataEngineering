"""
캐치테이블 전국 크롤러 v7
- 기존 기본정보/메뉴/방문목적 summary/리뷰 전량 수집 유지
- 개별 리뷰마다 response에서 가능한 정보 최대한 저장
- 개별 리뷰에 식사시간(meal_time) 저장
- 방문목적은 filter별 재조회 후 review_id 기준으로 매칭
실행:
  python3 crawler_v7.py --areas 용산/이태원/한남 --workers 16
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
from urllib.parse import urlencode

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
FILTER_API = API_BASE + "/api/review/v1/shops/{shop_ref}/filters"
SHOP_BASE = "https://app.catchtable.co.kr/ct/shop/"

OUTPUT_DIR = "/Users/bluecloud/Documents/대학/데엔/프젝1/data/incheon"
LOG_DIR = "./logs"
URLS_FILE = "./data/incheon_urls.txt"
FAILED_FILE = "./data/incheon_failed.txt"

PAGE_SIZE = 20
MAX_PAGES = 300

# 가게 단위 병렬 수
MAX_WORKERS = 8

REVIEW_PAGE_SIZE = 12
REVIEW_SORT = "B"
MAX_REVIEWS = 999999

# 리뷰 내부 페이지 병렬 수
REVIEW_PAGE_WORKERS = 4

# 방문목적 필터 병렬 수
VISIT_PURPOSE_WORKERS = 4

MAX_RETRY = 3
RETRY_DELAYS = [5, 15, 30]

REQUEST_DELAY = (0.15, 0.35)
DISCOVER_PAGE_DELAY = (0.25, 0.6)
DISCOVER_AREA_DELAY = (0.7, 1.2)
DISCOVER_ENDPOINT_DELAY = (0.5, 1.0)

COOKIE_STRING = "_gcl_au=1.1.1979824641.1775209724; _hackle_hid=28f588a3-142c-4825-acf2-73fbe167947b; _hackle_did_7dQgTKfweH0n436c9aJLVh84yOncuWxD=28f588a3-142c-4825-acf2-73fbe167947b; ab180ClientId=105817b5-9aa5-4c27-a7fb-f818cd04a9a0; airbridge_migration_metadata__catchtable=%7B%22version%22%3A%221.11.6%22%7D; _hackle_uid_7dQgTKfweH0n436c9aJLVh84yOncuWxD=DxT7xBqpaBWq3xWMvv4u2A; AMP_MKTG_948acc4216=JTdCJTIycmVmZXJyZXIlMjIlM0ElMjJodHRwcyUzQSUyRiUyRnd3dy5nb29nbGUuY29tJTJGJTIyJTJDJTIycmVmZXJyaW5nX2RvbWFpbiUyMiUzQSUyMnd3dy5nb29nbGUuY29tJTIyJTJDJTIyZ2JyYWlkJTIyJTNBJTIyMEFBQUFBQ3VMTEMzMDZ6UWdsdjR3ZTUzM1czclVqWm9QZSUyMiUyQyUyMmdjbGlkJTIyJTNBJTIyRUFJYUlRb2JDaE1JMUxIRTc4bUdsQU1WMWRVV0JSMlR6UlQ2RUFBWUFTQUFFZ0tKenZEX0J3RSUyMiU3RA==; _gcl_gs=2.1.k1$i1777036779$u71127104; x-ct-a=AACghOy8r-uQhuwAAAAKAG1uAgBCAAAAAgBwYQIBp3GUAHF1YxAA13rTAHF1EABLAAAAAgBwYWwCAEsAAAACAHRqAgA3Njg2MV-AsOqZsuycsOognJXttbPqsYTsAAAAGgBuZAIAAAGdxMu_vgB0QXB4ZRIAbGFlcgAAAAUAcGwCAHBwCgAAAIksDO5gjQjIKZsyj2xyMrcBa1tYck=; _gcl_aw=GCL.1777036781.EAIaIQobChMI1LHE78mGlAMV1dUWBR2TzRT6EAAYASAAEgKJzvD_BwE; _ga=GA1.3.1327940188.1775209724; _gid=GA1.3.1015150191.1777036781; _gac_UA-117680739-4=1.1777036781.EAIaIQobChMI1LHE78mGlAMV1dUWBR2TzRT6EAAYASAAEgKJzvD_BwE; airbridge_referrer_campaign_params__catchtable=google.adwords%24%24%7B%22channel%22%3A%22google.adwords%22%2C%22campaign%22%3A%2223753144786%22%2C%22campaign_id%22%3A%2223753144786%22%2C%22ad_group%22%3A%22%22%2C%22ad_group_id%22%3A%22%22%2C%22ad_creative%22%3A%22%22%2C%22ad_creative_id%22%3A%22%22%2C%22term%22%3A%22%22%2C%22sub_id%22%3A%22x%22%2C%22sub_id_1%22%3A%22%22%2C%22sub_id_2%22%3A%22%22%2C%22sub_id_3%22%3A%22%22%7D; airbridge_referrer_campaign_params_cta_parameter__catchtable=%7B%7D; airbridge_referrer_campaign_params_url__catchtable=https%3A//app.catchtable.co.kr/ct/exhibition/2026_brand_week_slnc%3Fairbridge_referrer%3Dairbridge%253Dtrue%2526channel%253Dgoogle.adwords%2526campaign%253D23753144786%2526campaign_id%253D23753144786%2526ad_group%253D%2526ad_group_id%253D%2526ad_creative%253D%2526ad_creative_id%253D%2526term%253D%2526sub_id%253Dx%2526sub_id_1%253D%2526sub_id_2%253D%2526sub_id_3%253D%2526click_id%253DEAIaIQobChMI1LHE78mGlAMV1dUWBR2TzRT6EAAYASAAEgKJzvD_BwE%2526gclid%253DEAIaIQobChMI1LHE78mGlAMV1dUWBR2TzRT6EAAYASAAEgKJzvD_BwE%2526ad_type%253Dclick%26gad_source%3D1%26gad_campaignid%3D23747865303%26gbraid%3D0AAAAACuLLC306zQglv4we533W3rUjZoPe%26gclid%3DEAIaIQobChMI1LHE78mGlAMV1dUWBR2TzRT6EAAYASAAEgKJzvD_BwE%26uniqueListId%3D1777036780781%26isUseExhibitionFilter%3D1%26metaContractedType%3D0%26currentExhibitionKey%3D2026_brand_week_slnc%26serviceType%3DDINING%26sortMethod%3Drecommended%26isInitialDate%3D0; airbridge_referrer_campaign_params_timestamp__catchtable=1777036781331; airbridge_user__catchtable=%7B%22attributes%22%3A%7B%22deviceType%22%3A%22Web-PC%22%2C%22isNativeApp%22%3Afalse%2C%22buildVersion%22%3A20260424165342%2C%22grade%22%3A%22A%22%2C%22userName%22%3A%22%uC190%uBBFC%uC120%22%2C%22userNickname%22%3A%22%uC131%uACF5%uD55C%20%uAC1C%uCC99%uAC00_16867%22%2C%22marketingAgreeYn%22%3A%22Y%22%2C%22isMember%22%3A%22Y%22%2C%22ctRegisterDate%22%3A%222024-12-05%22%2C%22loginChannel%22%3A%22Kakao%22%2C%22lastLoginDate%22%3A%222026-04-24%22%7D%2C%22externalUserID%22%3A%22DxT7xBqpaBWq3xWMvv4u2A%22%7D; __cf_bm=TltvXna_okCbebxOJ5m2eqJ88jQ7Eu4sxn6VNck5LZo-1777039920.946429-1.0.1.1-vTrJQWMlQaNUYEgmyswwq7ozMNzcxXT_ecRHzYRolhK9Erq0_1OBVnq3rME9B6yuhN6ZxebO96lpUkP9z.6br.4uIIdkvRz0RiQZiA6BwdpiGXqOhRvZAsT7IhRxTzAB; _gat_gtag_UA_117680739_4=1; _hackle_session_id_eH0n436c9aJLVh84yOncuWxD=1777040306366.8cc9ee19; _hackle_mkt_7dQgTKfw=%7B%7D; airbridge_session__catchtable=%7B%22id%22%3A%228039eaa7-bb34-4e69-852e-ec520eaad21c%22%2C%22timeout%22%3A1800000%2C%22start%22%3A1777040306358%2C%22end%22%3A1777040316029%7D; _ga_9ENCGJ7C7P=GS2.1.s1777040308$o9$g1$t1777040318$j50$l0$h0; _ga_95C07ZWW1T=GS2.1.s1777040304$o9$g1$t1777040322$j42$l0$h0; _hackle_last_event_ts_eH0n436c9aJLVh84yOncuWxD=1777040322705; AMP_948acc4216=JTdCJTIyZGV2aWNlSWQlMjIlM0ElMjJjY2JmOTk1My1jODJmLTQ3ZDYtOGE0OC1mMDVjNmIzZTFjNmQlMjIlMkMlMjJ1c2VySWQlMjIlM0ElMjJEeFQ3eEJxcGFCV3EzeFdNdnY0dTJBJTIyJTJDJTIyc2Vzc2lvbklkJTIyJTNBMTc3NzA0MDMwNDUzOSUyQyUyMm9wdE91dCUyMiUzQWZhbHNlJTJDJTIybGFzdEV2ZW50VGltZSUyMiUzQTE3NzcwNDAzMjI3MTQlMkMlMjJsYXN0RXZlbnRJZCUyMiUzQTY0MzIlMkMlMjJwYWdlQ291bnRlciUyMiUzQTUlMkMlMjJjb29raWVEb21haW4lMjIlM0ElMjIuY2F0Y2h0YWJsZS5jby5rciUyMiU3RA=="


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

    "인천": "CAT028",
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

_thread_local = threading.local()


def get_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = cf_requests.Session()
    return _thread_local.session


def get_headers(shop_ref: str = "") -> dict:
    referer = f"{SHOP_BASE}{shop_ref}" if shop_ref else "https://app.catchtable.co.kr/"
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Origin": "https://app.catchtable.co.kr",
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
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
                log.warning(f"429 Rate Limited — {wait}초 대기")
                time.sleep(wait)
                continue

            if r.status_code != 200:
                log.warning(f"HTTP {r.status_code}: {url}")
                return None

            return r.json()

        except Exception as e:
            if attempt < MAX_RETRY - 1:
                wait = RETRY_DELAYS[attempt]
                log.warning(f"POST 오류 재시도 {attempt + 1}: {e}")
                time.sleep(wait)
            else:
                log.warning(f"POST 최종 실패: {e}")

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
                log.warning(f"429 Rate Limited — {wait}초 대기")
                time.sleep(wait)
                continue

            if r.status_code != 200:
                log.warning(f"HTTP {r.status_code}: {url}")
                return None

            return r.json()

        except Exception as e:
            if attempt < MAX_RETRY - 1:
                wait = RETRY_DELAYS[attempt]
                log.warning(f"GET 오류 재시도 {attempt + 1}: {e}")
                time.sleep(wait)
            else:
                log.warning(f"GET 최종 실패: {e}")

    return None


def safe_int(val):
    try:
        return int(val) if val is not None and val != "" else None
    except Exception:
        return None


def safe_float(val):
    try:
        return float(val) if val is not None and val != "" else None
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


def discover_area_by_endpoint(
    area_name: str,
    region_code: str,
    endpoint_name: str,
    endpoint_url: str,
    seen: set,
    tx_seed: int = 100,
) -> list[str]:
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
            f"[{area_name}/{endpoint_name}] page={page} "
            f"shops+{new_shops} na+{new_na} 누적={len(refs)} has_more={has_more}"
        )

        if not has_more or not next_offset or next_offset == "0":
            break

        offset = str(next_offset)
        time.sleep(random.uniform(*DISCOVER_PAGE_DELAY))

    return refs


def discover_area(area_name: str, region_code: str) -> list[str]:
    seen = set()
    all_refs = []

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
        log.info(f"[{area_name}/{ep_name}] 완료: {len(refs)}개")
        time.sleep(random.uniform(*DISCOVER_ENDPOINT_DELAY))

    log.info(f"[{area_name}] 최종 {len(all_refs)}개 고유 ref")
    return [SHOP_BASE + ref for ref in all_refs]


def discover_all(areas: dict) -> list[str]:
    all_urls = {}

    for area_name, region_code in areas.items():
        urls = discover_area(area_name, region_code)
        for url in urls:
            ref = extract_shop_ref_from_url(url)
            if ref:
                all_urls[ref] = url
        time.sleep(random.uniform(*DISCOVER_AREA_DELAY))

    result = list(all_urls.values())
    log.info(f"[탐색 완료] 총 {len(result)}개 고유 매장")
    return result


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
            "latitude": safe_float(d.get("lat")),
            "longitude": safe_float(d.get("lon")),
            "phone": d.get("dispShopPhone", ""),
            "price_range": price_text,
            "sns_url": d.get("url", ""),
            "images": images[:5],
            "main_service": d.get("mainService", ""),
        },
        "schedule": business_hours,
        "facilities": facilities,
        "can_reserve": len(d.get("reservationInfoList", []) or []) > 0,
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
    return {
        "menus": parsed,
        "menu_boards": boards,
        "menu_detail_info": {
            "is_kids_menu": detail.get("isKidsMenu"),
            "is_vegan": detail.get("isVeganMenuSubstitute"),
            "is_allergy_substitute": detail.get("isAllergyMenuSubstitute"),
            "alcohol_required": detail.get("isAlcoholOrderRequired"),
            "corkage_guide": detail.get("corkChargeGuide", ""),
            "last_updated": detail.get("lastMenuUpdateDateTime", ""),
        },
    }


def fetch_visit_purposes(shop_ref: str) -> list:
    data = get_json(FILTER_API.format(shop_ref=shop_ref), shop_ref=shop_ref)
    if not data:
        return []

    purposes = data.get("data", {}).get("filters", {}).get("VISIT_PURPOSE", [])
    return [
        {
            "purpose": p.get("filterName"),
            "code": p.get("filterCode"),
            "count": safe_int(p.get("reviewCount")),
        }
        for p in purposes
        if p.get("filterName") or p.get("filterCode")
    ]


def make_review_key(raw_review: dict) -> str | None:
    for key in ["reviewRef", "reviewSeq", "articleSeq", "id"]:
        v = raw_review.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def parse_photo(p: dict) -> dict:
    return {
        "file_id": p.get("fileId"),
        "original_url": p.get("originalUrl") or p.get("url") or p.get("imgUrl"),
        "width": safe_int(p.get("width")),
        "height": safe_int(p.get("height")),
        "ordering": safe_int(p.get("ordering")),
    }


def parse_video(v: dict) -> dict:
    return {
        "file_id": v.get("fileId"),
        "original_url": v.get("originalUrl"),
        "static_thumbnail_url": v.get("staticThumbnailUrl"),
        "dynamic_thumbnail_url": v.get("dynamicThumbnailUrl"),
        "width": safe_int(v.get("width")),
        "height": safe_int(v.get("height")),
        "duration": safe_int(v.get("duration")),
        "ordering": safe_int(v.get("ordering")),
    }


def parse_review_item(r: dict, visit_purpose: dict | None = None) -> dict:
    content = r.get("content", {}) or {}
    writer = r.get("writer", {}) or {}
    engagement = r.get("engagement", {}) or {}
    reservation = r.get("reservation", {}) or {}
    food_type = reservation.get("foodType", {}) or {}

    photos = content.get("photos") or []
    videos = content.get("videos") or []

    try:
        created_at = (
            datetime.fromtimestamp(int(r["regDate"]) / 1000, tz=KST).isoformat()
            if r.get("regDate")
            else None
        )
    except Exception:
        created_at = None

    purpose_list = []
    if visit_purpose:
        purpose_list.append({
            "code": visit_purpose.get("code"),
            "label": visit_purpose.get("purpose") or visit_purpose.get("label"),
        })

    parsed_photos = [parse_photo(p) for p in photos if isinstance(p, dict)]
    parsed_videos = [parse_video(v) for v in videos if isinstance(v, dict)]

    return {
        "review_id": make_review_key(r),

        "review_seq": safe_int(r.get("reviewSeq")),
        "article_seq": safe_int(r.get("articleSeq")),
        "is_editable": r.get("isEditable"),
        "reg_date_raw": r.get("regDate"),
        "created_at": created_at,

        "writer": {
            "user_identifier": writer.get("userIdentifier"),
            "display_name": writer.get("displayName") or writer.get("nickName"),
            "profile_thumb_url": writer.get("profileThumbUrl"),
            "grade": writer.get("grade"),
            "total_review_count": safe_int(writer.get("totalReviewCnt")),
            "total_avg_score": safe_float(writer.get("totalAvgScore")),
        },

        "author": writer.get("displayName") or writer.get("nickName"),
        "author_id": writer.get("userIdentifier"),
        "author_profile_thumb_url": writer.get("profileThumbUrl"),

        "rating": safe_float(content.get("totalScore") or content.get("score")),
        "food_score": safe_float(content.get("tasteScore") or content.get("foodScore")),
        "ambience_score": safe_float(content.get("moodScore") or content.get("ambienceScore")),
        "service_score": safe_float(content.get("serviceScore")),
        "content": content.get("reviewContent") or content.get("text"),
        "review_comment": content.get("reviewComment"),

        "reservation": {
            "reservation_type": reservation.get("reservationType"),
            "is_takeout": reservation.get("isTakeOut"),
            "food_type": {
                "code": food_type.get("code"),
                "label": food_type.get("label"),
            },
        },

        "reservation_type": reservation.get("reservationType"),
        "is_takeout": reservation.get("isTakeOut"),
        "meal_time_code": food_type.get("code"),
        "meal_time_label": food_type.get("label"),

        "visit_purpose": purpose_list,

        "engagement": {
            "reply_count": safe_int(engagement.get("replyCnt") or engagement.get("replyCount")),
            "like_count": safe_int(engagement.get("likeCnt") or engagement.get("likeCount")),
            "is_liked": engagement.get("isLiked"),
        },
        "like_count": safe_int(engagement.get("likeCnt") or engagement.get("likeCount")),
        "reply_count": safe_int(engagement.get("replyCnt") or engagement.get("replyCount")),

        "boss_reply": r.get("bossReply"),
        "blinded": r.get("blinded"),

        "photos": parsed_photos,
        "videos": parsed_videos,

        "images": [
            p.get("original_url")
            for p in parsed_photos
            if p.get("original_url")
        ],
    }


def fetch_review_page(
    shop_ref: str,
    page: int,
    filter_code: str | None = None,
) -> tuple[list, int | None]:
    params = {
        "page": page,
        "size": REVIEW_PAGE_SIZE,
        "sort": REVIEW_SORT,
    }
    if filter_code:
        params["filter"] = filter_code

    url = f"{REVIEW_API.format(shop_ref=shop_ref)}?{urlencode(params)}"
    data = get_json(url, shop_ref=shop_ref)

    if not data:
        return [], None

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

    return review_list, total


def merge_visit_purpose(target_review: dict, purpose: dict) -> None:
    code = purpose.get("code")
    label = purpose.get("purpose") or purpose.get("label")

    if not code and not label:
        return

    current = target_review.setdefault("visit_purpose", [])
    exists = any(p.get("code") == code for p in current)

    if not exists:
        current.append({
            "code": code,
            "label": label,
        })


def fetch_all_review_pages(
    shop_ref: str,
    filter_code: str | None = None,
    total_hint: int | None = None,
    max_items: int | None = None,
) -> list[dict]:
    first_list, total = fetch_review_page(
        shop_ref,
        page=1,
        filter_code=filter_code,
    )

    if not first_list:
        return []

    effective_total = total if total is not None else total_hint

    if max_items is not None and effective_total is not None:
        effective_total = min(effective_total, max_items)

    if effective_total is None:
        all_reviews = list(first_list)
        page = 2

        while True:
            if max_items is not None and len(all_reviews) >= max_items:
                break

            review_list, _ = fetch_review_page(
                shop_ref,
                page=page,
                filter_code=filter_code,
            )

            if not review_list:
                break

            all_reviews.extend(review_list)

            if len(review_list) < REVIEW_PAGE_SIZE:
                break

            page += 1
            time.sleep(random.uniform(*REQUEST_DELAY))

        return all_reviews[:max_items] if max_items is not None else all_reviews

    total_pages = (effective_total + REVIEW_PAGE_SIZE - 1) // REVIEW_PAGE_SIZE

    if total_pages <= 1:
        return first_list[:max_items] if max_items is not None else first_list

    page_results: dict[int, list[dict]] = {1: first_list}

    def fetch_one_page(p: int):
        time.sleep(random.uniform(0.03, 0.18))
        review_list, _ = fetch_review_page(
            shop_ref,
            page=p,
            filter_code=filter_code,
        )
        return p, review_list

    with ThreadPoolExecutor(max_workers=REVIEW_PAGE_WORKERS) as executor:
        futures = [
            executor.submit(fetch_one_page, p)
            for p in range(2, total_pages + 1)
        ]

        for future in as_completed(futures):
            try:
                p, review_list = future.result()
                page_results[p] = review_list or []
            except Exception as e:
                log.warning(f"리뷰 페이지 병렬 수집 실패: filter={filter_code} | {e}")

    all_reviews = []
    for p in sorted(page_results):
        all_reviews.extend(page_results[p])

    return all_reviews[:max_items] if max_items is not None else all_reviews


def fetch_reviews(
    shop_ref: str,
    max_reviews: int = MAX_REVIEWS,
    visit_purposes: list | None = None,
) -> list:
    reviews = []
    review_map: dict[str, dict] = {}

    raw_reviews = fetch_all_review_pages(
        shop_ref,
        filter_code=None,
        total_hint=None,
        max_items=max_reviews,
    )

    for raw in raw_reviews:
        parsed = parse_review_item(raw)
        key = parsed.get("review_id")

        reviews.append(parsed)

        if key:
            review_map[key] = parsed

    purposes = [
        p for p in (visit_purposes or [])
        if p.get("code")
    ]

    def fetch_one_purpose(purpose: dict):
        code = purpose.get("code")
        count_hint = safe_int(purpose.get("count"))

        raw_list = fetch_all_review_pages(
            shop_ref,
            filter_code=code,
            total_hint=count_hint,
            max_items=None,
        )

        return purpose, raw_list

    with ThreadPoolExecutor(max_workers=VISIT_PURPOSE_WORKERS) as executor:
        futures = [
            executor.submit(fetch_one_purpose, purpose)
            for purpose in purposes
        ]

        for future in as_completed(futures):
            try:
                purpose, raw_list = future.result()
            except Exception as e:
                log.warning(f"방문목적 병렬 수집 실패: {e}")
                continue

            matched = 0

            for raw in raw_list:
                key = make_review_key(raw)

                if key and key in review_map:
                    before = len(review_map[key].get("visit_purpose") or [])
                    merge_visit_purpose(review_map[key], purpose)
                    after = len(review_map[key].get("visit_purpose") or [])

                    if after > before:
                        matched += 1

                else:
                    parsed = parse_review_item(raw, visit_purpose=purpose)
                    reviews.append(parsed)

                    parsed_key = parsed.get("review_id")
                    if parsed_key:
                        review_map[parsed_key] = parsed

                    matched += 1

            log.info(f"방문목적 매칭: {purpose.get('code')} 신규 {matched}개")

    return reviews[:max_reviews]


def crawl_one(url: str, max_reviews: int = MAX_REVIEWS) -> tuple[str, bool]:
    shop_ref = extract_shop_ref_from_url(url)

    if not shop_ref:
        return url, False

    out_path = Path(OUTPUT_DIR) / f"catchtable_{shop_ref}.json"

    if out_path.exists():
        log.info(f"스킵: {out_path.name}")
        return url, True

    crawled_at = datetime.now(KST).isoformat()
    start = time.time()

    shop_data = get_json(SHOP_API.format(shop_ref=shop_ref), shop_ref=shop_ref)

    if not shop_data or not shop_data.get("data", {}).get("shopDetailVO"):
        log.warning(f"기본 정보 없음: {url}")
        return url, False

    parsed = parse_shop_detail(shop_data)
    shop_name = parsed.get("basic_info", {}).get("name", shop_ref)
    log.info(f"[{shop_name}] 기본정보 완료")

    menu_data = fetch_menus(shop_ref)
    log.info(f"[{shop_name}] 메뉴 {len(menu_data['menus'])}개")

    visit_purposes = fetch_visit_purposes(shop_ref)
    log.info(f"[{shop_name}] 방문목적 {len(visit_purposes)}개")

    total_reviews = parsed.get("review_summary", {}).get("total_count") or 0

    if total_reviews > 0:
        reviews = fetch_reviews(
            shop_ref,
            max_reviews=max_reviews,
            visit_purposes=visit_purposes,
        )
    else:
        reviews = []

    reviews_with_purpose = sum(1 for r in reviews if r.get("visit_purpose"))
    reviews_with_meal_time = sum(1 for r in reviews if r.get("meal_time_label"))

    log.info(
        f"[{shop_name}] 리뷰 {len(reviews)}개 / "
        f"방문목적 {reviews_with_purpose}개 / "
        f"식사시간 {reviews_with_meal_time}개"
    )

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
            "reviews_with_visit_purpose": reviews_with_purpose,
            "reviews_with_meal_time": reviews_with_meal_time,
            "crawl_duration_seconds": round(time.time() - start, 1),
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log.info(
        f"저장 완료: {out_path.name} | "
        f"메뉴:{len(menu_data['menus'])} 리뷰:{len(reviews)} "
        f"방문목적:{reviews_with_purpose} 식사시간:{reviews_with_meal_time}"
    )

    return url, True


def crawl_parallel(
    urls: list[str],
    workers: int = MAX_WORKERS,
    max_reviews: int = MAX_REVIEWS,
) -> dict:
    stats = {"done": 0, "fail": 0}
    total = len(urls)
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(crawl_one, url, max_reviews): url
            for url in urls
        }

        for future in as_completed(futures):
            try:
                url, ok = future.result()
            except Exception as e:
                url = futures[future]
                ok = False
                log.warning(f"작업 예외: {url} | {e}")

            completed += 1

            if ok:
                stats["done"] += 1
            else:
                stats["fail"] += 1
                with open(FAILED_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now(KST).isoformat()}\t{url}\n")

            log.info(f"[진행] {completed}/{total} | 완료:{stats['done']} 실패:{stats['fail']}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="CatchTable crawler v6 - faster review purpose matching"
    )

    parser.add_argument(
        "--areas",
        nargs="+",
        default=None,
        help=f"크롤링할 지역명. 가능한 값: {list(REGION_CODE_MAP.keys())}",
    )

    parser.add_argument(
        "--skip-discover",
        action="store_true",
        help=f"URL 탐색 생략 — {URLS_FILE} 재사용",
    )

    parser.add_argument(
        "--max-reviews",
        type=int,
        default=MAX_REVIEWS,
        help="매장당 최대 리뷰 수",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help="가게 단위 병렬 스레드 수",
    )

    args = parser.parse_args()

    if args.areas:
        areas = {
            name: REGION_CODE_MAP[name]
            for name in args.areas
            if name in REGION_CODE_MAP
        }

        unknown = [
            name for name in args.areas
            if name not in REGION_CODE_MAP
        ]

        if unknown:
            log.warning(f"알 수 없는 지역명 무시됨: {unknown}")
            log.warning(f"사용 가능한 지역: {list(REGION_CODE_MAP.keys())}")
    else:
        areas = REGION_CODE_MAP

    if args.skip_discover and Path(URLS_FILE).exists():
        with open(URLS_FILE, encoding="utf-8") as f:
            urls = [
                line.strip()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
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

    log.info(f"[크롤링 시작] {len(urls)}개 매장 / {args.workers}스레드")

    stats = crawl_parallel(
        urls,
        workers=args.workers,
        max_reviews=args.max_reviews,
    )

    log.info(f"[완료] 성공:{stats['done']} 실패:{stats['fail']}")
    log.info(f"출력: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()