"""
threshold_tuning.py
- decision threshold 스윕 (0.0 ~ 1.0)
- upper / lower 클러스터별 F1 vs threshold 그래프
- 최적 threshold 탐색
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import torch
from sklearn.metrics import f1_score
sys.path.append('../modeling')
from transformer_final import ViolenceTransformerFinal

LABEL_MAP = {'neg_easy': 0, 'violence': 1}


def load_data(x_path, y_path, movie_ids_path):
    X = np.load(x_path).astype(np.float32)
    y_str = np.load(y_path)
    movie_ids = np.load(movie_ids_path)
    y = np.array([LABEL_MAP[str(l)] for l in y_str], dtype=np.int64)
    return X, y, movie_ids


def get_probs(model, X, device, batch_size=512):
    """softmax violence 확률 반환"""
    model.eval()
    probs = []
    for i in range(0, len(X), batch_size):
        batch = torch.tensor(X[i:i+batch_size]).to(device)
        with torch.no_grad():
            logits = model(batch)
            p = torch.softmax(logits, dim=1)[:, 1].cpu().numpy()
        probs.append(p)
    return np.concatenate(probs)


def threshold_sweep(probs, labels, thresholds=None):
    if thresholds is None:
        thresholds = np.arange(0.05, 0.95, 0.02)
    f1_list = []
    for t in thresholds:
        preds = (probs >= t).astype(int)
        f1_list.append(f1_score(labels, preds, pos_label=1, zero_division=0))
    return thresholds, np.array(f1_list)


def plot_threshold_sweep(results_by_cluster, save_path=None):
    colors = {'Upper': '#ffcc00', 'Lower': '#ff4444'}
    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor('#111')
    ax.set_facecolor('#111')

    best_t = {}
    for cluster_name, (thresholds, f1_list) in results_by_cluster.items():
        ax.plot(thresholds, f1_list, color=colors[cluster_name],
                linewidth=2, marker='s', markersize=3, label=cluster_name)
        best_idx = np.argmax(f1_list)
        best_t[cluster_name] = thresholds[best_idx]
        ax.axvline(thresholds[best_idx], color=colors[cluster_name],
                   linestyle='--', alpha=0.5)

    ax.set_xlabel('Decision threshold', color='#aaa')
    ax.set_ylabel('F1 (violence)', color='#aaa')
    ax.set_title('F1 vs Threshold — by Cluster', color='#eee')
    ax.tick_params(colors='#888')
    for spine in ax.spines.values():
        spine.set_edgecolor('#333')
    ax.legend(fontsize=10, facecolor='#222', labelcolor='#ccc')
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f'저장: {save_path}')
    else:
        plt.show()

    return best_t


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--x',         required=True)
    parser.add_argument('--y',         required=True)
    parser.add_argument('--movie_ids', required=True)
    parser.add_argument('--model',     required=True)
    parser.add_argument('--save',      default=None)
    args = parser.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    X, y, movie_ids = load_data(args.x, args.y, args.movie_ids)

    model = ViolenceTransformerFinal().to(device)
    model.load_state_dict(torch.load(args.model, map_location=device))

    probs = get_probs(model, X, device)

    # F1 기준 upper/lower 분리
    from sklearn.metrics import f1_score as _f1
    movie_f1 = {}
    for movie in set(movie_ids):
        idx = np.where(movie_ids == movie)[0]
        preds = (probs[idx] >= 0.5).astype(int)
        movie_f1[movie] = _f1(y[idx], preds, pos_label=1, zero_division=0)

    median_f1 = np.median(list(movie_f1.values()))
    upper_movies = [m for m, f in movie_f1.items() if f >= median_f1]
    lower_movies = [m for m, f in movie_f1.items() if f <  median_f1]

    def cluster_data(movies):
        idx = np.where(np.isin(movie_ids, movies))[0]
        return probs[idx], y[idx]

    thresholds = np.arange(0.05, 0.95, 0.02)
    results_by_cluster = {}
    for name, movies in [('Upper', upper_movies), ('Lower', lower_movies)]:
        p, l = cluster_data(movies)
        ts, f1s = threshold_sweep(p, l, thresholds)
        results_by_cluster[name] = (ts, f1s)
        best_idx = np.argmax(f1s)
        print(f'{name}: best threshold={ts[best_idx]:.2f}, F1={f1s[best_idx]:.3f}')

    best_t = plot_threshold_sweep(results_by_cluster, save_path=args.save)
    print(f'\n최적 threshold: {best_t}')
