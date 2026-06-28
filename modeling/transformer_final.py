import torch.nn as nn


class ViolenceTransformerFinal(nn.Module):
    """
    최종 모델: dropout=0.5, weight_decay=1e-4, CosineAnnealing
    attention weight 추출 기능 포함
    Test F1 (violence): 0.65 | Test Accuracy: 0.74
    """
    def __init__(self, input_size=2048, nhead=8, num_layers=2, dropout=0.5):
        super().__init__()
        self.layers = nn.ModuleList([
            nn.TransformerEncoderLayer(
                d_model=input_size,
                nhead=nhead,
                dim_feedforward=512,
                dropout=dropout,
                batch_first=True
            ) for _ in range(num_layers)
        ])
        self.fc = nn.Linear(input_size, 2)

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
