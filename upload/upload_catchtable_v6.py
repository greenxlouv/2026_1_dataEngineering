## 실행 python3 upload_catchtable_v6.py --folders ./data/v2/수도권 --workers 4 --chunk-size 1000

import os
import re
import json
import hashlib
import argparse
from datetime import datetime
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

from pymongo import MongoClient, UpdateOne


DB_USERNAME = "db_user1"
DB_PASSWORD = "deproject1"
ENCODED_PASSWORD = quote_plus(DB_PASSWORD)

URI = f"mongodb+srv://{DB_USERNAME}:{ENCODED_PASSWORD}@deproject1.gu6pl9.mongodb.net/?appName=DEproject1"

client = MongoClient(
    URI,
    maxPoolSize=50,
    retryWrites=True,
    serverSelectionTimeoutMS=30000,
)

db = client["DEproject1DB"]

restaurants_col = db["Restaurants"]
reviews_col = db["Reviews"]
menus_col = db["Menus"]


START_DATE = datetime(2024, 7, 17)
USE_DATE_FILTER = False
REVIEW_BULK_CHUNK_SIZE = 1000


def parse_iso_to_date(dt_str):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        return datetime(dt.year, dt.month, dt.day)
    except Exception:
        return None


def parse_price_range(price_str):
    if not price_str:
        return None, None

    s = str(price_str).strip()

    if s == "1만원 미만":
        return 0, 9999

    m = re.match(r"(-?\d+)만원\s*~\s*(-?\d+)만원", s)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a < 0 or b < 0:
            return None, None
        return a * 10000, b * 10000

    m = re.match(r"(-?\d+)만원\s*~", s)
    if m:
        a = int(m.group(1))
        if a < 0:
            return None, None
        return a * 10000, None

    return None, None


def normalize_text(text):
    if text is None:
        return None
    return re.sub(r"\s+", " ", str(text)).strip()


def make_review_dedupe_key(restaurant_id, author, created_at_raw, content, rating, image_count):
    raw_key = f"{restaurant_id}|{author}|{created_at_raw}|{content}|{rating}|{image_count}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def get_total_reviews_collected(raw):
    crawl_summary = raw.get("summary", {}) or {}
    review_summary = raw.get("review_summary", {}) or {}
    reviews = raw.get("reviews", []) or []

    value = (
        crawl_summary.get("total_reviews_collected")
        or review_summary.get("total_count")
        or len(reviews)
        or 0
    )

    try:
        return int(value)
    except Exception:
        return 0


def build_restaurant_doc(raw):
    basic = raw.get("basic_info", {}) or {}
    summary = raw.get("review_summary", {}) or {}

    price_min, price_max = parse_price_range(basic.get("price_range"))

    total_reviews_collected = get_total_reviews_collected(raw)
    is_review_100 = total_reviews_collected >= 100
    is_review_300 = total_reviews_collected >= 300

    visit_purposes_raw = raw.get("visit_purpose_summary", []) or []
    visit_purpose_map = {
        vp["code"]: vp["count"]
        for vp in visit_purposes_raw
        if vp.get("code") and vp.get("count") is not None
    }

    doc = {
        "restaurant_id": raw.get("restaurant_id"),
        "restaurant_name": basic.get("name"),
        "source": "catchtable",

        "shop_ref": raw.get("shop_ref"),
        "source_url": raw.get("source_url"),
        "crawled_at": parse_iso_to_date(raw.get("crawled_at")),

        "name_en": basic.get("name_en"),
        "alias": basic.get("alias"),
        "category": basic.get("category"),
        "area": basic.get("area"),
        "description": basic.get("description"),
        "address": basic.get("address"),
        "address_old": basic.get("address_old"),
        "latitude": basic.get("latitude"),
        "longitude": basic.get("longitude"),
        "phone_num": basic.get("phone"),
        "sns_url": basic.get("sns_url"),
        "images": basic.get("images", []),

        "price_range_raw": basic.get("price_range"),
        "price_min": price_min,
        "price_max": price_max,

        "schedule": raw.get("schedule", []),
        "facilities": raw.get("facilities", []),
        "can_reserve": raw.get("can_reserve"),
        "save_count": raw.get("save_count"),

        "total_reviews_collected": total_reviews_collected,
        "is_review_100": is_review_100,
        "is_review_300": is_review_300,

        "visit_purpose_summary": visit_purpose_map,

        "review_summary": {
            "total_count": summary.get("total_count"),
            "rating": summary.get("rating"),
            "food_score": summary.get("food_score"),
            "ambience_score": summary.get("ambience_score"),
            "service_score": summary.get("service_score"),
        },
    }

    return doc


