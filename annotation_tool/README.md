# violence_annotator

Korean movie clip sequence-level annotation tool (Flask).

## 실행

```bash
pip install flask
python app.py
# http://localhost:5000 에서 실행
```

## 사용법

1. mp4 파일 경로 입력 → **▶ mp4 선택 → 자동 추출** (ffmpeg으로 2fps 추출)
2. 또는 이미 추출된 영화를 드롭다운에서 선택

### 단축키

| 키 | 기능 |
|---|---|
| `←` / `→` | 프레임 이동 |
| `Shift + ←/→` | 10프레임 이동 |
| `1` | violence 시작 → 다시 `1` 끝 |
| `2` | neg_hard 시작 → 다시 `2` 끝 |
| `Esc` | 진행 중 구간 취소 |
| `Ctrl+S` | 저장 |

## 출력 포맷

`output/{movie_id}.txt`

```
movie_id, scene_num, start_frame, end_frame, label
Asura, 1, 1, 9, neg_easy
Asura, 2, 10, 30, violence
Asura, 3, 31, 49, neg_easy
...
```

violence / neg_hard만 수동 표시 → 나머지 구간은 저장 시 자동으로 neg_easy 채움.

## 디렉토리 구조

```
violence_annotator/
├── app.py
├── templates/index.html
├── images/          # 추출된 프레임 (movie_id/frame_XXXXXX.jpg)
└── output/          # 어노테이션 txt
```
