"""
식당 검색 데모 - Streamlit
실행: streamlit run app.py
pip install streamlit pymongo
"""

import streamlit as st
from pymongo import MongoClient
from collections import Counter

# =========================================================
# 설정
# =========================================================
MONGO_URI = "mongodb+srv://db_user1:deproject1@deproject1.gu6pl9.mongodb.net/?appName=DEproject1"

TOP_MENUS_READY = True
TOP_MENUS_BY_PURPOSE_READY = True

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

# =========================================================
# MongoDB 연결
# =========================================================
@st.cache_resource
def get_db():
    client = MongoClient(MONGO_URI)
    return client["DEproject1DB"]

@st.cache_data(ttl=60)
def search_restaurants(query):
    db = get_db()
    results = list(db.Restaurants.find(
        {"restaurant_name": {"$regex": query, "$options": "i"}},
        {"restaurant_id": 1, "restaurant_name": 1, "category": 1,
         "area": 1, "review_summary": 1, "price_range_raw": 1,
         "top_menus": 1, "top_menus_by_purpose": 1,
         "total_reviews_collected": 1}
    ).limit(20))
    return results

@st.cache_data(ttl=60)
def get_visit_purpose_dist(restaurant_id):
    db = get_db()
    reviews = list(db.Reviews.find(
        {"restaurant_id": restaurant_id,
         "visit_purpose_codes": {"$exists": True, "$ne": []}},
        {"visit_purpose_codes": 1}
    ))
    counter = Counter()
    for r in reviews:
        for code in (r.get("visit_purpose_codes") or []):
            counter[code] += 1
    return counter

