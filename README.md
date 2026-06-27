# 🎬 영화 폭력 자동 감지 시스템

> **데이터 엔지니어링 수업 프로젝트 2** | 2026-1 DataEngineering class prj2
> 유튜브 영화 리뷰 클립 수집부터 동작하는 데모까지 — 한국 영화 폭력 감지 파이프라인

---

## 📌 개요

OTT 플랫폼의 콘텐츠가 폭발적으로 증가하면서 영상물등급위원회(KMRB) 심사에 병목이 생겼다. 심사위원 1인당 주 15~20편을 처리해야 하고, 수개월의 대기가 발생한다. 이 프로젝트는 영화 내 **폭력 구간을 자동으로 탐지**해 심사를 보조하는 시스템을 구축한다.

흡연·노출 감지(프레임 단위, 객체 존재 여부 판단)와 달리 **폭력은 본질적으로 시계열적**이다 — 프레임 한 장으로는 아무것도 알 수 없다. 우리는 프레임 시퀀스의 *시간적 패턴*을 학습해 초 단위로 폭력 구간을 예측하는 엔드투엔드 파이프라인을 구축했다.

**최종 결과:** Transformer Encoder — Test F1 (violence) **0.65**, Test Accuracy **0.74** (전통 ML 베이스라인 대비 +6%p)

---

## 🗂️ 저장소 구조

```
violence-detection/
│
├── data_collection/
│   └── download_clips.py          # yt-dlp 유튜브 영화 리뷰 클립 다운로더
│
├── annotation_tool/               # 커스텀 Flask 어노테이션 앱
│   ├── app.py
│   ├── templates/
│   └── README_annotator.md
│
├── preprocessing/
│   ├── extract_frames.py          # ffmpeg 2fps 프레임 추출
│   ├── build_clips.py             # ResNet50 피처 추출 → (N, 4, 2048) 클립
│   └── upload_to_hf.py            # HuggingFace 데이터셋 업로드
│
├── modeling/
│   ├── baselines.py               # Logistic Regression, Linear SVM (mean+max pooling)
│   ├── lstm_model.py
│   ├── tcn_model.py
│   ├── bilstm_model.py
│   ├── transformer_model.py       # ViolenceTransformer (base)
│   ├── transformer_final.py       # ViolenceTransformerFinal (attention 추출 포함)
│   └── train.py                   # 학습 루프, CosineAnnealing, 체크포인트 저장
│
├── analysis/
│   ├── attention_viz.py           # 클립별 attention weight 히트맵
│   ├── per_movie_f1.py            # 영화별 F1 바차트 + 클러스터 분석
│   └── threshold_tuning.py        # Hysteresis dual-threshold 스윕
│
├── demo/
│   └── app.py                     # Streamlit 데모: 영상 업로드 → 폭력 타임라인
│
├── notebooks/
│   └── DE_PJ2_modeling.ipynb      # 전체 Colab 학습 노트북 (T4 GPU)
│
├── dataset/                       # git 미추적 (HuggingFace 참고)
│   ├── frames/{movie_id}/frame_{N:06d}.jpg
│   ├── annotations/{movie_id}.txt
│   └── test_annotations/{movie_id}.txt
│
└── README.md
```

---

## 🔧 파이프라인

```
유튜브 (20~40분 영화 리뷰 영상)
    ↓  yt-dlp
.mp4 클립 (액션, 범죄, 누아르, 스릴러 장르)
    ↓  ffmpeg @ 2fps
프레임 이미지  >  HuggingFace (DEteam4/datasetVer3)
    ↓  커스텀 Flask 어노테이션 툴
시퀀스 레벨 레이블: violence / neg_hard / neg_easy
    ↓  build_clips.py (ResNet50 frozen, 영화별 StandardScaler)
X.npy (N, 4, 2048)  +  y.npy (N,)  +  movie_ids.npy (N,)
    ↓  영화 단위 train/val/test split
시계열 모델: LSTM → TCN → BiLSTM → Transformer
    ↓  최종 선택: ViolenceTransformerFinal
폭력 구간 예측 [start_sec, end_sec]
```

**데이터셋 규모:** 62개 영상 / 174,253 프레임 / 약 38,913 클립
Train: 49편 (27,513 클립) | Val: 13편 (6,205 클립) | Test: 9편 (5,195 클립)

---

## 🏷️ 어노테이션 툴 (`violence_annotator`)

기존 도구가 우리 워크플로를 지원하지 않아 Flask로 직접 제작했다.

**주요 기능:**
- 키보드 기반 조작 (속도를 위해 마우스 불필요)
- `[1]` violence 시작 → 다시 `[1]` 끝 / `[2]` neg_hard
- 어노테이션 구간 실시간 타임라인 바 시각화
- 자동 채움: 미표시 구간은 저장 시 자동으로 `neg_easy` 할당
- TXT 출력 형식: `[movie_id, scene_num, start_frame, end_frame, label]`

**직접 만든 이유:** 프로젝트 1에서 사용한 CVAT는 bbox 어노테이션 전용이라 시퀀스 레벨 구간 레이블링을 지원하지 않는다. 기존 도구들은 프레임 단위 클릭 방식이라 2fps로 추출한 20~40분 영상 어노테이션에는 너무 느렸다.

