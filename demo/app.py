"""
영화 폭력 구간 탐지 데모
ResNet50 피처 + Transformer → 클립별 폭력 확률 → 구간 탐지

실행: streamlit run app.py

모델 파일(best_model.pth)이 없으면 더미 추론으로 동작합니다.
"""

import os
import math
import tempfile
import subprocess
from pathlib import Path

import numpy as np
import streamlit as st
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy.ndimage import gaussian_filter1d

# ── PyTorch (선택적) ───────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    from torchvision import models, transforms
    from PIL import Image
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

# ── 설정 ──────────────────────────────────────────────────────────
CLIP_LEN     = 4        # 클립당 프레임 수
FPS          = 2        # 추출 fps
T_HIGH       = 0.45     # hysteresis 시작 임계값
T_LOW        = 0.30     # hysteresis 유지 임계값
GAUSS_SIGMA  = 2.0      # Gaussian smoothing sigma
MODEL_PATH   = Path(__file__).parent / "best_model.pth"

# ── Transformer 모델 정의 ──────────────────────────────────────────
if TORCH_AVAILABLE:
    class ViolenceTransformerFinal(nn.Module):
        def __init__(self, input_size=2048, nhead=8, num_layers=2, dropout=0.5):
            super().__init__()
            self.layers = nn.ModuleList([
                nn.TransformerEncoderLayer(
                    d_model=input_size, nhead=nhead,
                    dim_feedforward=512, dropout=dropout,
                    batch_first=True
                ) for _ in range(num_layers)
            ])
            self.fc = nn.Linear(input_size, 2)

        def forward(self, x):
            out = x
            for layer in self.layers:
                out = layer(out)
            return self.fc(out[:, -1, :])

# ── 모델 / 피처 추출기 로드 (캐시) ────────────────────────────────
@st.cache_resource
def load_models():
    if not TORCH_AVAILABLE:
        return None, None, None

    device = torch.device("cpu")

    # ResNet50 frozen feature extractor
    resnet = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V1)
    resnet = torch.nn.Sequential(*list(resnet.children())[:-1])
    resnet.eval().to(device)

    preprocess = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225]),
    ])

    # Transformer
    model = ViolenceTransformerFinal().to(device)
    if MODEL_PATH.exists():
        state = torch.load(MODEL_PATH, map_location=device)
        model.load_state_dict(state)
        st.sidebar.success("✅ best_model.pth 로드됨")
    else:
        st.sidebar.warning("⚠️ best_model.pth 없음 → 더미 추론 모드")
    model.eval()

    return resnet, model, preprocess

# ── 프레임 추출 ────────────────────────────────────────────────────
def extract_frames(video_path: str, out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={FPS}",
        "-q:v", "2",
        os.path.join(out_dir, "frame_%06d.jpg"),
        "-y", "-loglevel", "error"
    ]
    subprocess.run(cmd, check=True)
    frames = sorted(Path(out_dir).glob("frame_*.jpg"))
    return [str(f) for f in frames]

# ── CNN 피처 추출 ──────────────────────────────────────────────────
def extract_cnn_feature(img_path: str, resnet, preprocess, device) -> np.ndarray:
    img = Image.open(img_path).convert("RGB")
    t = preprocess(img).unsqueeze(0).to(device)
    with torch.no_grad():
        feat = resnet(t).squeeze().cpu().numpy()   # (2048,)
    return feat

# ── 더미 추론 (모델 없을 때) ───────────────────────────────────────
def dummy_inference(n_clips: int) -> np.ndarray:
    """실제 모델 없이 그럴듯한 확률 시계열 생성"""
    np.random.seed(42)
    base = np.random.beta(1.5, 4.0, n_clips)   # 전반적으로 낮은 확률
    # 몇 군데 폭력 구간 spike 추가
    for center in [int(n_clips * r) for r in [0.08, 0.2, 0.35, 0.55, 0.72, 0.88]]:
        width = np.random.randint(8, 25)
        for i in range(max(0, center - width), min(n_clips, center + width)):
            dist = abs(i - center) / width
            base[i] = min(1.0, base[i] + (0.85 - 0.3 * dist) * np.random.uniform(0.7, 1.0))
    return base.astype(np.float32)

