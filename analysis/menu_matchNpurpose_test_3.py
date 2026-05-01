"""
menu_extract_final.py

목적:
  1. 유효 리뷰(15자+) 100개 이상 + 메뉴 있는 식당 → 가중치 매칭으로 Top5 추출
  2. 메뉴 없는 식당 → Kiwi 명사 추출로 폴백
  3. 결과를 MongoDB Restaurants 컬렉션 top_menus 필드에 저장
  4. CSV로도 저장

실행:
  python3 menu_matchNpurpose_test_3.py
  python3 menu_matchNpurpose_test_3.py --min-reviews 100 --workers 4 --sample 20 --test
  python3 menu_matchNpurpose_test_3.py --min-reviews 100 --workers 4
"""

import re
import csv
import argparse
import multiprocessing
from collections import Counter, defaultdict
from urllib.parse import quote_plus

from pymongo import MongoClient
from kiwipiepy import Kiwi

# =========================================================
# 1. 설정
# =========================================================
MONGO_URI = "mongodb+srv://db_user1:deproject1@deproject1.gu6pl9.mongodb.net/?appName=DEproject1"
OUTPUT_CSV = "menu_extract_results.csv"
MIN_VALID_REVIEWS = 100
NUM_WORKERS = 8

PURPOSE_LABELS = {
    "ANNIVERSARY":      "기념일",
    "BIRTHDAY":         "생일",
    "BLIND_DATE":       "소개팅",
    "BUSINESS_MEETING": "비즈니스 미팅",
    "COMPANY_OUTING":   "회식",
    "DATE":             "데이트",
    "DRINK_ALONE":      "혼술",
    "EAT_ALONE":        "혼밥",
    "FAMILY_EVENT":     "가족 행사",
    "FAMILY_MEAL":      "가족 식사",
    "GROUP_MEETING":    "단체 모임",
    "MARRIAGE_MEETING": "맞선",
    "RETURN_GIFT":      "답례",
    "SOCIAL_GATHERING": "친목 모임",
    "TRAVEL":           "여행",
}

# 보완된 불용어 (기존 + 일반적 음식/상태 단어 추가)
STOPWORDS = {
    # 기존
    "사계절", "옛날", "세트", "추가",
    "사이다", "콜라", "탄산수", "음료",
    "디저트", "하우스", "프리미엄", "제철", "시즈널",
    "모둠", "무침", "암소", "반상", "구이", "숯불", "돌판", "볶음",
    # 추가: 너무 일반적인 음식/상태 단어
    "메뉴", "음식", "요리", "식사", "밥", "국물",
    "점심", "저녁", "런치", "디너",
    "추천", "최고", "맛있", "조금", "정말", "진짜", "너무",
    "오늘", "이번", "항상", "매번", "같이",
    "친절", "서비스", "분위기", "자리", "직원",
}


# =========================================================
# 2. 유틸 함수
# =========================================================
def extract_nouns(kiwi, text, exclude=None):
    tokens = kiwi.analyze(text)[0][0]
    nouns = [t.form for t in tokens if t.tag in ("NNG", "NNP") and len(t.form) >= 2]
    if exclude:
        nouns = [n for n in nouns if n not in exclude]
    return nouns


def get_name_keywords(kiwi, name):
    return set(extract_nouns(kiwi, re.sub(r'[^\w\s]', '', name)))


def extract_menu_keywords(kiwi, menu_name, exclude=None):
    cleaned = re.sub(r'\(.*?\)', '', menu_name)
    cleaned = re.sub(r'[^\w\s]', '', cleaned)
    return extract_nouns(kiwi, cleaned, exclude=exclude)


# =========================================================
# 3. 가중치 매칭 (메뉴 있는 식당)
# =========================================================
def match_menus_weighted(kiwi, official_menus, review_contents, restaurant_name="", top_n=5):
    name_kws = get_name_keywords(kiwi, restaurant_name)
    exclude = STOPWORDS | name_kws

    # [수정] 직접 문자열 매칭 먼저 시도
    direct_counter = Counter()
    for content in review_contents:
        for menu_name in official_menus:
            if menu_name in content:
                direct_counter[menu_name] += 1

    # 직접 매칭 결과가 충분하면 바로 반환
    if len(direct_counter) >= top_n:
        return [(m, c) for m, c in direct_counter.most_common(top_n) if c > 0]

    # 직접 매칭 부족 → 가중치 키워드 매칭으로 보완
    menu_keywords = {}
    for m in official_menus:
        kws = extract_menu_keywords(kiwi, m, exclude=exclude)
        if kws:
            menu_keywords[m] = kws

    if not menu_keywords:
        return [(m, c) for m, c in direct_counter.most_common(top_n) if c > 0]

    kw_menu_count = {}
    for kws in menu_keywords.values():
        for kw in kws:
            kw_menu_count[kw] = kw_menu_count.get(kw, 0) + 1

    weighted_counter = Counter(direct_counter)  # 직접 매칭 결과 시작점으로

    for content in review_contents:
        scores = {
            m: sum(1.0 / kw_menu_count[kw] for kw in kws if kw in content)
            for m, kws in menu_keywords.items()
        }
        scores = {m: s for m, s in scores.items() if s > 0}
        if scores:
            max_s = max(scores.values())
            for m, s in scores.items():
                if s == max_s:
                    weighted_counter[m] += 1

    return [(m, c) for m, c in weighted_counter.most_common(top_n) if c > 0]