```bash
cd annotation_tool
pip install flask
python app.py
# http://localhost:5000 에서 실행
```

---

## 🧠 모델링: 시행착오의 기록

### 왜 객체 탐지가 아닌가?

프로젝트 1에서는 흡연·음주 감지에 바운딩 박스 탐지를 사용했다. 하지만 폭력은 *어떤 객체가 있는지*가 아니라 *시간에 걸쳐 어떤 행동이 전개되는지*의 문제다. 팔을 드는 동작은 프레임 한 장으로는 판단이 불가능하다. 세 프레임 후에야 주먹인지 알 수 있다.

### 시도 1: MediaPipe Pose Detection ❌

**가설:** 폭력 = 비정상적인 신체 움직임 > 프레임당 33개 관절 좌표 추출 > LSTM에 입력

**결과:** MediaPipe가 정작 필요한 순간에 정확히 실패했다:
- 클로즈업 샷 > 전신 미포착 > 키포인트 전부 0
- 격투 장면의 모션 블러 > 불안정/누락 감지
- 어두운 씬 (누아르 장르) > 감지 완전 실패

이것들은 예외 케이스가 아니라 한국 액션 영화 폭력 씬의 *대부분*이다.

**결정:** CNN 기반 시각 피처 추출로 전환

### 시도 2: Optical Flow (병렬 탐색)

Farneback dense optical flow로 프레임 간 픽셀 단위 움직임을 포착했다. 속도 최적화 (320×240 리사이즈, 피라미드 레벨 축소 → 약 8배 속도향상). 시각화 결과는 유망했다 — 폭력 클립은 크고 혼란스러운 flow 벡터, neg_easy는 잔잔한 flow.

그러나 픽셀 단위 flow를 프레임 쌍당 단일 magnitude 값으로 압축하면서 공간 정보를 너무 많이 잃었다. clip_length=8로 늘려도 Val F1이 0.66에서 정체됐다. 실패로 끝냈지만 의미 있는 음성 결과로 기록했다.

### 시도 3: CNN 피처 추출 ✅

**ResNet50 (frozen, ImageNet pretrained):**
```python
resnet = models.resnet50(weights='IMAGENET1K_V1')
resnet = torch.nn.Sequential(*list(resnet.children())[:-1])
resnet.eval().to(device)
# 결과물 :  2048차원 피처 벡터
```

연속 4프레임 → 클립 shape `(4, 2048)` : 2fps 기준 2초 컨텍스트. 4프레임인 이유? 하나의 완결된 동작(주먹, 발차기, 잡기)을 포착하기 위한 최소 단위.

**영화별 정규화:** 각 영화마다 조명과 색온도가 다르다. 영화 단위로 StandardScaler를 fit하면 "이 어두운 영화 = 폭력"을 학습하는 것을 방지한다.

**Neg_hard 처리:** 초기에는 3클래스 (violence / neg_hard / neg_easy)로 설계했다. neg_hard(장난 격투, 훈련 씬)는 경계 클래스로 어노테이션했지만 학습 시 neg_easy로 병합했다 — 실제 태스크는 이진 분류이고, neg_hard를 별도 클래스로 유지하면 노이즈가 더 컸기 때문이다.

### 시계열 모델 실험

전체 모델을 Google Colab Pro (T4 GPU)에서 학습. 데이터 리케이지 방지를 위해 영화 단위 split 적용 — 클립 단위 split이면 같은 영화 클립이 train/val 양쪽에 들어가 모델이 폭력 패턴이 아닌 영화 스타일을 외워버린다.

| 모델 | Test F1 (violence) | Test Acc | 비고 |
|---|---|---|---|
| Logistic Regression | 0.61 | 0.68 | mean+max pool → flat (4096,) |
| Linear SVM | 0.608 | 0.68 | 동일 flattening |
| LSTM | 0.62 | 0.71 | hidden=256, layers=2, dropout=0.3 |
| TCN | 0.57 | 0.69 | channels=[128,128,256], kernel=2 |
| BiLSTM | 0.59 | 0.71 | bidirectional=True |
| Transformer (base) | 0.62 | 0.72 | nhead=8, layers=2, dropout=0.3 |
| **Transformer (final)** | **0.65** | **0.74** | dropout=0.5, weight_decay=1e-4, CosineAnnealing |

**베이스라인에서 얻은 인사이트:** LogReg/SVM이 0.68 정확도로 놀랍도록 경쟁력 있었다. 이는 (1) ResNet50 피처가 선형 분류기도 잘 동작할 만큼 풍부하고, (2) Transformer의 +6%p 이득이 순전히 시간적 순서를 학습한 덕분임을 의미한다.

### 과적합 문제와 해결

base Transformer 학습 후: Train Acc ≈ 0.99, Val Acc ≈ 0.66 > **gap 0.33**. 원인: 제한된 학습 데이터 + 영화 단위 도메인 차이 (모델이 폭력 패턴 대신 영화별 시각 스타일을 외움).

