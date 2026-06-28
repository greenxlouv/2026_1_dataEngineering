# violence detection demo

영상 업로드 → ResNet50 + Transformer → 폭력 구간 타임라인

## 실행

```bash
pip install streamlit torch torchvision scipy matplotlib pillow
streamlit run app.py
```

## 모델 파일

`best_model.pth`를 같은 디렉토리에 두면 실제 추론, 없으면 더미 추론으로 동작.

```
demo/
├── app.py
└── best_model.pth   ← 선택사항
```

## 기능

- 영상 업로드 (mp4/avi/mov/mkv)
- ffmpeg으로 2fps 프레임 추출
- ResNet50 frozen 피처 추출 → `(N, 4, 2048)` 클립
- Transformer 추론 → 클립별 폭력 확률
- Gaussian smoothing (sigma=2.0)
- Hysteresis dual-threshold 구간 탐지 (t_high=0.45, t_low=0.30)
- 지표 카드: 영상 길이 / 클립 수 / 폭력 구간 수 / 폭력 비율
- Violence timeline 차트
- 탐지된 구간 테이블 (시간범위, 길이, 최고확률)
- 하단 폭력 progress bar
- 사이드바: t_high / t_low / sigma 실시간 조정 + 재탐지

## 주의사항

- ffmpeg이 설치되어 있어야 함 (`brew install ffmpeg` / `apt install ffmpeg`)
- '영상 내 폭력 클립 비율'은 영상 내 상대값이며 영상 간 비교에 사용하면 안 됨
  (피처가 영상 단위로 정규화되기 때문)
