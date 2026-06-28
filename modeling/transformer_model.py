import torch.nn as nn


class ViolenceTransformer(nn.Module):
    def __init__(self, input_size=2048, nhead=8, num_layers=2, dropout=0.3):
        super().__init__()
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=input_size,
            nhead=nhead,
            dim_feedforward=512,
            dropout=dropout,
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(input_size, 2)

    def forward(self, x):
        out = self.transformer(x)
        return self.fc(out[:, -1, :])
