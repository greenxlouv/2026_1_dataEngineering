# 클립 추출 파이프라인 가이드

## 개요

영화 프레임에서 CNN(ResNet50) 피처를 추출해서 4프레임 클립으로 묶고 HuggingFace에 업로드하는 파이프라인.
violence와 neg_easy 클립 수 1:1.5로 조정된 ver

---

## A. 로컬 버전 (맥북)

### 조건
- 로컬에 프레임이 이미 있는 경우
- `violence_annotator/images/{영화이름}/frame_XXXXXX.jpg` 형태로 저장된 경우

### 1단계: 패키지 설치

```bash
pip install torch torchvision numpy
```

### 2단계: 스크립트 작성

아래 내용을 `build_clips.py`로 저장:

```python
import re
import numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
import os

# ========================================
# ⚠️ 여기만 수정
BASE_PATH = '/Users/본인이름/Documents/경로/pj2/violence_annotator'  # 본인 경로로 변경
SAVE_PATH = '/Users/본인이름/Documents/경로/pj2'                      # npy 저장 경로
SAVE_NAME = 'clips_이름'                                               # 본인 이름으로 변경
MY_MOVIES = ['영화1', '영화2', '영화3']                                 # 본인 담당 영화
# ========================================

device = 'cpu'  # M1이면 'mps' 로 바꿔도 됨 (백그라운드 실행시 cpu 권장)
print(f'디바이스: {device}')

resnet = models.resnet50(weights='IMAGENET1K_V1')
resnet = torch.nn.Sequential(*list(resnet.children())[:-1])
resnet.eval().to(device)

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

def extract_cnn_feature(image_path):
    img = Image.open(image_path).convert('RGB')
    x = transform(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = resnet(x).squeeze().cpu().numpy()
    return feat

def build_clips_local(movie_id, clip_len=4, stride=2, neg_ratio=1.5):
    txt_path = f'{BASE_PATH}/output/{movie_id}.txt'
    frames_dir = f'{BASE_PATH}/images/{movie_id}'

    scenes = []
    with open(txt_path, 'r') as f:
        for line in f:
            match = re.match(r'\[(.+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\w+)\]', line.strip())
            if match:
                start, end, label = int(match.group(3)), int(match.group(4)), match.group(5)
                if label in ['violence', 'neg_easy']:
                    scenes.append((start, end, label))

    vio_clips, vio_labels = [], []
    neg_clips, neg_labels = [], []

    for (start, end, label) in scenes:
        frames = list(range(start, end+1))
        for i in range(0, len(frames) - clip_len + 1, stride):
            clip_frames = frames[i:i+clip_len]
            clip_feats = []
            for f in clip_frames:
                img_path = f'{frames_dir}/frame_{f:06d}.jpg'
                if os.path.exists(img_path):
                    clip_feats.append(extract_cnn_feature(img_path))
                else:
                    clip_feats.append(np.zeros(2048))
            if len(clip_feats) == clip_len:
                if label == 'violence':
                    vio_clips.append(clip_feats)
                    vio_labels.append(label)
                else:
                    neg_clips.append(clip_feats)
                    neg_labels.append(label)

    # violence 클립 수 기준으로 neg_easy 자동 조정 (비율 1.5배)
    max_neg_clips = int(len(vio_clips) * neg_ratio)
    if len(neg_clips) > max_neg_clips:
        idx = np.random.choice(len(neg_clips), max_neg_clips, replace=False)
        neg_clips = [neg_clips[i] for i in idx]
        neg_labels = [neg_labels[i] for i in idx]

    return vio_clips + neg_clips, vio_labels + neg_labels

X_clips, y_clips, movie_ids = [], [], []
for movie in MY_MOVIES:
    print(f'{movie} 처리 중...')
    clips, labels = build_clips_local(movie)
    X_clips.extend(clips)
    y_clips.extend(labels)
    movie_ids.extend([movie] * len(clips))
    print(f'{movie} 완료: {len(clips)}개 | 누적: {len(X_clips)}개')
    # 영화 하나 끝날 때마다 저장
    np.save(f'{SAVE_PATH}/{SAVE_NAME}_X.npy', np.array(X_clips, dtype=np.float32))
    np.save(f'{SAVE_PATH}/{SAVE_NAME}_y.npy', np.array(y_clips))
    np.save(f'{SAVE_PATH}/{SAVE_NAME}_movie_ids.npy', np.array(movie_ids))
    print('저장 완료')

print(f'\n총 클립: {len(X_clips)}개')
print(f'violence: {y_clips.count("violence")}개')
print(f'neg_easy: {y_clips.count("neg_easy")}개')
```

