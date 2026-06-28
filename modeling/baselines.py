"""
baselines.py
- Logistic Regression / Linear SVM (mean+max pooling)
- (N, 4, 2048) → flatten → (N, 4096) → sklearn 분류기

결과:
  LogReg    Test F1: 0.61 | Acc: 0.68
  LinearSVM Test F1: 0.608 | Acc: 0.68
"""

import numpy as np
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import classification_report

TRAIN_PATH = '/content/drive/MyDrive/ColabNotebooks/dataEng/DE_PJ2/npyForLSTM/trainandval'
TEST_PATH  = '/content/drive/MyDrive/ColabNotebooks/dataEng/DE_PJ2/npyForLSTM/test'

LABEL_MAP = {'neg_easy': 0, 'violence': 1}


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


def pool_features(X):
    """(N, 4, 2048) → mean+max concat → (N, 4096)"""
    mean_feat = X.mean(axis=1)   # (N, 2048)
    max_feat  = X.max(axis=1)    # (N, 2048)
    return np.concatenate([mean_feat, max_feat], axis=1)  # (N, 4096)


def movie_split(X, y, movie_ids, val_ratio=0.2, seed=42):
    unique_movies = list(set(movie_ids))
    np.random.seed(seed)
    np.random.shuffle(unique_movies)
    n = len(unique_movies)
    train_movies = unique_movies[:int(n * (1 - val_ratio))]
    val_movies   = unique_movies[int(n * (1 - val_ratio)):]
    train_idx = np.where(np.isin(movie_ids, train_movies))[0]
    val_idx   = np.where(np.isin(movie_ids, val_movies))[0]
    return (X[train_idx], y[train_idx]), (X[val_idx], y[val_idx])


if __name__ == '__main__':
    # 로드 & 정규화
    X, y, movie_ids = load_npy(TRAIN_PATH)
    X_norm = normalize_per_movie(X, movie_ids)
    (X_train, y_train), (X_val, y_val) = movie_split(X_norm, y, movie_ids)

    # pooling
    X_train_flat = pool_features(X_train)
    X_val_flat   = pool_features(X_val)

    X_test, y_test, movie_test_ids = load_npy(TEST_PATH)
    X_test_norm = normalize_per_movie(X_test, movie_test_ids)
    X_test_flat = pool_features(X_test_norm)

    print(f'피처 shape | train: {X_train_flat.shape} | val: {X_val_flat.shape} | test: {X_test_flat.shape}')

    # Logistic Regression
    print('\n=== Logistic Regression ===')
    logreg = LogisticRegression(max_iter=1000, random_state=42)
    logreg.fit(X_train_flat, y_train)
    val_preds = logreg.predict(X_val_flat)
    print(f'[LogReg] VAL: acc={logreg.score(X_val_flat, y_val):.4f}')
    test_preds = logreg.predict(X_test_flat)
    print(f'[LogReg] TEST: acc={logreg.score(X_test_flat, y_test):.4f}')
    print(classification_report(y_test, test_preds, target_names=['neg_easy', 'violence']))

    # Linear SVM
    print('\n=== Linear SVM ===')
    svm = LinearSVC(max_iter=2000, random_state=42)
    svm.fit(X_train_flat, y_train)
    val_preds = svm.predict(X_val_flat)
    print(f'[LinearSVM] VAL: acc={svm.score(X_val_flat, y_val):.4f}')
    test_preds = svm.predict(X_test_flat)
    print(f'[LinearSVM] TEST: acc={svm.score(X_test_flat, y_test):.4f}')
    print(classification_report(y_test, test_preds, target_names=['neg_easy', 'violence']))