# ── 실제 추론 ──────────────────────────────────────────────────────
def run_inference(frames: list[str], resnet, model, preprocess) -> np.ndarray:
    device = next(model.parameters()).device
    n_clips = max(0, len(frames) - CLIP_LEN + 1)
    probs = []

    progress = st.progress(0, text="추론 중...")
    for i in range(n_clips):
        clip_frames = frames[i: i + CLIP_LEN]
        feats = np.stack([extract_cnn_feature(f, resnet, preprocess, device)
                          for f in clip_frames])          # (4, 2048)
        x = torch.tensor(feats, dtype=torch.float32).unsqueeze(0).to(device)
        with torch.no_grad():
            logits = model(x)
            p = torch.softmax(logits, dim=-1)[0, 1].item()  # violence 확률
        probs.append(p)
        progress.progress((i + 1) / n_clips, text=f"추론 중... {i+1}/{n_clips}")

    progress.empty()
    return np.array(probs, dtype=np.float32)

# ── Gaussian smoothing + Hysteresis 구간 탐지 ─────────────────────
def detect_segments(probs: np.ndarray, sigma=GAUSS_SIGMA,
                    t_high=T_HIGH, t_low=T_LOW, clip_duration=0.5):
    smoothed = gaussian_filter1d(probs, sigma=sigma)

    in_segment = False
    segments = []
    start_i = 0

    for i, p in enumerate(smoothed):
        if not in_segment and p >= t_high:
            in_segment = True
            start_i = i
        elif in_segment and p < t_low:
            in_segment = False
            segments.append((start_i, i - 1))
    if in_segment:
        segments.append((start_i, len(smoothed) - 1))

    # 클립 인덱스 → 초 단위 변환
    result = []
    for s, e in segments:
        start_sec = s * clip_duration
        end_sec   = (e + CLIP_LEN) * clip_duration
        duration  = end_sec - start_sec
        peak_prob = float(smoothed[s:e+1].max())
        result.append({
            "start_sec": round(start_sec, 1),
            "end_sec":   round(end_sec,   1),
            "duration":  round(duration,  1),
            "peak_prob": round(peak_prob, 2),
        })
    return smoothed, result

# ── 타임라인 차트 ─────────────────────────────────────────────────
def plot_timeline(smoothed: np.ndarray, segments: list[dict],
                  total_clips: int, clip_duration=0.5):
    times = np.arange(len(smoothed)) * clip_duration

    fig, ax = plt.subplots(figsize=(12, 3))
    fig.patch.set_facecolor("#0e1117")
    ax.set_facecolor("#0e1117")

    # 폭력 구간 음영
    for seg in segments:
        ax.axvspan(seg["start_sec"], seg["end_sec"],
                   alpha=0.25, color="#ff4444", zorder=1)

    # smoothed 확률 곡선
    ax.plot(times, smoothed, color="#4c9be8", linewidth=1.2,
            label="violence prob (smoothed)", zorder=2)
    ax.axhline(T_HIGH, color="#ffaa00", linewidth=0.8,
               linestyle="--", label=f"t_high={T_HIGH} (start)", zorder=3)
    ax.axhline(T_LOW,  color="#ff6666", linewidth=0.8,
               linestyle=":",  label=f"t_low={T_LOW} (hold)",  zorder=3)

    ax.set_xlim(0, times[-1] if len(times) else 1)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("time (sec)", color="#aaa", fontsize=9)
    ax.set_ylabel("violence probability", color="#aaa", fontsize=9)
    ax.set_title("Violence timeline", color="#eee", fontsize=11)
    ax.tick_params(colors="#888")
    for spine in ax.spines.values():
        spine.set_edgecolor("#333")
    ax.legend(fontsize=8, facecolor="#1a1a2e", labelcolor="#ccc",
              loc="upper right", framealpha=0.7)

    plt.tight_layout()
    return fig

# ── Progress bar SVG (영상 하단 폭력 구간 표시) ───────────────────
def violence_progress_bar_html(segments: list[dict], total_sec: float) -> str:
    if total_sec <= 0:
        return ""
    bars = ""
    for seg in segments:
        left  = seg["start_sec"] / total_sec * 100
        width = (seg["end_sec"] - seg["start_sec"]) / total_sec * 100
        bars += (f'<div style="position:absolute;left:{left:.2f}%;'
                 f'width:{width:.2f}%;height:100%;'
                 f'background:#cc2222;border-radius:2px;opacity:0.85"></div>')
    return f"""
    <div style="position:relative;width:100%;height:14px;
                background:#2a2a2a;border-radius:4px;overflow:hidden;margin-top:4px">
        {bars}
    </div>
    <div style="font-size:11px;color:#888;margin-top:3px">
        🔴 폭력 구간 (바를 클릭하면 이동)
    </div>
    """

# ── Streamlit UI ──────────────────────────────────────────────────
def fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}.{int((sec % 1) * 10)}"

