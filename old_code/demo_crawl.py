"""
발표 시범용 데모 크롤러
- crawler_v7의 함수를 그대로 재사용 (코드 중복 없음)
- --areas로 지역을 지정하면 해당 지역 식당을 자동 탐색
- 지역 미지정 시 하드코딩된 대표 식당 5곳 크롤링
- 리뷰는 매장당 최대 24개 (2페이지)만 수집

실행:
  python3 demo_crawl.py                              # 하드코딩 5곳
  python3 demo_crawl.py --areas 강남                 # 강남 지역 탐색 후 상위 5곳
  python3 demo_crawl.py --areas 홍대/합정/마포 --shop-count 3
  python3 demo_crawl.py --areas 강남 용산/이태원/한남 --max-reviews 12 --workers 5
  

가능한 지역명:
  서울: 강남, 서초, 잠실/송파/강동, 영등포/여의도/강서, 건대/성수/왕십리,
        종로/중구, 홍대/합정/마포, 용산/이태원/한남, 성북/노원/중랑, 구로/관악/동작
  경기: 성남시(분당/판교/성남), 수원, 용인/화성(동탄), 안양/과천, 고양/파주,
        가평/양평, 남양주/의정부, 하남/구리, 부천/안산/시흥/광명 ...
  기타: 인천, 부산, 제주, 대구, 강원, 대전 ...
"""

import argparse
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── crawler_v7의 함수/변수를 그대로 import ──────────────────
import crawler_v7 as v7

# ──────────────────────────────────────────────────────────────
# 데모 기본 설정 (CLI 인자로 덮어쓸 수 있음)
# ──────────────────────────────────────────────────────────────

# 시범용 식당 URL — --areas 미지정 시 사용하는 하드코딩 5곳
DEMO_URLS = [
    "https://app.catchtable.co.kr/ct/shop/CzsdML6SGlcEFcCyVuLmFQ",   # 산수유산장
    "https://app.catchtable.co.kr/ct/shop/-U1h_sqsOlHoi5yGc9_p9g",   # 쉐누
    "https://app.catchtable.co.kr/ct/shop/3raGi2A25sZGLpHk3RQLfg",   # 리오네즈
    "https://app.catchtable.co.kr/ct/shop/rtVCpcqCI2SmotYO6-VCrQ",   # 아난티코드 더 레스토랑
    "https://app.catchtable.co.kr/ct/shop/MCseAMksisV2rlPmrQG1jw",   # 다 안토니오 이탈리안 컨템포러리
]

DEMO_MAX_REVIEWS = 24        # 매장당 최대 리뷰 수 (12개 × 2페이지)
DEMO_WORKERS = 3             # 병렬 스레드 수
DEMO_SHOP_COUNT = 5          # 지역 탐색 시 몇 개 식당을 뽑을지
DEMO_OUTPUT_DIR = "./data/demo"

KST = v7.KST

# ──────────────────────────────────────────────────────────────
# 로깅 (콘솔 출력 강화)
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("demo")


def discover_quick(area_name: str, region_code: str, shop_count: int) -> list[str]:
    """1페이지만 긁어서 shop_count개 ref를 빠르게 수집 (데모 전용).

    - 큰 지역(홍대 등)은 1페이지에 이미 70개+ 노출 → 전 페이지 불필요
    - 작은 지역(가평 등)은 1페이지로 충분
    - 부족하면 waiting/pickup 엔드포인트도 1페이지씩 추가 시도
    """
    refs: list[str] = []
    seen: set[str] = set()

    for ep_name, ep_url in v7.SEARCH_ENDPOINTS.items():
        if len(refs) >= shop_count:
            break

        body = {
            "paging": {"offset": "0", "size": 72},
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
        resp = v7.post_search(ep_url, body, ep_name, tx_id=100)
        if not resp:
            continue

        data = resp.get("data", {}) or {}
        shops = data.get("shopResults", {}).get("shops", []) or []
        na    = data.get("shopResults", {}).get("notAvailableShopRefs", []) or []

        for shop in shops:
            ref = v7.extract_ref_from_shop(shop)
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
        for ref in na:
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)

        log.info(f"[데모 탐색] {area_name}/{ep_name} 1페이지 -> {len(shops)}개")

    return refs[:shop_count]


