"""
per_movie_f1.py
- 영화별 F1 (violence) 바차트
- upper / lower 클러스터 분석
- 2-proportion z-test
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import torch
from sklearn.metrics import f1_score, accuracy_score, precision_score, recall_score
from scipy import stats
sys.path.append('../modeling')
from transformer_final import ViolenceTransformerFinal

LABEL_MAP = {'neg_easy': 0, 'violence': 1}


def load_data(x_path, y_path, movie_ids_path):
    X = np.load(x_path).astype(np.float32)
    y_str = np.load(y_path)
    movie_ids = np.load(movie_ids_path)
    y = np.array([LABEL_MAP[str(l)] for l in y_str], dtype=np.int64)
    return X, y, movie_ids


def per_movie_eval(model, X, y, movie_ids, device):
    model.eval()
    results = {}

    for movie in sorted(set(movie_ids)):
        idx = np.where(movie_ids == movie)[0]
        X_m = torch.tensor(X[idx]).to(device)
        y_m = y[idx]

        with torch.no_grad():
            preds = model(X_m).argmax(1).cpu().numpy()

        results[movie] = {
            'f1':        f1_score(y_m, preds, pos_label=1, zero_division=0),
            'acc':       accuracy_score(y_m, preds),
            'precision': precision_score(y_m, preds, pos_label=1, zero_division=0),
            'recall':    recall_score(y_m, preds, pos_label=1, zero_division=0),
            'n_clips':   len(idx),
            'n_vio':     int(sum(y_m == 1)),
            'n_neg':     int(sum(y_m == 0)),
            'preds':     preds,
            'labels':    y_m,
        }

    return results


def plot_per_movie_f1(results, overall_f1=0.65, save_path=None):
    movies    = list(results.keys())
    f1_scores = [results[m]['f1'] for m in movies]
    colors    = ['#cc2222' if f < 0.55 else '#ff8800' if f < 0.65 else '#22aa44'
                 for f in f1_scores]

    fig, ax = plt.subplots(figsize=(14, 5))
    bars = ax.bar(range(len(movies)), f1_scores, color=colors)
    ax.axhline(y=overall_f1, color='#4488ff', linestyle='--',
               label=f'Overall F1={overall_f1}')
    ax.set_xticks(range(len(movies)))
    ax.set_xticklabels(movies, rotation=45, ha='right', fontsize=9)
    ax.set_ylabel('F1 Score (violence)')
    ax.set_title('Violence F1 Score by Movie')
    ax.set_ylim(0, 1.0)
    ax.legend()
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f'저장: {save_path}')
    else:
        plt.show()


def cluster_ztest(results):
    """F1 기준으로 upper/lower 클러스터 분리 후 2-proportion z-test"""
    f1_scores = {m: results[m]['f1'] for m in results}
    median_f1 = np.median(list(f1_scores.values()))

    upper = [m for m, f in f1_scores.items() if f >= median_f1]
    lower = [m for m, f in f1_scores.items() if f <  median_f1]

    def concat(movies, key):
        return np.concatenate([results[m][key] for m in movies])

    for cluster_name, movies in [('Upper', upper), ('Lower', lower)]:
        labels = concat(movies, 'labels')
        preds  = concat(movies, 'preds')
        f1  = f1_score(labels, preds, pos_label=1, zero_division=0)
        rec = recall_score(labels, preds, pos_label=1, zero_division=0)
        pre = precision_score(labels, preds, pos_label=1, zero_division=0)
        acc = accuracy_score(labels, preds)
        print(f'{cluster_name} cluster ({len(movies)}편): '
              f'F1={f1:.3f} | Recall={rec:.3f} | Precision={pre:.3f} | Acc={acc:.3f}')

    # 2-proportion z-test (recall)
    def recall_counts(movies):
        tp = sum(
            sum((results[m]['labels'] == 1) & (results[m]['preds'] == 1))
            for m in movies
        )
        n  = sum(sum(results[m]['labels'] == 1) for m in movies)
        return int(tp), int(n)

    tp_u, n_u = recall_counts(upper)
    tp_l, n_l = recall_counts(lower)

    p_u = tp_u / n_u if n_u > 0 else 0
    p_l = tp_l / n_l if n_l > 0 else 0
    p_pool = (tp_u + tp_l) / (n_u + n_l)
    se = np.sqrt(p_pool * (1 - p_pool) * (1/n_u + 1/n_l))
    z  = (p_u - p_l) / se if se > 0 else 0
    p_val = 2 * (1 - stats.norm.cdf(abs(z)))
    print(f'\n2-proportion z-test (recall): z={z:.2f}, p={p_val:.2e}')
    print(f'Gap: {abs(p_u - p_l):.3f} (upper={p_u:.3f}, lower={p_l:.3f})')


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

    results = per_movie_eval(model, X, y, movie_ids, device)

    print('\n=== 영화별 성능 ===')
    print(f'{"movie":20s} {"n_clips":>8} {"f1":>6} {"acc":>6} {"precision":>10} {"recall":>8}')
    for m, r in sorted(results.items(), key=lambda x: -x[1]['f1']):
        print(f'{m:20s} {r["n_clips"]:>8} {r["f1"]:>6.3f} {r["acc"]:>6.3f} '
              f'{r["precision"]:>10.3f} {r["recall"]:>8.3f}')

    print('\n=== 클러스터 분석 ===')
    cluster_ztest(results)
    plot_per_movie_f1(results, save_path=args.save)