def main():
    st.set_page_config(
        page_title="영화 폭력 구간 탐지 데모",
        page_icon="🎬",
        layout="wide"
    )

    st.markdown("## 🎬 영화 폭력 구간 탐지 데모")
    st.caption(
        "영상 업로드 → 폭력 구간(start-end) 타임라인. "
        "ResNet50 피처 + Transformer (test Acc 0.74). "
        "학습/test 71편 외 영상으로 데모하세요."
    )

    # 모델 로드
    resnet, transformer, preprocess = load_models()
    use_dummy = (not TORCH_AVAILABLE) or (not MODEL_PATH.exists())

    # ── 업로드 ──
    st.markdown("#### 영상 업로드")
    uploaded = st.file_uploader("", type=["mp4", "avi", "mov", "mkv"])

    if not uploaded:
        st.info("영상을 업로드하면 폭력 구간을 자동으로 탐지합니다.")
        return

    # 임시 파일로 저장
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(uploaded.read())
        video_path = tmp.name

    # ── 지표 카드 (프레임 추출 후) ──
    with st.spinner("프레임 추출 중..."):
        with tempfile.TemporaryDirectory() as frame_dir:
            try:
                frames = extract_frames(video_path, frame_dir)
            except Exception as e:
                st.error(f"ffmpeg 오류: {e}\nffmpeg이 설치되어 있는지 확인하세요.")
                return

            n_clips = max(0, len(frames) - CLIP_LEN + 1)
            total_sec = len(frames) / FPS

            # ── 추론 ──
            with st.spinner("추론 중..." if not use_dummy else "더미 추론 중..."):
                if use_dummy or transformer is None:
                    probs = dummy_inference(n_clips)
                else:
                    probs = run_inference(frames, resnet, transformer, preprocess)

    smoothed, segments = detect_segments(probs)

    violence_ratio = sum(
        seg["end_sec"] - seg["start_sec"] for seg in segments
    ) / total_sec if total_sec > 0 else 0

    # ── 지표 카드 ──
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("영상 길이",   fmt_time(total_sec))
    c2.metric("클립 수",     f"{n_clips:,}")
    c3.metric("폭력 구간",   f"{len(segments)}개")
    c4.metric("영상 내 폭력 클립 비율", f"{violence_ratio*100:.0f}%")

    if segments:
        st.caption(
            "⚠️ '영상 내 폭력 클립 비율'은 이 영상 안에서의 상대값입니다. "
            "피처가 영상 단위로 정규화되므로 서로 다른 영상끼리 이 수치를 비교해 "
            "'어느 영화가 더 폭력적인가'를 판단하면 안 됩니다. "
            "(영화 단위 폭력 척도는 이 시스템의 유효한 출력이 아닙니다)"
        )

    # ── 영상 플레이어 ──
    st.video(video_path)
    st.markdown(
        violence_progress_bar_html(segments, total_sec),
        unsafe_allow_html=True
    )

    # ── 타임라인 차트 ──
    st.markdown("#### Violence timeline")
    if len(probs) > 0:
        fig = plot_timeline(smoothed, segments, n_clips)
        st.pyplot(fig)
        plt.close(fig)
    else:
        st.warning("클립 수가 너무 적습니다.")

    # ── 탐지된 폭력 구간 테이블 ──
    st.markdown("#### 탐지된 폭력 구간")
    if segments:
        rows = []
        for seg in segments:
            rows.append({
                "구간": f"{fmt_time(seg['start_sec'])} ~ {fmt_time(seg['end_sec'])}",
                "길이(초)": seg["duration"],
                "최고확률": seg["peak_prob"],
            })
        st.table(rows)
    else:
        st.success("탐지된 폭력 구간 없음")

    # ── 사이드바: 파라미터 조정 ──
    with st.sidebar:
        st.markdown("### ⚙️ 탐지 파라미터")
        t_high_ui = st.slider("t_high (시작 임계값)", 0.1, 0.9, T_HIGH, 0.05)
        t_low_ui  = st.slider("t_low (유지 임계값)",  0.1, 0.9, T_LOW,  0.05)
        sigma_ui  = st.slider("Gaussian sigma",       0.5, 5.0, GAUSS_SIGMA, 0.5)

        if st.button("재탐지"):
            smoothed2, segments2 = detect_segments(
                probs, sigma=sigma_ui, t_high=t_high_ui, t_low=t_low_ui
            )
            st.rerun()

    # 임시 파일 정리
    try:
        os.unlink(video_path)
    except Exception:
        pass

if __name__ == "__main__":
    main()
