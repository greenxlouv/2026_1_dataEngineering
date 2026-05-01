# 🍽️ Beyond Ratings: Understanding Restaurants Through Reviews
### A CatchTable-Based Restaurant Review Analysis
**Data Engineering Project 1 — Group 3**


---

## 📌 Project Overview

캐치테이블(CatchTable) 플랫폼의 리뷰 데이터를 기반으로, 비정형 텍스트 리뷰를 직관적인 감성 태그와 데이터 기반 메뉴 인사이트로 변환하는 데이터 엔지니어링 프로젝트.

### Problem Definition
- 리뷰에는 가격, 서비스, 분위기, 불만 등 유용한 정보가 담겨있으나 비정형 텍스트로만 존재
- 플랫폼이 제공하는 태그(방문목적, 테이블 유형 등)가 제한적
- 부정적 경험(주의사항)이 태그로 제공되지 않아 사용자가 직접 리뷰를 읽어야 함

### Project Goal
| 목표 | 설명 |
|---|---|
| Hidden Sentiment Extraction | 텍스트 마이닝으로 숨겨진 불만/주의사항 추출 → 직관적 태그로 시각화 |
| Data-Driven Signature Menus | 실제 리뷰 언급 기반 대표 메뉴 추출 (플랫폼 지정 메뉴와 교차 검증) |
| Instant Insight Delivery | 식당의 장단점과 핵심 특징을 1초에 파악할 수 있는 대시보드 제공 |

---

## 🔧 Pipeline

```
Data Collection
→ 9,317개 식당 / 약 373만 건 리뷰 (CatchTable 크롤링)

Database Design & Data Ingestion
→ JSON 로딩 + MongoDB Atlas 설계 및 적재

Data Preprocessing
→ Kiwi 필터링 + 리뷰 정제 → 250만 건 유효 리뷰

Data Analysis
→ 식당별 Top 5 대표 메뉴 추출
→ 긍정/부정 태그 추출 (LLM 활용)

Insight Extraction
→ Streamlit 대시보드로 식당 검색 및 인사이트 시각화
```

---

## 🕷️ Data Crawling

### CatchTable
- **방식:** GraphQL API 역엔지니어링 (네트워크 패킷 분석)
- **수집 데이터:** 식당 기본정보, 메뉴, 리뷰, 방문목적, 식사시간
- **크롤러:** `crawler_v7.py`
- **세션 관리:** `get_cookies.py` (카카오 로그인 → 쿠키 저장)

### 주요 트러블슈팅
- Playwright/Selenium DOM 방식 → API 직접 호출 방식으로 전환 (속도 1/100 단축)
- 페이지네이션 cursor 기반 재귀 루프 설계
- 봇 탐지 우회: 동적 딜레이 + 헤더 위장
- `visit_purpose` 개별 리뷰 매칭 (filter별 재조회 방식)

---

## 🗄️ Database Design

### MongoDB Atlas — Collections

| Collection | 설명 | 주요 필드 |
|---|---|---|
| **Restaurants** | 식당 기본정보 + 분석 결과 | restaurant_id, category, area, review_summary, top_menus, top_menus_by_purpose |
| **Reviews** | 방문자 리뷰 + 방문 맥락 | restaurant_id, date, rating, visit_purpose_codes, meal_time_code |
| **Menus** | 식당별 메뉴 목록 | restaurant_id, name, is_representative, is_recommended, embed_text |

### 적재 파이프라인 (`upload_catchtable_v6.py`)

```bash
python3 upload_catchtable_v6.py \
  --folders ./data/v2/seoul ./data/v2/gyeong-gi \
  --workers 4 \
  --chunk-size 1000 \
  --min-reviews 20
```

### 주요 최적화 (v5 → v6)

| 항목 | v5 | v6 |
|---|---|---|
| 리뷰 적재 방식 | update_one() per review (N 트립) | bulk_write() + chunk(1000) (N/1000 트립) |
| 실패 처리 | ordered=True (첫 실패 시 중단) | ordered=False (실패 스킵 후 계속) |
| 저리뷰 필터 | 미적용 | --min-reviews 20 스킵 |
| 커넥션 풀 | 기본 설정 | maxPoolSize=50, 타임아웃 설정 |
| 중복 방지 | source_review_id 기반 | SHA-256 dedupe_key 폴백 추가 |
| DB 인덱스 | - | (source, restaurant_id, source_review_id) 복합 인덱스 → O(N) → O(log N) |

---

## 🔬 Data Preprocessing & Analysis

### 1. 대표 메뉴 추출 (`menu_matchNpurpose_test_3.py`)

- **대상:** 유효 리뷰(15자+) 100개 이상 식당 (4,457개)
- **방법:**
  1. 메뉴명 직접 문자열 매칭 (1차)
  2. Kiwi 형태소 분석 + 가중치 키워드 매칭 (2차 보완)
  3. 메뉴 없는 식당(오마카세 등) → Kiwi 명사 빈도 추출 (폴백)
