"""
캐치테이블 로그인 후 쿠키 저장
실행: python3 get_cookies.py
브라우저가 열리면 카카오 로그인 완료 후 Enter
"""
import json
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

COOKIES_FILE = "./data/cookies.json"
TARGET_URL   = "https://app.catchtable.co.kr"

def main():
    options = Options()
    options.add_argument("--lang=ko-KR")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)


    print("브라우저 실행 중...")
    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    driver.get(TARGET_URL)

    print("\n브라우저에서 카카오 로그인을 완료해주세요.")
    print("로그인 완료 후 여기서 Enter를 누르세요.")
    input()

    # 로그인 반영 대기
    time.sleep(2)

    cookies = driver.get_cookies()
    driver.quit()

    cookie_dict = {c["name"]: c["value"] for c in cookies}

    Path("./data").mkdir(exist_ok=True)
    with open(COOKIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cookie_dict, f, ensure_ascii=False, indent=2)

    print(f"\n쿠키 {len(cookie_dict)}개 저장 완료: {COOKIES_FILE}")
    key_cookies = ["x-ct-a", "_ga", "__cf_bm"]
    for k in key_cookies:
        if k in cookie_dict:
            print(f"  ✅ {k}")
        else:
            print(f"  ❌ {k} (없음)")

if __name__ == "__main__":
    main()
