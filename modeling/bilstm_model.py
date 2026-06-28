import torch.nn as nn


class ViolenceBiLSTM(nn.Module):
    def __init__(self, input_size=2048, hidden_size=256, num_layers=2, dropout=0.3):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size, hidden_size, num_layers,
            batch_first=True, dropout=dropout,
            bidirectional=True
        )
        self.fc = nn.Linear(hidden_size * 2, 2)  # bidirectional이라 *2

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])