def load_demo_urls(areas: list[str] | None, shop_count: int) -> list[str]:
    """시범용 URL 목록 결정.

    - areas 지정 시: 각 지역 1페이지만 긁어 shop_count개 선택 (빠른 데모용)
    - areas 미지정 시: 하드코딩된 DEMO_URLS 사용
    """
    if areas:
        # 지역 코드 매핑
        area_map = {
            name: v7.REGION_CODE_MAP[name]
            for name in areas
            if name in v7.REGION_CODE_MAP
        }
        unknown = [name for name in areas if name not in v7.REGION_CODE_MAP]
        if unknown:
            log.warning(f"[데모] 알 수 없는 지역명 무시됨: {unknown}")
            log.warning(f"[데모] 사용 가능한 지역: {list(v7.REGION_CODE_MAP.keys())}")

        if not area_map:
            log.error("[데모] 유효한 지역명이 없습니다.")
            return []

        log.info(f"[데모] 지역 탐색 시작 (목표: {shop_count}개): {list(area_map.keys())}")

        collected: dict[str, str] = {}
        for area_name, region_code in area_map.items():
            if len(collected) >= shop_count:
                break
            refs = discover_quick(area_name, region_code, shop_count - len(collected))
            for ref in refs:
                if ref not in collected:
                    collected[ref] = v7.SHOP_BASE + ref

        selected = list(collected.values())[:shop_count]
        log.info(f"[데모] {len(selected)}개 선택 완료")
        return selected
    else:
        log.info(f"[데모] 하드코딩된 URL {len(DEMO_URLS)}개 사용")
        return DEMO_URLS[:shop_count]


def crawl_one_demo(
    url: str,
    output_dir: str = DEMO_OUTPUT_DIR,
    max_reviews: int = DEMO_MAX_REVIEWS,
) -> tuple[str, bool]:
    """
    v7.crawl_one과 동일 로직이지만 출력 디렉토리·리뷰 수만 데모용으로 변경.
    원본 함수를 직접 재사용한다.
    """
    shop_ref = v7.extract_shop_ref_from_url(url)
    if not shop_ref:
        return url, False

    out_path = Path(output_dir) / f"catchtable_{shop_ref}.json"
    if out_path.exists():
        log.info(f"[스킵] 이미 존재: {out_path.name}")
        return url, True

    crawled_at = datetime.now(KST).isoformat()
    start = time.time()

    # ── 기본 정보 ──────────────────────────────────────────
    shop_data = v7.get_json(v7.SHOP_API.format(shop_ref=shop_ref), shop_ref=shop_ref)
    if not shop_data or not shop_data.get("data", {}).get("shopDetailVO"):
        log.warning(f"[기본정보 없음] {url}")
        return url, False

    parsed = v7.parse_shop_detail(shop_data)
    shop_name = parsed.get("basic_info", {}).get("name", shop_ref)
    log.info(f"   [{shop_name}] 기본정보 완료")

    # ── 메뉴 ───────────────────────────────────────────────
    menu_data = v7.fetch_menus(shop_ref)
    log.info(f"    [{shop_name}] 메뉴 {len(menu_data['menus'])}개")

    # ── 방문목적 ────────────────────────────────────────────
    visit_purposes = v7.fetch_visit_purposes(shop_ref)
    log.info(f"   [{shop_name}] 방문목적 {len(visit_purposes)}개")

    # ── 리뷰 (최대 max_reviews개) ───────────────────────────
    total_reviews = parsed.get("review_summary", {}).get("total_count") or 0
    if total_reviews > 0:
        reviews = v7.fetch_reviews(
            shop_ref,
            max_reviews=max_reviews,
            visit_purposes=visit_purposes,
        )
    else:
        reviews = []

    reviews_with_purpose  = sum(1 for r in reviews if r.get("visit_purpose"))
    reviews_with_meal_time = sum(1 for r in reviews if r.get("meal_time_label"))

    log.info(
        f"   [{shop_name}] 리뷰 {len(reviews)}개 | "
        f"방문목적 {reviews_with_purpose}개 | 식사시간 {reviews_with_meal_time}개"
    )

    # ── 저장 ───────────────────────────────────────────────
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

    elapsed = round(time.time() - start, 1)
    log.info(f"   [{shop_name}] 저장 완료 ({elapsed}초) → {out_path.name}")
    return url, True


