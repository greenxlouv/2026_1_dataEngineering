
import re

import numpy as np

import torch

import torchvision.models as models

import torchvision.transforms as transforms

from PIL import Image

import os

device = 'cpu'

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

def build_clips_local(movie_id, clip_len=4, stride=2, max_neg_clips=50):

    base = '/Users/bluecloud/Documents/대학/데엔/pj2/violence_annotator'

    txt_path = f'{base}/output/{movie_id}.txt'

    frames_dir = f'{base}/images/{movie_id}'

    scenes = []

    with open(txt_path, 'r') as f:

        for line in f:

            match = re.match(r'\[(.+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\w+)\]', line.strip())

            if match:

                start = int(match.group(3))

                end = int(match.group(4))

                label = match.group(5)

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

    if len(neg_clips) > max_neg_clips:

        idx = np.random.choice(len(neg_clips), max_neg_clips, replace=False)

        neg_clips = [neg_clips[i] for i in idx]

        neg_labels = [neg_labels[i] for i in idx]

    return vio_clips + neg_clips, vio_labels + neg_labels

movies = ['doorlock', 'evil_city', 'geunomdida', 'goksung',

          'mission_possible', 'mokgyeok', 'myway', 'smartphone', 'thephone', 'veteran']

X_clips, y_clips, movie_ids = [], [], []

for movie in movies:

    print(f'{movie} 처리 중...')

    clips, labels = build_clips_local(movie)

    X_clips.extend(clips)

    y_clips.extend(labels)

    movie_ids.extend([movie] * len(clips))

    print(f'{movie} 완료: {len(clips)}개 | 누적: {len(X_clips)}개')

    np.save('/Users/bluecloud/Documents/대학/데엔/pj2/X_clips_local2.npy', np.array(X_clips, dtype=np.float32))

    np.save('/Users/bluecloud/Documents/대학/데엔/pj2/y_clips_local2.npy', np.array(y_clips))

    np.save('/Users/bluecloud/Documents/대학/데엔/pj2/movie_ids_local2.npy', np.array(movie_ids))

    print('저장 완료')

print(f'\n총 클립: {len(X_clips)}개')

print(f'violence: {y_clips.count("violence")}개')

print(f'neg_easy: {y_clips.count("neg_easy")}개')