### 3단계: 실행

```bash
cd [build_clips.py 있는 파일 경로]
[본인 가상환경, torch torchvision numpy 이거 설치된걸로] build_clips.py
ex) /opt/anaconda3/bin/python build_clips.py
```

백그라운드로 돌리려면:

```bash
[본인 가상환경, torch torchvision numpy 이거 설치된걸로] build_clips.py &
ex) /opt/anaconda3/bin/python build_clips.py &
disown %1
```

진행 상황 확인:

```bash
# 프로세스 살아있는지
ps aux | grep build_clips | grep -v grep

# 클립 쌓인 개수 확인
[본인 가상환경, torch torchvision numpy 이거 설치된걸로 ex)/opt/anaconda3/bin/python] -c "
import numpy as np
y = np.load('/Users/본인이름/경로/clips_이름_y.npy')
print(f'총 클립: {len(y)}개 | violence: {sum(y==\"violence\")}개 | neg_easy: {sum(y==\"neg_easy\")}개')
"
```

### 4단계: HuggingFace 업로드(안해도 됨)

```bash
# 로그인 (최초 1번)
huggingface-cli login
# → 토큰 입력 (https://huggingface.co/settings/tokens 에서 write 권한으로 발급)

# 업로드
huggingface-cli upload DEteam4/datasetVer3 /경로/clips_이름_X.npy clips/clips_이름_X.npy --repo-type dataset
huggingface-cli upload DEteam4/datasetVer3 /경로/clips_이름_y.npy clips/clips_이름_y.npy --repo-type dataset
huggingface-cli upload DEteam4/datasetVer3 /경로/clips_이름_movie_ids.npy clips/clips_이름_movie_ids.npy --repo-type dataset
```

---

## A-2. 로컬 버전 (윈도우)

### 조건
- 로컬에 프레임이 이미 있는 경우
- `violence_annotator\images\{영화이름}\frame_XXXXXX.jpg` 형태로 저장된 경우

### 1단계: 패키지 설치

```bash
pip install torch torchvision numpy
```

### 2단계: 스크립트 작성

아래 내용을 `build_clips.py`로 저장:

