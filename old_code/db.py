import os
import json
from pymongo import MongoClient
from datetime import datetime

# 1. 아틀라스 연결 정보
URI = "mongodb+srv://<USER_NAME>:<PASSWORD>@deproject1db.yoew6c3.mongodb.net/?appName=DEproject1DB"
client = MongoClient(URI)
db = client['DEproject1DB']  # 전체 프로젝트(데이터베이스) 이름
reviews_col = db['Reviews']  # 컬렉션

# 2. 설정: 날짜 범위 및 폴더 경로
START_DATE = datetime(2024, 7, 17)
END_DATE = datetime(2025, 4, 7)
DATA_FOLDER = "season1_data"  # json 파일이 들어있는 폴더

def parse_naver_date(date_str):
    """
    연도가 있는 완벽한 날짜("2024-03-12" 또는 "2024.03.12")만 
    datetime 객체로 변환하고, 연도가 없는 형태("4.4.토")는 버립니다.
    """
    if not date_str:
        return None
    
    try:
        # "2024.03.12" 같은 형태가 섞여 있다면 하이픈(-)으로 통일
        clean_str = date_str.replace('.', '-')
        
        # %Y는 4자리 연도를 요구하므로, 없으면 예외(ValueError)가 발생해 아래로 빠집니다.
        return datetime.strptime(clean_str, "%Y-%m-%d")
        
    except ValueError:
        # 연도가 없거나 형식이 완전히 틀린 불량 데이터는 무시
        return None

def process_review_file(file_path):
    # JSON 파일 읽기 (가장 바깥이 리스트 [] 인 구조)
    with open(file_path, 'r', encoding='utf-8') as f:
        raw_reviews = json.load(f) 
    
    bulk_docs = []
    
    for r in raw_reviews:
        # 1. 날짜 파싱 및 기간 필터링
        clean_date = parse_naver_date(r.get('date'))
        if not clean_date:
            continue
            
        # 2. 기간 필터링 (2024-07-17 ~ 2025-04-07)
        if not (START_DATE <= clean_date <= END_DATE):
            continue # 기간 밖의 데이터도 탈락!

        # 3. followers 문자열 형변환 안전 처리 ("0" -> 0)
        followers_str = str(r.get('author_followers', '0'))
        followers_int = int(followers_str) if followers_str.isdigit() else 0

        # 4. visit_context 안전 추출 (없으면 빈 딕셔너리 사용)
        visit_context = r.get('visit_context', {})
        
        # 5. DB 설계도에 맞춘 최종 문서 조립
        doc = {
            "restaurant_name": r.get('restaurant_name'),
            "source": "naver",
            "source_review_id": r.get('review_id'), # 원본 사이트 고유 ID
            "date": clean_date,
            "content": r.get('review_text'), # 필드명 매핑
            "author": {
                "nickname": r.get('author_nickname'),
                "total_reviews": r.get('author_total_reviews'),
                "avg_rating": r.get('author_avg_rating'),
                "followers": followers_int
            },
            "visit_context": {
                "is_reserved": visit_context.get('예약 여부'),
                "waiting_time": visit_context.get('대기 시간'),
                "companions": visit_context.get('동행인'),
                "visit_purpose": visit_context.get('기타 정보')
            },
            "tags": r.get('tags', [])
        }
        bulk_docs.append(doc)
    
    return bulk_docs

def main():
    total_count = 0
    
    # 폴더 존재 여부 확인
    if not os.path.exists(DATA_FOLDER):
        print(f"❌ 폴더를 찾을 수 없습니다: {DATA_FOLDER}")
        print(f"파이썬 파일과 같은 위치에 '{DATA_FOLDER}' 폴더가 있는지 확인해주세요.")
        return

    print("🚀 데이터 적재를 시작합니다...\n")

    # season1_data 폴더 안의 모든 json 파일 순회
    for filename in os.listdir(DATA_FOLDER):
        if filename.endswith(".json"):
            file_path = os.path.join(DATA_FOLDER, filename)
            docs = process_review_file(file_path)
            
            if docs:
                # 몽고DB에 일괄 삽입 (속도 향상을 위해 insert_many 사용)
                reviews_col.insert_many(docs)
                total_count += len(docs)
                print(f"✅ {filename} : 유효 데이터 {len(docs)}건 적재 완료")
            else:
                print(f"⚠️ {filename} : 조건에 맞는 데이터가 없어 건너뜁니다.")

    print(f"\n✨ 적재 완료! 총 {total_count}건의 정예 리뷰가 DB에 저장되었습니다.")

if __name__ == "__main__":
    main()