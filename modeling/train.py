"""
train.py
- .npy 파일 로드 (train/val/test)
- 영화별 StandardScaler 정규화
- 영화 단위 train/val split
- 모델 선택 & 학습
- Best checkpoint 저장

실행:
    python train.py --model transformer_final --epochs 30
"""

import argparse
import numpy as np
import torch
import torch.nn as nn
from pathlib import Path
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report
from torch.utils.data import Dataset, DataLoader

from lstm_model import ViolenceLSTM
from tcn_model import ViolenceTCN
from bilstm_model import ViolenceBiLSTM
from transformer_model import ViolenceTransformer
from transformer_final import ViolenceTransformerFinal

# ── 설정 ──────────────────────────────────────────────────────────
TRAIN_PATH = '/content/drive/MyDrive/ColabNotebooks/dataEng/DE_PJ2/npyForLSTM/trainandval'
TEST_PATH  = '/content/drive/MyDrive/ColabNotebooks/dataEng/DE_PJ2/npyForLSTM/test'
SAVE_DIR   = '/content/drive/MyDrive/ColabNotebooks/dataEng/DE_PJ2'

LABEL_MAP = {'neg_easy': 0, 'violence': 1}


# ── Dataset ───────────────────────────────────────────────────────
class ClipDataset(Dataset):
    def __init__(self, X, y):
        self.X = torch.tensor(X)
        self.y = torch.tensor(y)
    def __len__(self): return len(self.X)
    def __getitem__(self, idx): return self.X[idx], self.y[idx]


# ── 데이터 로드 ───────────────────────────────────────────────────
def load_npy(path):
    X_all, y_all, movie_all = [], [], []
    for x_path in sorted(Path(path).glob('*_X.npy')):
        name = x_path.stem.replace('_X', '')
        y_path = x_path.parent / f'{name}_y.npy'
        m_path = x_path.parent / f'{name}_movie_ids.npy'
        X_all.append(np.load(x_path))
        y_all.append(np.load(y_path))
        movie_all.append(np.load(m_path))
    X = np.concatenate(X_all).astype(np.float32)
    y_str = np.concatenate(y_all)
    movie_ids = np.concatenate(movie_all)
    y = np.array([LABEL_MAP[str(l)] for l in y_str], dtype=np.int64)
    return X, y, movie_ids


# ── 영화별 정규화 ─────────────────────────────────────────────────
def normalize_per_movie(X, movie_ids):
    X_norm = X.copy()
    for movie in set(movie_ids):
        idx = np.where(movie_ids == movie)[0]
        if len(idx) > 1:
            orig_shape = X[idx].shape
            flat = X[idx].reshape(len(idx), -1)
            flat = StandardScaler().fit_transform(flat)
            X_norm[idx] = flat.reshape(orig_shape)
    return X_norm


# ── 영화 단위 train/val split ─────────────────────────────────────
def movie_split(X, y, movie_ids, val_ratio=0.2, seed=42):
    unique_movies = list(set(movie_ids))
    np.random.seed(seed)
    np.random.shuffle(unique_movies)
    n = len(unique_movies)
    train_movies = unique_movies[:int(n * (1 - val_ratio))]
    val_movies   = unique_movies[int(n * (1 - val_ratio)):]

    train_idx = np.where(np.isin(movie_ids, train_movies))[0]
    val_idx   = np.where(np.isin(movie_ids, val_movies))[0]

    print(f'train: {len(train_idx)}개 ({len(train_movies)}개 영화)')
    print(f'val:   {len(val_idx)}개 ({len(val_movies)}개 영화)')
    return (X[train_idx], y[train_idx]), (X[val_idx], y[val_idx])