def main():
    parser = argparse.ArgumentParser(
        description="CatchTable 데모 크롤러 — 발표용 빠른 시범 크롤링",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--areas",
        nargs="+",
        default=None,
        metavar="지역명",
        help=(
            "크롤링할 지역명 (공백으로 여러 개 지정 가능). "
            f"가능한 값: {list(v7.REGION_CODE_MAP.keys())}. "
            "미지정 시 하드코딩된 5곳 사용."
        ),
    )

    parser.add_argument(
        "--shop-count",
        type=int,
        default=DEMO_SHOP_COUNT,
        metavar="N",
        help=f"크롤링할 식당 수 (기본: {DEMO_SHOP_COUNT})",
    )

    parser.add_argument(
        "--max-reviews",
        type=int,
        default=DEMO_MAX_REVIEWS,
        metavar="N",
        help=f"매장당 최대 리뷰 수 (기본: {DEMO_MAX_REVIEWS})",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=DEMO_WORKERS,
        metavar="N",
        help=f"병렬 스레드 수 (기본: {DEMO_WORKERS})",
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        metavar="경로",
        help="결과 저장 경로 (기본: ./data/demo 또는 --areas 지정 시 ./data/demo/{지역명})",
    )

    args = parser.parse_args()

    # ── 출력 디렉토리 결정 ─────────────────────────────────
    if args.output_dir:
        output_dir = args.output_dir
    elif args.areas:
        safe_name = "_".join(args.areas).replace("/", "_")
        output_dir = f"./data/demo/{safe_name}"
    else:
        output_dir = DEMO_OUTPUT_DIR

    os.makedirs(output_dir, exist_ok=True)

    # ── URL 목록 결정 ──────────────────────────────────────
    urls = load_demo_urls(args.areas, args.shop_count)
    if not urls:
        log.error("크롤링할 URL이 없습니다.")
        return

    log.info("=" * 60)
    log.info(f"   CatchTable 데모 크롤러 시작")
    if args.areas:
        log.info(f"     대상 지역 : {' / '.join(args.areas)}")
    log.info(f"     대상 식당 : {len(urls)}곳")
    log.info(f"     리뷰 상한 : 매장당 {args.max_reviews}개")
    log.info(f"     병렬 스레드: {args.workers}개")
    log.info(f"     저장 경로 : {output_dir}/")
    log.info("=" * 60)

    total_start = time.time()
    stats = {"done": 0, "fail": 0}

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(crawl_one_demo, url, output_dir, args.max_reviews): url
            for url in urls
        }

        for i, future in enumerate(as_completed(futures), 1):
            try:
                url, ok = future.result()
            except Exception as e:
                url = futures[future]
                ok = False
                log.warning(f"예외 발생: {url} | {e}")

            if ok:
                stats["done"] += 1
            else:
                stats["fail"] += 1

            log.info(f"[진행] {i}/{len(urls)} | 완료:{stats['done']} 실패:{stats['fail']}")

    elapsed = round(time.time() - total_start, 1)
    log.info("=" * 60)
    log.info(f"   완료! 총 소요시간: {elapsed}초")
    log.info(f"     성공: {stats['done']}개  실패: {stats['fail']}개")
    log.info(f"     결과 파일: {output_dir}/")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