```python
import re
import numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
import os

# ========================================
# ⚠️ 여기만 수정
BASE_PATH = r'C:\Users\본인이름\Documents\경로\pj2\violence_annotator'  # 본인 경로로 변경
SAVE_PATH = r'C:\Users\본인이름\Documents\경로\pj2'                      # npy 저장 경로
SAVE_NAME = 'clips_이름'                                                  # 본인 이름으로 변경
MY_MOVIES = ['영화1', '영화2', '영화3']                                    # 본인 담당 영화
# ========================================

device = 'cuda' if torch.cuda.is_available() else 'cpu'  # GPU 있으면 자동으로 cuda 사용
print(f'디바이스: {device}')

resnet = models.resnet50(weights='IMAGENET1K_V1')
resnet = torch.nn.Sequential(*list(resnet.children())[:-1])
resnet.eval().to(device)

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

def extract_cnn_feature(image_path):
    img = Image.open(image_path).convert('RGB')
    x = transform(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = resnet(x).squeeze().cpu().numpy()
    return feat

def build_clips_local(movie_id, clip_len=4, stride=2, neg_ratio=1.5):
    txt_path = os.path.join(BASE_PATH, 'output', f'{movie_id}.txt')
    frames_dir = os.path.join(BASE_PATH, 'images', movie_id)

    scenes = []
    with open(txt_path, 'r') as f:
        for line in f:
            match = re.match(r'\[(.+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\w+)\]', line.strip())
            if match:
                start, end, label = int(match.group(3)), int(match.group(4)), match.group(5)
                if label in ['violence', 'neg_easy']:
                    scenes.append((start, end, label))

    vio_clips, vio_labels = [], []
    neg_clips, neg_labels = [], []

    for (start, end, label) in scenes:
        frames = list(range(start, end+1))
        for i in range(0, len(frames) - clip_len + 1, stride):
            clip_frames = frames[i:i+clip_len]
            clip_feats = []
            for f in clip_frames:
                img_path = os.path.join(frames_dir, f'frame_{f:06d}.jpg')
                if os.path.exists(img_path):
                    clip_feats.append(extract_cnn_feature(img_path))
                else:
                    clip_feats.append(np.zeros(2048))
            if len(clip_feats) == clip_len:
                if label == 'violence':
                    vio_clips.append(clip_feats)
                    vio_labels.append(label)
                else:
                    neg_clips.append(clip_feats)
                    neg_labels.append(label)

    # violence 클립 수 기준으로 neg_easy 자동 조정 (비율 1.5배)
    max_neg_clips = int(len(vio_clips) * neg_ratio)
    if len(neg_clips) > max_neg_clips:
        idx = np.random.choice(len(neg_clips), max_neg_clips, replace=False)
        neg_clips = [neg_clips[i] for i in idx]
        neg_labels = [neg_labels[i] for i in idx]

    return vio_clips + neg_clips, vio_labels + neg_labels

X_clips, y_clips, movie_ids = [], [], []
for movie in MY_MOVIES:
    print(f'{movie} 처리 중...')
    clips, labels = build_clips_local(movie)
    X_clips.extend(clips)
    y_clips.extend(labels)
    movie_ids.extend([movie] * len(clips))
    print(f'{movie} 완료: {len(clips)}개 | 누적: {len(X_clips)}개')
    np.save(os.path.join(SAVE_PATH, f'{SAVE_NAME}_X.npy'), np.array(X_clips, dtype=np.float32))
    np.save(os.path.join(SAVE_PATH, f'{SAVE_NAME}_y.npy'), np.array(y_clips))
    np.save(os.path.join(SAVE_PATH, f'{SAVE_NAME}_movie_ids.npy'), np.array(movie_ids))
    print('저장 완료')

print(f'\n총 클립: {len(X_clips)}개')
print(f'violence: {y_clips.count("violence")}개')
print(f'neg_easy: {y_clips.count("neg_easy")}개')
print(f'파일 위치: {SAVE_PATH}')
print(f'이 파일들을 Jen에게 전달해주세요:')
print(f'  {SAVE_NAME}_X.npy')
print(f'  {SAVE_NAME}_y.npy')
print(f'  {SAVE_NAME}_movie_ids.npy')
```

### 3단계: 실행

명령 프롬프트(cmd) 또는 Anaconda Prompt에서:

```bash
cd C:\Users\본인이름\Documents\경로\pj2
python build_clips.py
```

진행 상황 확인 (새 cmd 창에서):

```bash
python -c "import numpy as np; y = np.load('C:\\Users\\본인이름\\경로\\clips_이름_y.npy'); print(f'총 클립: {len(y)}개 | violence: {sum(y==\"violence\")}개 | neg_easy: {sum(y==\"neg_easy\")}개')"
```

---


## B. Colab 버전 (로컬 프레임 없는 경우)

### 조건
- 로컬에 프레임 없고 HuggingFace에 올라간 프레임 사용
- CPU, GPU 크게 상관 없음

### 1단계: Colab 설정

런타임 → 런타임 유형 변경 → 마음에 드는 자원으로..

### 2단계: HuggingFace 토큰 등록 (최초 1번)