# ── 학습 루프 ─────────────────────────────────────────────────────
def train(model, train_loader, val_loader, device, epochs=30,
          lr=1e-3, weight_decay=0.0, use_scheduler=True, save_path=None):

    criterion = nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=20) \
                if use_scheduler else None

    best_val_acc = 0
    best_state   = None

    for epoch in range(epochs):
        # train
        model.train()
        total_loss, correct, total = 0, 0, 0
        for X_batch, y_batch in train_loader:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            optimizer.zero_grad()
            pred = model(X_batch)
            loss = criterion(pred, y_batch)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            correct += (pred.argmax(1) == y_batch).sum().item()
            total += len(y_batch)
        train_loss = total_loss / len(train_loader)
        train_acc  = correct / total
        if scheduler:
            scheduler.step()

        # val
        model.eval()
        val_loss, val_correct, val_total = 0, 0, 0
        all_preds, all_labels = [], []
        with torch.no_grad():
            for X_batch, y_batch in val_loader:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                pred = model(X_batch)
                val_loss    += criterion(pred, y_batch).item()
                val_correct += (pred.argmax(1) == y_batch).sum().item()
                val_total   += len(y_batch)
                all_preds.extend(pred.argmax(1).cpu().numpy())
                all_labels.extend(y_batch.cpu().numpy())
        val_loss /= len(val_loader)
        val_acc   = val_correct / val_total

        print(f'Epoch {epoch+1}/{epochs} | '
              f'Train Loss: {train_loss:.4f} | Val Loss: {val_loss:.4f} | '
              f'Train Acc: {train_acc:.4f} | Val Acc: {val_acc:.4f}')

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            best_state   = {k: v.clone() for k, v in model.state_dict().items()}
            print(f'  ✅ Best 모델 저장 (val_acc={val_acc:.4f})')

    model.load_state_dict(best_state)
    print('\n--- Val classification report ---')
    print(classification_report(all_labels, all_preds, target_names=['neg_easy', 'violence']))

    if save_path:
        torch.save(best_state, save_path)
        print(f'모델 저장 완료: {save_path}')

    return model


# ── 테스트 ────────────────────────────────────────────────────────
def test(model, X_test, y_test, device):
    model.eval()
    X_tensor = torch.tensor(X_test).to(device)
    with torch.no_grad():
        preds = model(X_tensor).argmax(1).cpu().numpy()
    print('\n--- Test classification report ---')
    print(classification_report(y_test, preds, target_names=['neg_easy', 'violence']))


# ── 메인 ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='transformer_final',
                        choices=['lstm', 'tcn', 'bilstm', 'transformer', 'transformer_final'])
    parser.add_argument('--epochs', type=int, default=30)
    parser.add_argument('--lr', type=float, default=1e-3)
    parser.add_argument('--weight_decay', type=float, default=1e-4)
    args = parser.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'디바이스: {device}')

    # 데이터 로드
    print('데이터 로드 중...')
    X, y, movie_ids = load_npy(TRAIN_PATH)
    print(f'총 클립: {len(X)}개 | violence: {sum(y==1)}개 | neg_easy: {sum(y==0)}개')

    # 정규화
    X_norm = normalize_per_movie(X, movie_ids)

    # split
    (X_train, y_train), (X_val, y_val) = movie_split(X_norm, y, movie_ids)

    train_loader = DataLoader(ClipDataset(X_train, y_train), batch_size=64, shuffle=True)
    val_loader   = DataLoader(ClipDataset(X_val,   y_val),   batch_size=64)

    # 모델 선택
    models = {
        'lstm':              ViolenceLSTM(),
        'tcn':               ViolenceTCN(),
        'bilstm':            ViolenceBiLSTM(),
        'transformer':       ViolenceTransformer(),
        'transformer_final': ViolenceTransformerFinal(),
    }
    model = models[args.model].to(device)
    print(f'모델: {args.model}')

    # 학습
    save_path = f'{SAVE_DIR}/best_{args.model}.pth'
    model = train(model, train_loader, val_loader, device,
                  epochs=args.epochs, lr=args.lr,
                  weight_decay=args.weight_decay,
                  save_path=save_path)

    # 테스트
    X_test, y_test, movie_test_ids = load_npy(TEST_PATH)
    X_test_norm = normalize_per_movie(X_test, movie_test_ids)
    test(model, X_test_norm, y_test, device)


if __name__ == '__main__':
    main()