def build_menu_docs(raw):
    restaurant_name = (raw.get("basic_info", {}) or {}).get("name")
    restaurant_id = raw.get("restaurant_id")
    menus_raw = raw.get("menus", []) or []

    docs = []

    for m in menus_raw:
        name = normalize_text(m.get("name"))
        if not name:
            continue

        doc = {
            "restaurant_id": restaurant_id,
            "restaurant_name": restaurant_name,
            "name": name,
            "description": normalize_text(m.get("description")),
            "is_representative": bool(m.get("is_representative", False)),
            "is_recommended": bool(m.get("is_recommended", False)),
            "embed_text": f"{name} {normalize_text(m.get('description')) or ''}".strip(),
        }
        docs.append(doc)

    return docs


def build_review_docs(raw, end_date=None):
    restaurant_id = raw.get("restaurant_id")
    restaurant_name = (raw.get("basic_info", {}) or {}).get("name")
    reviews = raw.get("reviews", []) or []

    if end_date is None:
        end_date = datetime.now()

    docs = []

    for r in reviews:
        created_dt = parse_iso_to_date(r.get("created_at"))

        if not created_dt:
            continue

        if USE_DATE_FILTER and not (START_DATE <= created_dt <= end_date):
            continue

        content = normalize_text(r.get("content"))
        author = r.get("author")
        rating = r.get("rating")
        images = r.get("images", []) or []
        source_review_id = r.get("review_id")

        dedupe_key = make_review_dedupe_key(
            restaurant_id=restaurant_id,
            author=author,
            created_at_raw=r.get("created_at"),
            content=content,
            rating=rating,
            image_count=len(images),
        )

        visit_purpose_codes = [
            vp.get("code")
            for vp in (r.get("visit_purpose") or [])
            if vp.get("code")
        ]

        doc = {
            "restaurant_id": restaurant_id,
            "restaurant_name": restaurant_name,
            "source": "catchtable",
            "source_review_id": source_review_id,
            "review_dedupe_key": dedupe_key,
            "date": created_dt,
            "visited_at": r.get("visited_at"),
            "content": content,
            "author": {
                "nickname": author
            },
            "rating": rating,
            "food_score": r.get("food_score"),
            "ambience_score": r.get("ambience_score"),
            "service_score": r.get("service_score"),
            "like_count": r.get("like_count"),
            "image_urls": images,
            "visit_purpose_codes": visit_purpose_codes,
            "meal_time_code": r.get("meal_time_code"),
            "reservation_type": r.get("reservation_type"),
        }

        docs.append(doc)

    return docs


def upsert_restaurant(doc):
    rid = doc.get("restaurant_id")
    if not rid:
        raise ValueError("restaurant_id 없음")

    restaurants_col.update_one(
        {"restaurant_id": rid},
        {"$set": doc},
        upsert=True,
    )


def upsert_menus(menu_docs, restaurant_id):
    if not restaurant_id:
        return 0

    menus_col.delete_many({"restaurant_id": restaurant_id})

    if not menu_docs:
        return 0

    menus_col.insert_many(menu_docs, ordered=False)
    return len(menu_docs)


def upsert_reviews(review_docs):
    if not review_docs:
        return 0

    total_changed = 0

    for batch in chunked(review_docs, REVIEW_BULK_CHUNK_SIZE):
        operations = []

        for doc in batch:
            if doc.get("source_review_id") is not None:
                filter_q = {
                    "source": "catchtable",
                    "restaurant_id": doc["restaurant_id"],
                    "source_review_id": doc["source_review_id"],
                }
            else:
                filter_q = {
                    "source": "catchtable",
                    "restaurant_id": doc["restaurant_id"],
                    "review_dedupe_key": doc["review_dedupe_key"],
                }

            operations.append(
                UpdateOne(
                    filter_q,
                    {"$set": doc},
                    upsert=True,
                )
            )

        result = reviews_col.bulk_write(
            operations,
            ordered=False,
            bypass_document_validation=True,
        )

        total_changed += (
            result.upserted_count
            + result.modified_count
            + result.matched_count
        )

    return total_changed