Colab 왼쪽 🔑 아이콘 → Add new secret
- 이름: `HF_TOKEN`
- 값: HuggingFace 토큰 (https://huggingface.co/settings/tokens 에서 write 권한으로 발급)

### 3단계: 코드 실행

```python
# ========================================
# ⚠️ 여기만 수정
MY_MOVIES = ['영화1', '영화2', '영화3']   # 본인 담당 영화 목록
SAVE_NAME = 'clips_이름'                  # 본인 이름으로 변경
DRIVE_PATH = ' Drive 경로'  #저장할 드라이브 경로
# ========================================

# 설치
!pip install torch torchvision huggingface_hub -q

# import
import torch
import torch.nn as nn
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
import numpy as np
import re
import os
from huggingface_hub import HfApi, hf_hub_download
from google.colab import drive

# Drive 마운트
drive.mount('/content/drive')

# 디바이스
device = 'cuda' if torch.cuda.is_available() else 'cpu'
print(f'디바이스: {device}')

# ResNet 로드
resnet = models.resnet50(weights='IMAGENET1K_V1')
resnet = torch.nn.Sequential(*list(resnet.children())[:-1])
resnet.eval().to(device)

transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

def extract_cnn_feature(image_path):
    img = Image.open(image_path).convert('RGB')
    x = transform(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = resnet(x).squeeze().cpu().numpy()
    return feat

def build_clips_from_hf(movie_id, clip_len=4, stride=2, neg_ratio=1.5):
    txt_path = hf_hub_download(
        repo_id='DEteam4/datasetVer3',
        filename=f'annotations/raw_txt/{movie_id}.txt',
        repo_type='dataset'
    )
    scenes = []
    with open(txt_path, 'r') as f:
        for line in f:
            match = re.match(r'\[(.+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\w+)\]', line.strip())
            if match:
                start, end, label = int(match.group(3)), int(match.group(4)), match.group(5)
                if label in ['violence', 'neg_easy']:
                    scenes.append((start, end, label))

    vio_clips, vio_labels = [], []
    neg_clips, neg_labels = [], []

    for (start, end, label) in scenes:
        frames = list(range(start, end+1))
        for i in range(0, len(frames) - clip_len + 1, stride):
            clip_frames = frames[i:i+clip_len]
            clip_feats = []
            for f in clip_frames:
                try:
                    img_path = hf_hub_download(
                        repo_id='DEteam4/datasetVer3',
                        filename=f'frames/{movie_id}/frame_{f:06d}.jpg',
                        repo_type='dataset'
                    )
                    clip_feats.append(extract_cnn_feature(img_path))
                except:
                    clip_feats.append(np.zeros(2048))
            if len(clip_feats) == clip_len:
                if label == 'violence':
                    vio_clips.append(clip_feats)
                    vio_labels.append(label)
                else:
                    neg_clips.append(clip_feats)
                    neg_labels.append(label)

    # violence 클립 수 기준으로 neg_easy 자동 조정 (비율 1.5배)
    max_neg_clips = int(len(vio_clips) * neg_ratio)
    if len(neg_clips) > max_neg_clips:
        idx = np.random.choice(len(neg_clips), max_neg_clips, replace=False)
        neg_clips = [neg_clips[i] for i in idx]
        neg_labels = [neg_labels[i] for i in idx]

    return vio_clips + neg_clips, vio_labels + neg_labels

# 클립 추출
X_clips, y_clips, movie_ids = [], [], []
for movie in MY_MOVIES:
    print(f'{movie} 처리 중...')
    clips, labels = build_clips_from_hf(movie)
    X_clips.extend(clips)
    y_clips.extend(labels)
    movie_ids.extend([movie] * len(clips))
    print(f'{movie} 완료: {len(clips)}개 | 누적: {len(X_clips)}개')
    # 영화 하나 끝날 때마다 Drive에 저장
    np.save(f'{DRIVE_PATH}/{SAVE_NAME}_X.npy', np.array(X_clips, dtype=np.float32))
    np.save(f'{DRIVE_PATH}/{SAVE_NAME}_y.npy', np.array(y_clips))
    np.save(f'{DRIVE_PATH}/{SAVE_NAME}_movie_ids.npy', np.array(movie_ids))
    print('Drive 저장 완료')

print(f'\n총 클립: {len(X_clips)}개')
print(f'violence: {y_clips.count("violence")}개')
print(f'neg_easy: {y_clips.count("neg_easy")}개')

print(f'저장 완료!')
print(f'파일 위치: {DRIVE_PATH}/{SAVE_NAME}_X.npy')
```

---

## C. 업로드 확인

HuggingFace에서 확인:
https://huggingface.co/datasets/DEteam4/datasetVer3/tree/main/clips

---

## 주의사항

- `MY_MOVIES` 에 본인 담당 영화 이름만 넣을 것 (HuggingFace frames 폴더 이름과 동일하게)
- `SAVE_NAME` 은 팀원끼리 겹치지 않게 본인 이름으로 설정
- 영화 하나 끝날 때마다 자동 저장되니까 중간에 끊겨도 괜찮음
- 로컬 실행 시 `device = 'cpu'` 권장 (MPS는 백그라운드에서 불안정)