적용한 해결책:
- Dropout: 0.3 > **0.5**
- **Weight decay 1e-4** (Adam optimizer)
- **CosineAnnealing LR scheduler** (T_max=20)
- Best checkpoint 저장 (val_acc 기준)

결과: Train Acc 0.79 vs Val Acc 0.73 > **gap 0.06으로 감소**

### 최종 모델: ViolenceTransformerFinal

forward pass마다 attention weight를 추출할 수 있도록 확장:
```python
def forward(self, x, return_attn=False):
    attn_weights = []
    out = x
    for layer in self.layers:
        attn_out, attn_w = layer.self_attn(out, out, out, average_attn_weights=True)
        attn_weights.append(attn_w.detach().cpu())
        out = layer(out)
    logits = self.fc(out[:, -1, :])
    if return_attn:
        return logits, attn_weights
    return logits
```

**Attention 시각화 결과:**
- 폭력 클립 > f3에 attention 집중 (피크 순간), f4는 거의 0
- neg_easy 클립 > f1, f2, f3에 고르게 분산

모델이 평균적 외형으로 분류하는 게 아니라 클립 내 *피크 프레임*을 찾아내는 것을 학습했다.

---

## 📊 영화별 성능 분석

상위/하위 테스트 영화 클러스터 간 2-proportion z-test 수행:

- 상위 클러스터 (5편) 평균 F1: **0.671**
- 하위 클러스터 (4편) 평균 F1: **0.547** > gap 0.124
- Recall: 0.639 > 0.502 (Δ 0.137) — recall 하락이 gap의 주원인
- 통계적 유의성: Recall z=6.13, p=8.6×10⁻¹⁰

근본 원인: 모델의 보수적 판단이 아니라 **표현 불일치** — 하위 클러스터 영화(심리적 폭력, 야간/어두운 씬, 독특한 촬영 스타일)가 학습 데이터에서 과소 대표됐다.

threshold를 0.5 → 0.25로 조정하면 gap이 0.062로 절반이 됐지만, 남은 delta는 하이퍼파라미터 문제가 아닌 데이터 분포 문제다.

---

## 🎮 데모 앱

영상 업로드 > [start, end] 폭력 구간 타임라인 출력.

**후처리:**
- 클립별 확률에 Gaussian smoothing 적용 (인접 클립이 짧은 딥을 끌어올림)
- Hysteresis dual-threshold: `t_high=0.45`에서 구간 시작, `t_low=0.30` 아래로 떨어질 때까지 유지
- 출력: 시간 범위, 길이, 구간 내 최고 확률

---

## 🛠️ 기술 스택

| 구성요소 | 도구 |
|---|---|
| 데이터 수집 | `yt-dlp`, `ffmpeg` |
| 어노테이션 | 커스텀 Flask 앱 (`violence_annotator`) |
| 저장소 | HuggingFace (`DEteam4/datasetVer3`), MinIO (S3 호환) |
| 피처 추출 | PyTorch, ResNet50 (torchvision) |
| 학습 | Google Colab Pro (T4 GPU) |
| 모델 | LSTM, TCN, BiLSTM, Transformer Encoder |
| 베이스라인 | scikit-learn (LogReg, LinearSVC) |
| 데모 | Streamlit |
| 분석 | matplotlib, scipy (z-test) |

---

## 💡 배운 것들

**잘 된 것:**
- 영상 데이터셋에서 영화 단위 split은 필수 — 클립 단위 split은 다른 이름의 데이터 리케이지다
- 데이터 출처가 다를 때 영화별 정규화가 전체 정규화보다 훨씬 중요하다
- 단순 베이스라인(mean+max pooling + LogReg)을 먼저 돌려보는 것이 현실적인 성능 상한선을 잡는 데 필수다
- Attention weight 추출은 거의 비용이 없고 해석 가능한 진단 정보를 공짜로 준다

**예상과 달랐던 것:**
- Pose Detection은 가장 필요한 순간(빠르고, 어둡고, 클로즈업인 씬)에 정확히 실패한다
- Optical flow는 clip_len을 늘리면 도움이 되지만(8 > 4 > 2) CNN과의 격차를 좁히지 못했다
- TCN은 병렬 처리 장점에도 불구하고 BiLSTM보다 낮았다 — 4프레임 시퀀스가 dilated convolution이 빛을 발하기엔 너무 짧은 것으로 추정
- VideoMAE는 가능성을 보였으나 우리 컴퓨팅 예산에 비해 너무 무거웠다

**다시 한다면:**
- frozen ResNet50 대신 엔드투엔드 파인튜닝 — 외형 피처만으로는 움직임을 놓친다
- 리뷰 클립 대신 원본 영화 — 더 깨끗하고 긴 폭력 구간을 얻을 수 있다
- 다중 어노테이터 교차 검증으로 neg_hard 경계의 주관성을 줄인다

---

## 🔗 참고

- 데이터셋: [HuggingFace DEteam4/datasetVer3](https://huggingface.co/datasets/DEteam4/datasetVer3)
- 영상물등급위원회 분류 기준: http://ors.kmrb.or.kr/guide/case/classstandards.do
