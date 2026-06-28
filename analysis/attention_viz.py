"""
attention_viz.py
- ViolenceTransformerFinal의 attention weight 히트맵 시각화
- violence vs neg_easy 클립 비교
"""

import sys
import numpy as np
import matplotlib.pyplot as plt
import torch
sys.path.append('../modeling')
from transformer_final import ViolenceTransformerFinal

LABEL_MAP = {'neg_easy': 0, 'violence': 1}
IDX_TO_LABEL = {0: 'neg_easy', 1: 'violence'}


def load_data(x_path, y_path, movie_ids_path):
    X = np.load(x_path).astype(np.float32)
    y_str = np.load(y_path)
    movie_ids = np.load(movie_ids_path)
    y = np.array([LABEL_MAP[str(l)] for l in y_str], dtype=np.int64)
    return X, y, movie_ids


def visualize_attention(model, X, y, movie_ids, device, n_movies=4, save_path=None):
    model.eval()
    sample_movies = sorted(set(movie_ids))[:n_movies]

    fig, axes = plt.subplots(len(sample_movies), 2, figsize=(12, 4 * len(sample_movies)))
    if len(sample_movies) == 1:
        axes = [axes]

    for row, movie in enumerate(sample_movies):
        m_idx = np.where(movie_ids == movie)[0]
        vio_idx = m_idx[np.where(y[m_idx] == 1)[0]]
        neg_idx = m_idx[np.where(y[m_idx] == 0)[0]]

        if len(vio_idx) == 0 or len(neg_idx) == 0:
            continue

        for col, (sample_idx, label_name) in enumerate([
            (vio_idx[0], 'violence'),
            (neg_idx[0], 'neg_easy')
        ]):
            sample = torch.tensor(X[sample_idx:sample_idx + 1]).to(device)
            with torch.no_grad():
                logit, attn_weights = model(sample, return_attn=True)
                pred = logit.argmax(1).item()

            attn_map = attn_weights[-1][0].numpy()  # 마지막 레이어 attention
            ax = axes[row][col]
            im = ax.imshow(attn_map, cmap='hot')
            ax.set_title(
                f'{movie}\n'
                f'True={label_name} | Pred={IDX_TO_LABEL[pred]}',
                fontsize=10
            )
            ax.set_xticks(range(4))
            ax.set_yticks(range(4))
            ax.set_xticklabels([f'f{i+1}' for i in range(4)])
            ax.set_yticklabels([f'f{i+1}' for i in range(4)])
            ax.set_xlabel('Value (Key)')
            ax.set_ylabel('Query')
            plt.colorbar(im, ax=ax)

    plt.suptitle('Attention Weight Visualization: Violence vs Neg_easy', fontsize=13)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches='tight')
        print(f'저장: {save_path}')
    else:
        plt.show()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--x',         required=True, help='X.npy 경로')
    parser.add_argument('--y',         required=True, help='y.npy 경로')
    parser.add_argument('--movie_ids', required=True, help='movie_ids.npy 경로')
    parser.add_argument('--model',     required=True, help='best_model.pth 경로')
    parser.add_argument('--n_movies',  type=int, default=4)
    parser.add_argument('--save',      default=None,  help='저장 경로 (없으면 plt.show)')
    args = parser.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    X, y, movie_ids = load_data(args.x, args.y, args.movie_ids)

    model = ViolenceTransformerFinal().to(device)
    model.load_state_dict(torch.load(args.model, map_location=device))

    visualize_attention(model, X, y, movie_ids, device,
                        n_movies=args.n_movies, save_path=args.save)