def main():
    parser = argparse.ArgumentParser(description="캐치테이블 JSON → MongoDB 적재")

    parser.add_argument(
        "--folders",
        nargs="+",
        required=True,
        help="JSON 파일이 있는 폴더 경로 여러 개 가능",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="병렬 처리 스레드 수 기본 4",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="리뷰 bulk_write chunk 크기 기본 1000",
    )

    parser.add_argument(
        "--min-reviews",
        type=int,
        default=20,
        help="최소 리뷰 수. 기본 16이면 리뷰 15개 이하 가게는 스킵",
    )

    args = parser.parse_args()

    global REVIEW_BULK_CHUNK_SIZE
    REVIEW_BULK_CHUNK_SIZE = args.chunk_size

    DATA_FOLDERS = args.folders
    WORKERS = args.workers
    MIN_REVIEWS = args.min_reviews
    END_DATE = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    print("적재 기간: 전 기간 리뷰")
    print(f"대상 폴더: {DATA_FOLDERS}")
    print(f"병렬 스레드: {WORKERS}")
    print(f"리뷰 bulk chunk size: {REVIEW_BULK_CHUNK_SIZE}")
    print(f"최소 리뷰 수: {MIN_REVIEWS}개 이상\n")

    total_restaurants = 0
    total_menus = 0
    total_reviews = 0
    processed_files = 0
    skipped_low_review = 0
    failed_files = 0

    all_files = []

    for folder in DATA_FOLDERS:
        if not os.path.exists(folder):
            print(f"폴더 없음 스킵: {folder}")
            continue

        for filename in os.listdir(folder):
            if filename.endswith(".json") and filename.startswith("catchtable_"):
                all_files.append(os.path.join(folder, filename))

    print(f"캐치테이블 데이터 적재 시작: 총 {len(all_files)}개 파일\n")

    def process_file(file_path):
        filename = os.path.basename(file_path)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            total_reviews_collected = get_total_reviews_collected(raw)

            if total_reviews_collected < MIN_REVIEWS:
                return filename, 0, 0, 0, "LOW_REVIEW", total_reviews_collected

            restaurant_doc = build_restaurant_doc(raw)
            menu_docs = build_menu_docs(raw)
            review_docs = build_review_docs(raw, END_DATE)

            upsert_restaurant(restaurant_doc)
            menu_count = upsert_menus(menu_docs, raw.get("restaurant_id"))
            review_count = upsert_reviews(review_docs)

            return filename, 1, menu_count, review_count, None, total_reviews_collected

        except Exception as e:
            return filename, 0, 0, 0, str(e), 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_file, file_path): file_path
            for file_path in all_files
        }

        for future in as_completed(futures):
            filename, restaurant_count, menu_count, review_count, err, total_count = future.result()

            if err == "LOW_REVIEW":
                skipped_low_review += 1
                print(f"{filename} | 스킵: 리뷰 {total_count}개")
                continue

            if err:
                failed_files += 1
                print(f"{filename} | 적재 실패: {err}")
                continue

            total_restaurants += restaurant_count
            total_menus += menu_count
            total_reviews += review_count
            processed_files += 1

            print(
                f"{filename} | 총리뷰 {total_count}개 | "
                f"메뉴 {menu_count}개 | 리뷰 처리 {review_count}개"
            )

    print("\n==============================")
    print(f"처리 완료 파일 수     : {processed_files}")
    print(f"리뷰 20개 이하 스킵  : {skipped_low_review}")
    print(f"실패 파일 수          : {failed_files}")
    print(f"매장 처리 수          : {total_restaurants}")
    print(f"메뉴 처리 수          : {total_menus}")
    print(f"리뷰 처리 수          : {total_reviews}")
    print("==============================")


if __name__ == "__main__":
    main()