# =========================================================
# 4. Kiwi 폴백 (메뉴 없는 식당)
# =========================================================
def extract_top_nouns(kiwi, review_contents, restaurant_name="", top_n=5):
    name_kws = get_name_keywords(kiwi, restaurant_name)
    exclude = STOPWORDS | name_kws

    counter = Counter()
    for content in review_contents:
        nouns = extract_nouns(kiwi, content, exclude=exclude)
        counter.update(nouns)

    return [(noun, cnt) for noun, cnt in counter.most_common(top_n) if cnt > 0]


# =========================================================
# 5. 워커 함수 (multiprocessing)
# =========================================================
def process_restaurant(args):
    restaurant_id, sample_mode, test_mode = args
    kiwi = Kiwi()
    db = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=10000,  # 10초 안에 연결 안되면 포기
        connectTimeoutMS=10000,
        socketTimeoutMS=30000)["DEproject1DB"]

    try:
        
        # 이미 처리된 식당이면 스킵
        existing = db.ex_Restaurants.find_one(
            {"restaurant_id": restaurant_id, "top_menus": {"$exists": True}},
            {"_id": 1}
        )
        if existing:
            return [{"restaurant_id": restaurant_id, "skipped": True}]
        

        restaurant = db.ex_Restaurants.find_one(
            {"restaurant_id": restaurant_id},
            {"restaurant_name": 1, "_id": 0}
        )
        name = restaurant.get("restaurant_name", "이름없음") if restaurant else "이름없음"

        official_menus = [
            m["name"] for m in db.ex_Menus.find(
                {"restaurant_id": restaurant_id},
                {"name": 1, "_id": 0}
            ) if m.get("name")
        ]

        all_reviews = list(db.ex_Reviews.find(
            {
                "restaurant_id": restaurant_id,
                "content": {"$exists": True, "$nin": [None, ""]},
                "$expr": {"$gte": [{"$strLenCP": "$content"}, 15]}
            },
            {"content": 1, "visit_purpose_codes": 1, "_id": 0}
        ))

        contents = [r["content"] for r in all_reviews]
        has_menu = bool(official_menus)

        # [수정] 메뉴 유무에 따라 분기
        if has_menu:
            top5 = match_menus_weighted(kiwi, official_menus, contents, restaurant_name=name)
            method = "weighted_match"
        else:
            top5 = extract_top_nouns(kiwi, contents, restaurant_name=name)
            method = "kiwi_fallback"

        # 방문 목적별 Top5
        purpose_reviews = defaultdict(list)
        for r in all_reviews:
            for code in (r.get("visit_purpose_codes") or []):
                purpose_reviews[code].append(r["content"])

        top_menus_by_purpose = {}
        for code, pc in purpose_reviews.items():
            if has_menu:
                result = match_menus_weighted(kiwi, official_menus, pc, restaurant_name=name)
            else:
                result = extract_top_nouns(kiwi, pc, restaurant_name=name)
            if result:
                top_menus_by_purpose[code] = [m for m, _ in result]

        # [수정] MongoDB 저장
        if not sample_mode:
            col_name = "Restaurants_test" if test_mode else "ex_Restaurants"
            db[col_name].update_one(
                {"restaurant_id": restaurant_id},
                {"$set": {
                    "top_menus": [m for m, _ in top5],
                    "top_menus_by_purpose": top_menus_by_purpose,
                    "top_menus_method": method,
                }},
                upsert=True
            )

        row = {
            "restaurant_id": restaurant_id,
            "restaurant_name": name,
            "has_menu": has_menu,
            "method": method,
            "valid_review_count": len(all_reviews),
        }
        for i, (menu, cnt) in enumerate(top5, 1):
            row[f"top{i}_menu"] = menu
            row[f"top{i}_count"] = cnt

        # 샘플 모드일 때 방문 목적별 Top5 출력
        if sample_mode:
            print(f"\n▶ {name} (유효 리뷰 {len(all_reviews)}건 | {method})")
            print(f"  [전체 Top5]")
            for i, (menu, cnt) in enumerate(top5, 1):
                print(f"    {i}. {menu}: {cnt}회")
            if top_menus_by_purpose:
                print(f"  [방문 목적별 Top5]")
                for code, menus in top_menus_by_purpose.items():
                    label = PURPOSE_LABELS.get(code, code)
                    purpose_cnt = len(purpose_reviews.get(code, []))
                    print(f"    [{label}] ({purpose_cnt}건)")
                    for i, menu in enumerate(menus, 1):
                        print(f"      {i}. {menu}")
            else:
                print(f"  [방문 목적별 Top5] (방문 목적 태그 없음)")

        return [row]

    except Exception as e:
        return [{"restaurant_id": restaurant_id, "error": str(e)}]


