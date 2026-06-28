import re
import numpy as np
import torch
import torchvision.models as models
import torchvision.transforms as transforms
from PIL import Image
import os

# ========================================
# ⚠️ 여기만 수정
BASE_PATH = '/Users/bluecloud/Documents/대학/데엔/pj2/violence_annotator'
SAVE_PATH = '/Users/bluecloud/Documents/대학/데엔/pj2'
MY_MOVIES = ['_8CiLAInaKk', 'J9oPKXbiG14', 'PgDp_1RvZfg']
SAVE_NAME = 'clips_jaelin_test'
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