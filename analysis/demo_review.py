import os
import json
import datetime
import torch
from pymongo import MongoClient, ReturnDocument
from urllib.parse import quote_plus
from kiwipiepy import Kiwi
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans
from sklearn.metrics import pairwise_distances_argmin_min
import warnings

warnings.filterwarnings("ignore")

# 1. 환경 설정 
WORKER_NAME = "demo" 
BATCH_SIZE = 15        
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

# 2. DB 접속 (기존 정보 유지)
DB_USERNAME = "db_user1"
DB_PASSWORD = "deproject1"
ENCODED_PASSWORD = quote_plus(DB_PASSWORD)
URI = f"mongodb+srv://{DB_USERNAME}:{ENCODED_PASSWORD}@deproject1.gu6pl9.mongodb.net/?appName=DEproject1"

client = MongoClient(URI, maxPoolSize=50)
db = client["DEproject1DB"]
restaurants_col = db["Restaurants"]
reviews_col = db["Reviews"]

# 시연용 적재를 위한 컬렉션 정의
demo_col = db["demo_sentiment"]

# 3. 모델 로드 (결과 동일성 유지)
kiwi = Kiwi(num_workers=4)
embedder = SentenceTransformer('jhgan/ko-sroberta-multitask')

TARGET_KEYWORDS = [
    "주차", "주차장", "발렛", "공영주차장", "골목", "뚜벅이", "역에서",
    "웨이팅", "대기", "예약", "오픈런", "회전율", "줄", "캐치테이블",
    "분위기", "인테리어", "조명", "뷰", "시끄러", "소음", "조용", "음악", "감성",
    "화장실", "청결", "더러", "깨끗", "테이블", "간격", "좁", "넓", "룸",
    "직원", "알바", "사장님", "친절", "불친절", "서비스", "응대", "설명", "눈치",
    "맛", "존맛", "꿀맛", "노맛", "JMT", "풍미", "식감", "신선",
    "짜", "달", "맵", "싱겁", "느끼", "비리", "질기", "촉촉",
    "양", "푸짐", "가성비", "비싸", "창렬", "혜자", "돈 아깝"
]

def get_batch_tasks(limit=3):
    """process_status(pending) 상관없이 리뷰 100개 이상인 식당을 무조건 가져옵니다."""
    tasks = list(restaurants_col.find({"is_review_100": True}).limit(limit))
    return tasks

def process_reviews():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    output_file = f"processed_data_{WORKER_NAME}.jsonl"
    print(f"🚀 작업을 시작합니다! 결과물: {output_file} 및 MongDB [demo_sentiment] 컬렉션")

    with open(output_file, 'a', encoding='utf-8') as f_out:
        
        tasks = get_batch_tasks(3) 
        
        if not tasks:
            print("🏁 조건에 맞는 식당 데이터가 DB에 없습니다!")
            return

        for i, rest in enumerate(tasks):
            target_id = rest["restaurant_id"]
            
            print(f"\n🔎 [{i + 1}/3] 식당 ID: {target_id} 데이터 추출 및 임베딩 & AI 군집화 중...")
            
            # 리뷰 데이터 조회
            cursor = reviews_col.find(
                {"restaurant_id": target_id, "content": {"$regex": ".{10,}"}},
                {"content": 1, "_id": 0}
            )
            
            # 키워드 필터링
            valid_raw_reviews = [
                doc["content"] for doc in cursor 
                if any(k in doc["content"] for k in TARGET_KEYWORDS)
            ]

            if not valid_raw_reviews:
                print("⚠️ 유효한 키워드 리뷰가 없어 건너뜁니다.")
                continue

            # Kiwi 문장 분리 
            all_sentences = []
            for sents in kiwi.split_into_sents(valid_raw_reviews):
                for sent in sents:
                    s_text = sent.text.strip()
                    if any(k in s_text for k in TARGET_KEYWORDS):
                        all_sentences.append(s_text)

            # AI 임베딩 및 군집화
            if len(all_sentences) >= 10:
                num_clusters = min(80, len(all_sentences))
                embeddings = embedder.encode(all_sentences, batch_size=16, device=DEVICE, show_progress_bar=False)
                kmeans = KMeans(n_clusters=num_clusters, random_state=42, n_init="auto").fit(embeddings)
                closest, _ = pairwise_distances_argmin_min(kmeans.cluster_centers_, embeddings)
                final_80 = [all_sentences[idx] for idx in closest]

                # DB 구조
                result = {
                    "restaurant_id": target_id,
                    "representative_sentences": final_80,
                    "sentence_count": len(final_80)
                }

                # 파일 저장
                f_out.write(json.dumps(result, ensure_ascii=False) + "\n")
                f_out.flush()
                
                # MongoDB 적재
                demo_col.update_one(
                    {"restaurant_id": target_id},
                    {"$set": result},
                    upsert=True
                )
                
                print(f"✅ {len(final_80)}개의 대표 문장 및 기본 스키마 DB(demo_sentiment) 적재 완료!")
            else:
                print(f"⚠️ 문장 개수가 부족하여 AI 군집화를 생략합니다. ({len(all_sentences)}개)")
        
        print("\n🏁 3개 식당 처리가 모두 완료되었습니다!")

if __name__ == "__main__":
    process_reviews()