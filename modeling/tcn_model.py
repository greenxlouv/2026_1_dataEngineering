import torch.nn as nn
from pytorch_tcn import TCN


class ViolenceTCN(nn.Module):
    def __init__(self, input_size=2048, num_channels=[128, 128, 256], dropout=0.3):
        super().__init__()
        self.tcn = TCN(
            num_inputs=input_size,
            num_channels=num_channels,
            kernel_size=2,
            dropout=dropout,
            input_shape='NLC'
        )
        self.fc = nn.Linear(num_channels[-1], 2)

    def forward(self, x):
        out = self.tcn(x)
        return self.fc(out[:, -1, :])