- **결과:** `top_menus`, `top_menus_by_purpose` 필드로 Restaurants 컬렉션에 저장

```bash
python3 menu_matchNpurpose_test_3.py --min-reviews 100 --workers 4
```

### 2. 긍정/부정 태그 추출 (`demo_review.py`)

- **전처리:** TARGET_KEYWORDS 기반 리뷰 필터링 → Kiwi 문장 분리
- **AI 군집화:** `jhgan/ko-sroberta-multitask` 임베딩 + KMeans(80 clusters) → 대표 문장 추출
- **LLM 태깅:** GPT-4o-mini → 긍정/주의/재방문 3개 카테고리 태그 생성
- **저장:** `demo_sentiment` 컬렉션 + `.jsonl` 파일

```bash
python3 demo_review.py
```

---

## 📊 Key Insights

### Menu Insight

**Insight 1 — 플랫폼 대표 메뉴 vs 실제 인기 메뉴**
> 플랫폼이 지정한 대표 메뉴와 실제 방문자가 가장 많이 언급한 메뉴가 다를 수 있다
- `is_representative=True` 메뉴가 리뷰 Top 5에 없는 식당 다수 존재
- 예) 공식 대표 메뉴: 보쌈 → 리뷰 최다 언급: 칼국수

**Insight 2 — 방문 목적별 메뉴 선호 차이**
> 같은 식당이라도 방문 목적에 따라 주문하는 메뉴가 달라진다
- 데이트: 코스/세트 메뉴 선호
- 가족 식사: 공유형·대용량 메뉴 선호
- → "상황별 맛집 추천" 로직의 데이터 근거 확보

### Sentiment Tag Insight

**Insight 1 — 정보 비대칭 해소**
> 플랫폼에서 숨겨진 주의사항(#주차_어려움, #자리_좁음 등)을 가시화

**Insight 2 — 부정 경험의 누적 효과**
> 부정 태그가 누적될수록 만족도가 급격히 하락 (4.21 → 1.04)
- 긍정이 많아도 주의 태그 3~4개면 경험 점수 폭락

**Insight 3 — 운영 효율이 맛보다 중요**
> 고객 불만은 맛이 아닌 서비스·주차·웨이팅 등 운영 요소에 집중
- 웨이팅 관련 불만이 만족도 3.9 → 3.56으로 가장 큰 하락 유발

---

## 🖥️ Streamlit Dashboard

```bash
pip install streamlit pymongo
streamlit run app5.py
```

### 대시보드 구성
1. **식당 검색** — restaurant_name 기반 정규식 검색
2. **기본 정보** — 카테고리, 지역, 가격대, 전체 평점
3. **실제 대표 메뉴** — 리뷰 언급 빈도 기반 Top 5
4. **방문 목적 분포** — visit_purpose_codes 실시간 집계 + 바 차트
5. **AI 감성 태그** — 긍정 / 주의 / 재방문 태그 칩
6. **방문 목적별 인기 메뉴** — 목적별 Top 5 카드

---

## 📁 Repository Structure

```
├── crawling/
│   ├── crawler_v7.py          # CatchTable 전국 크롤러 (최종)
│   ├── get_cookies.py         # 카카오 로그인 쿠키 저장
│   └── add_visit_purposes.py  # 기존 JSON에 방문목적 추가
│
├── upload/
│   └── upload_catchtable_v6.py  # MongoDB 적재 파이프라인 (최종)
│
├── analysis/
│   ├── menu_matchNpurpose_test_3.py  # 대표 메뉴 추출
│   └── demo_review.py               # 감성 태그 추출
│
├── dashboard/
│   └── app5.py               # Streamlit 대시보드
│
└── README.md
```

---

## 🛠️ Tech Stack

| 분류 | 기술 |
|---|---|
| 크롤링 | Python, curl_cffi, Selenium |
| 데이터베이스 | MongoDB Atlas |
| 형태소 분석 | Kiwi (kiwipiepy) |
| 임베딩 | jhgan/ko-sroberta-multitask (SentenceTransformer) |
| 군집화 | KMeans (scikit-learn) |
| LLM | OpenAI GPT-4o-mini |
| 대시보드 | Streamlit |
| 병렬 처리 | multiprocessing, ThreadPoolExecutor |

---

## 📚 References

- [별난리서치] 맛집 선택의 기준 — hrcopinion.co.kr
- 엄해정, 진현정 (2024). 온라인 리뷰가 음식점 순위에 미치는 영향. 호텔경영학연구 Vol.33 No.1
- kiwipiepy — github.com/bab2min/kiwipiepy
- ko-sroberta-multitask — huggingface.co/jhgan/ko-sroberta-multitask
- pymongo — pymongo.readthedocs.io
- CatchTable — app.catchtable.co.kr