# =========================================================
# 페이지 설정
# =========================================================
st.set_page_config(page_title="식당 검색 데모", page_icon="🍽️", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif; }
.main { background-color: #f8f7f4; }
* { color: #1a1a1a !important; }
[data-baseweb="select"], [data-baseweb="select"] * { background-color: white !important; color: #1a1a1a !important; }
[data-baseweb="popover"], [data-baseweb="popover"] * { background-color: white !important; color: #1a1a1a !important; }
header[data-testid="stHeader"] { background-color: #f8f7f4 !important; }
input { background-color: white !important; color: #1a1a1a !important; }
[data-testid="stMarkdownContainer"] div {
    background: transparent !important;
}
.block-container { padding: 2rem 3rem; max-width: 1200px; }
.tag-chip { display: inline-block; padding: 0.3rem 0.8rem; border-radius: 20px; font-size: 0.82rem; margin: 0.2rem; }
.tag-pos { background: #e8f5e9; color: #2e7d32; }
.tag-warn { background: #fff3e0; color: #e65100; }
.tag-menu { background: #e3f2fd; color: #1565c0; }
</style>
""", unsafe_allow_html=True)

# =========================================================
# 검색
# =========================================================
st.markdown("## 🍽️ 식당 검색")
query = st.text_input("", placeholder="식당 이름을 입력하세요", label_visibility="collapsed")

if not query:
    st.markdown("<p style='color:#aaa;'>식당 이름을 검색해보세요.</p>", unsafe_allow_html=True)
    st.stop()

results = search_restaurants(query)
if not results:
    st.warning("검색 결과가 없습니다.")
    st.stop()

selected_name = st.selectbox("검색 결과", [r["restaurant_name"] for r in results], label_visibility="collapsed")
restaurant = next(r for r in results if r["restaurant_name"] == selected_name)
st.divider()

# =========================================================
# 기본 정보
# =========================================================
rid = restaurant.get("restaurant_id")
name = restaurant.get("restaurant_name", "")
category = restaurant.get("category", "")
area = restaurant.get("area", "")
price = restaurant.get("price_range_raw", "")
summary = restaurant.get("review_summary", {}) or {}
rating = summary.get("rating")
total = restaurant.get("total_reviews_collected", 0)
sub = " · ".join([p for p in [category, area, price] if p])

col_a, col_b = st.columns([3, 1])
with col_a:
    st.markdown(f"""
    <div>
        <div style='font-size:1.6rem; font-weight:700; margin-bottom:0.3rem;'>{name}</div>
        <div style='color:#888; font-size:0.9rem; margin-bottom:0.3rem;'>{sub}</div>
        <div style='color:#aaa; font-size:0.85rem;'>리뷰 {total:,}개</div>
    </div>
    """, unsafe_allow_html=True)
with col_b:
    st.markdown(f"""
    <div style='background:white; border-radius:12px; padding:1.2rem 1.5rem;
                text-align:center; box-shadow:0 1px 4px rgba(0,0,0,0.06);'>
        <div style='font-size:1.8rem; font-weight:700;'>{'★ ' + str(rating) if rating else 'N/A'}</div>
        <div style='font-size:0.8rem; color:#888; margin-top:0.2rem;'>전체 평점</div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# =========================================================
# 대표 메뉴 + 방문 목적 분포 (하나의 HTML 블록)
# =========================================================
top_menus = restaurant.get("top_menus") or []
purpose_dist = get_visit_purpose_dist(rid)

# 대표 메뉴 HTML
if not TOP_MENUS_READY:
    menus_html = "<div style='background:#fff8e1; border:1px dashed #ffd54f; border-radius:8px; padding:0.8rem; color:#f57f17; text-align:center; font-size:0.85rem;'>⚠️ top_menus DB 적재 완료 후 True로 변경</div>"
elif top_menus:
    menus_html = ""
    for i, menu in enumerate(top_menus[:5], 1):
        menu_name = menu.get("name", menu) if isinstance(menu, dict) else menu
        menus_html += f"""
        <div style='display:flex; align-items:center; padding:0.45rem 0; border-bottom:1px solid #f4f4f4;'>
            <span style='color:#bbb; font-size:0.85rem; width:20px;'>{i}</span>
            <span style='flex:1; margin-left:0.5rem; font-weight:500;'>{menu_name}</span>
        </div>"""
else:
    menus_html = "<p style='color:#aaa; font-size:0.9rem;'>데이터 없음</p>"

# 방문 목적 분포 HTML
if purpose_dist:
    total_p = sum(purpose_dist.values())
    dist_html = ""
    for code, cnt in purpose_dist.most_common(5):
        label = PURPOSE_LABELS.get(code, code)
        pct = cnt / total_p * 100
        dist_html += f"""
        <div style='margin-bottom:0.7rem;'>
            <div style='display:flex; justify-content:space-between; font-size:0.88rem;'>
                <span>{label}</span><span style='color:#888;'>{cnt:,}</span>
            </div>
            <div style='background:#f0f0f0; border-radius:4px; height:8px; margin-top:0.3rem;'>
                <div style='background:#66bb6a; border-radius:4px; height:8px; width:{pct:.1f}%;'></div>
            </div>
        </div>"""
else:
    dist_html = "<p style='color:#aaa; font-size:0.9rem;'>데이터 없음</p>"

st.markdown(f"""
<div style='display:flex; gap:1rem; margin-bottom:1rem;'>
    <div style='flex:1; background:white; border-radius:12px; padding:1.4rem 1.6rem; box-shadow:0 1px 4px rgba(0,0,0,0.06);'>
        <div style='font-size:1rem; font-weight:600; margin-bottom:1rem;'>실제 대표메뉴 (전체 리뷰 기반)</div>
        {menus_html}
    </div>
    <div style='flex:1; background:white; border-radius:12px; padding:1.4rem 1.6rem; box-shadow:0 1px 4px rgba(0,0,0,0.06);'>
        <div style='font-size:1rem; font-weight:600; margin-bottom:1rem;'>방문 목적 분포</div>
        {dist_html}
    </div>
</div>
""", unsafe_allow_html=True)

# =========================================================
# 리뷰 태그
# =========================================================
# ⚠️ 다른 팀 연결 포인트 — 아래 형식으로 데이터 교체:
# review_tags = {
#     "positive": ["재방문의사", "직원친절", "가성비"],
#     "warning":  ["웨이팅길음", "테이블좁음"],
#     "menu":     ["닭볶음탕", "라면사리"]
# }
review_tags = {
    "positive": ["연결 예정"],
    "warning":  ["연결 예정"],
    "menu":     ["연결 예정"],
}

def make_tags(tags, cls):
    return "".join(f"<span class='tag-chip {cls}'>{t}</span>" for t in tags)

st.markdown(f"""
<div style='background:white; border-radius:12px; padding:1.4rem 1.6rem;
            box-shadow:0 1px 4px rgba(0,0,0,0.06); margin-bottom:1rem;'>
    <div style='font-size:1rem; font-weight:600; margin-bottom:1rem;'>리뷰 태그</div>
    <div style='display:flex; gap:2rem;'>
        <div>
            <div style='font-size:0.85rem; font-weight:600; margin-bottom:0.5rem;'>긍정</div>
            {make_tags(review_tags["positive"], "tag-pos")}
        </div>
        <div>
            <div style='font-size:0.85rem; font-weight:600; margin-bottom:0.5rem;'>주의</div>
            {make_tags(review_tags["warning"], "tag-warn")}
        </div>
        <div>
            <div style='font-size:0.85rem; font-weight:600; margin-bottom:0.5rem;'>메뉴</div>
            {make_tags(review_tags["menu"], "tag-menu")}
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

# =========================================================
# 방문 목적별 인기 메뉴
# =========================================================
by_purpose = restaurant.get("top_menus_by_purpose") or {}

if not TOP_MENUS_BY_PURPOSE_READY:
    st.markdown("""
    <div style='background:white; border-radius:12px; padding:1.4rem 1.6rem; box-shadow:0 1px 4px rgba(0,0,0,0.06);'>
        <div style='font-size:1rem; font-weight:600; margin-bottom:1rem;'>방문 목적별 인기 메뉴</div>
        <div style='background:#fff8e1; border:1px dashed #ffd54f; border-radius:8px; padding:0.8rem; color:#f57f17; text-align:center; font-size:0.85rem;'>
            ⚠️ top_menus_by_purpose DB 적재 완료 후 True로 변경
        </div>
    </div>
    """, unsafe_allow_html=True)
elif by_purpose:
    items = list(by_purpose.items())
    rows = [items[i:i+3] for i in range(0, len(items), 3)]

    all_rows_html = ""
    for row in rows:
        max_menus = max(len(menus[:5]) for _, menus in row)
        box_height = 90 + max_menus * 44

        cols_html = ""
        for code, menus in row:
            label = PURPOSE_LABELS.get(code, code)
            menu_rows_html = ""
            for i, menu in enumerate(menus[:5], 1):
                menu_name = menu.get("name", menu) if isinstance(menu, dict) else menu
                menu_rows_html += f"""
                <div style='display:flex; align-items:center; padding:0.35rem 0; border-bottom:1px solid #d9d4c8; background:#ede9df;'>
                    <span style='color:#555; font-size:0.82rem; width:18px;'>{i}</span>
                    <span style='margin-left:0.5rem; font-size:0.9rem; color:#1a1a1a;'>{menu_name}</span>
                </div>"""

            cols_html += f"""
            <div style='flex:1; background:#ede9df; border-radius:12px; padding:1.2rem 1.4rem;
                        height:{box_height}px; box-sizing:border-box;'>
                <div style='display:inline-block; background:#e8f5f0; color:#2e8b6e;
                            font-size:0.8rem; font-weight:600; padding:0.25rem 0.7rem;
                            border-radius:20px; margin-bottom:0.8rem;'>{label}</div>
                {menu_rows_html}
            </div>"""

        # 3개 미만이면 빈 칸 채우기
        for _ in range(3 - len(row)):
            cols_html += "<div style='flex:1;'></div>"

        all_rows_html += f"<div style='display:flex; gap:1rem; margin-bottom:1rem;'>{cols_html}</div>"

    st.markdown(f"""
    <div style='background:white; border-radius:12px; padding:1.4rem 1.6rem; box-shadow:0 1px 4px rgba(0,0,0,0.06);'>
        <div style='font-size:1rem; font-weight:600; margin-bottom:1rem;'>방문 목적별 인기 메뉴</div>
        {all_rows_html}
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown("""
    <div style='background:white; border-radius:12px; padding:1.4rem 1.6rem; box-shadow:0 1px 4px rgba(0,0,0,0.06);'>
        <div style='font-size:1rem; font-weight:600; margin-bottom:1rem;'>방문 목적별 인기 메뉴</div>
        <p style='color:#aaa; font-size:0.9rem;'>데이터 없음</p>
    </div>
    """, unsafe_allow_html=True)