# =========================================================
# 6. 메인
# =========================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-reviews", type=int, default=MIN_VALID_REVIEWS)
    parser.add_argument("--workers", type=int, default=NUM_WORKERS)
    parser.add_argument("--sample", type=int, default=None, help="샘플 테스트 시 식당 수")
    parser.add_argument("--test", action="store_true", help="Restaurants_test 컬렉션에 저장")
    args = parser.parse_args()

    sample_mode = args.sample is not None
    test_mode = args.test

    print("=" * 60)
    print("menu_extract_final.py 시작")
    print(f"최소 유효 리뷰: {args.min_reviews}개 | workers: {args.workers}")
    print(f"샘플 모드: {sample_mode} {'(' + str(args.sample) + '개)' if sample_mode else ''}")
    print(f"저장 컬렉션: {'Restaurants_test' if test_mode else 'ex_Restaurants'}")
    print("=" * 60)

    db = MongoClient(MONGO_URI)["DEproject1DB"]

    print(f"\n[1단계] 유효 리뷰 {args.min_reviews}개+ 식당 추출 중...")
    pipeline = [
        {"$match": {
            "content": {"$exists": True, "$nin": [None, ""]},
            "$expr": {"$gte": [{"$strLenCP": "$content"}, 15]}
        }},
        {"$group": {"_id": "$restaurant_id", "cnt": {"$sum": 1}}},
        {"$match": {"cnt": {"$gte": args.min_reviews}}}
    ]
    valid_ids = [r["_id"] for r in db.ex_Reviews.aggregate(pipeline)]
    print(f"  → 유효 리뷰 {args.min_reviews}개+ 식당: {len(valid_ids)}개")

    # 메뉴 있는 식당 / 없는 식당 분리
    ids_with_menus = set(db.ex_Menus.distinct("restaurant_id", {"restaurant_id": {"$in": valid_ids}}))
    ids_without_menus = [rid for rid in valid_ids if rid not in ids_with_menus]
    all_ids = list(ids_with_menus) + ids_without_menus

    print(f"  → 메뉴 있는 식당: {len(ids_with_menus)}개 | 메뉴 없는 식당: {len(ids_without_menus)}개")

    if sample_mode:
        all_ids = all_ids[:args.sample]
        print(f"  → 샘플 {args.sample}개만 처리\n")

    print(f"\n[2단계] 병렬 처리 시작 (workers={args.workers})...")
    all_rows = []
    task_args = [(rid, sample_mode, test_mode) for rid in all_ids]
    skipped_count = 0
    with multiprocessing.Pool(args.workers) as pool:
        for i, result_rows in enumerate(pool.imap_unordered(process_restaurant, task_args), 1):
            if result_rows:
                for row in result_rows:
                    if "error" in row:
                        print(f"  ❌ {row['restaurant_id']}: {row['error']}")
                    elif "skipped" in row:
                        skipped_count += 1
                    else:
                        all_rows.append(row)
            if i % 100 == 0 or i == len(all_ids):
                print(f"  진행: {i}/{len(all_ids)} ({i/len(all_ids)*100:.1f}%)")
    print(f"  스킵된 식당 수: {skipped_count}")  

    print(f"\n[3단계] CSV 저장 중... ({len(all_rows)}행)")
    fieldnames = [
        "restaurant_id", "restaurant_name",
        "has_menu", "method", "valid_review_count"
    ]
    for i in range(1, 6):
        fieldnames += [f"top{i}_menu", f"top{i}_count"]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)

    print(f"완료! → {OUTPUT_CSV}")
    if not sample_mode:
        print("MongoDB Restaurants 컬렉션 top_menus 필드도 업데이트됨")


if __name__ == "__main__":
    main()
