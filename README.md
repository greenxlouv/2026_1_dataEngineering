# 🎬 Automated Violence Detection for Film Classification

> **Data Engineering Course Project 2** | 2026-1 DataEngineering class prj2 
> Korean movie violence detection pipeline — from raw YouTube clips to a working demo  

---

## 📌 Overview

OTT platforms have surged in volume, creating a bottleneck for the Korea Media Rating Board (KMRB): human reviewers handle 15–20 films per week, with months of backlog. This project automates the detection of **violence intervals** in film footage to assist the rating process.

Unlike smoking or nudity detection (frame-level, object-presence tasks), **violence is inherently sequential** — a single frame tells you nothing. We built an end-to-end pipeline that learns *temporal patterns* across frame sequences to predict violence intervals with second-level granularity.

**Final result:** Transformer Encoder — Test F1 (violence) **0.65**, Test Accuracy **0.74** (+6%p over traditional ML baseline)

---

## 🗂️ Repository Structure

```
violence-detection/
│
├── data_collection/
│   └── download_clips.py          # yt-dlp wrapper for YouTube movie review clips
│
├── annotation_tool/               # Custom Flask-based annotation app 
│   ├── app.py
│   ├── templates/
│   └── README_annotator.md        # How to run the annotator locally
│
├── preprocessing/
│   ├── extract_frames.py          # ffmpeg frame extraction at 2fps
│   ├── build_clips.py             # ResNet50 feature extraction → (N, 4, 2048) clips
│   └── upload_to_hf.py            # HuggingFace dataset upload script
│
├── modeling/
│   ├── baselines.py               # Logistic Regression, Linear SVM (mean+max pooling)
│   ├── lstm_model.py
│   ├── tcn_model.py
│   ├── bilstm_model.py
│   ├── transformer_model.py       # ViolenceTransformer (base)
│   ├── transformer_final.py       # ViolenceTransformerFinal (with attention export)
│   └── train.py                   # Training loop, CosineAnnealing, checkpoint saving
│
├── analysis/
│   ├── attention_viz.py           # Per-clip attention weight heatmaps
│   ├── per_movie_f1.py            # Per-movie F1 bar charts + cluster analysis
│   └── threshold_tuning.py        # Hysteresis dual-threshold sweep
│
├── demo/
│   └── app.py                     # Streamlit demo: video upload → violence timeline
│
├── notebooks/
│   └── DE_PJ2_modeling.ipynb      # Full Colab training notebook (T4 GPU)
│
├── dataset/                       # Not tracked by git (see HuggingFace)
│   ├── frames/{movie_id}/frame_{N:06d}.jpg
│   ├── annotations/{movie_id}.txt
│   └── test_annotations/{movie_id}.txt
│
└── README.md
```

---

## 🔧 Pipeline

```
YouTube (20–40min review videos)
    ↓  yt-dlp
Raw .mp4 clips (action, crime, noir, thriller genres)
    ↓  ffmpeg @ 2fps
Frame images  →  HuggingFace (DEteam4/datasetVer3)
    ↓  Custom Flask Annotation Tool
Sequence-level labels: violence / neg_hard / neg_easy
    ↓  build_clips.py (ResNet50 frozen, per-movie StandardScaler)
X.npy (N, 4, 2048)  +  y.npy (N,)  +  movie_ids.npy (N,)
    ↓  Movie-level train/val/test split
Temporal models: LSTM → TCN → BiLSTM → Transformer
    ↓  Best: ViolenceTransformerFinal
Violence interval prediction [start_sec, end_sec]
```

**Dataset stats:** 62 videos / 174,253 frames / ~38,913 clips  
Train: 49 movies (27,513 clips) | Val: 13 movies (6,205 clips) | Test: 9 movies (5,195 clips)

---

## 🏷️ Annotation Tool (`violence_annotator`)

Built from scratch as a custom Flask web app — because no existing tool supported our specific sequence-level annotation workflow.

**Features:**
- Keyboard-driven navigation (no mouse needed for speed)
- `[1]` to mark violence start → `[1]` again to end; `[2]` for neg_hard
- Progress bar visualizing annotated segments in real-time
- Auto-fill: unannotated intervals automatically assigned `neg_easy`
- TXT export format: `[movie_id, scene_num, start_frame, end_frame, label]`

**Why we built our own:** CVAT (used in Project 1 for bbox annotation) doesn't support sequence-level interval labeling. Existing tools required frame-by-frame clicking — too slow for 20–40 minute review videos at 2fps.

**Run locally:**
```bash
cd annotation_tool
pip install flask
python app.py
# → Navigate to http://localhost:5000
# → Load .mp4 or frames directory
```

---

## 🧠 Modeling: The Journey

### Why not just object detection?

We initially tried bounding box detection (used in our Project 1 for smoking/drinking). But violence isn't about *what object is present* — it's about *what action unfolds across time*. A person raising their arm is ambiguous in one frame. Three frames later, it's a punch.

### Attempt 1: MediaPipe Pose Detection ❌

**Hypothesis:** Violence = abnormal body movements → extract 33 joint keypoints per frame → feed sequences to LSTM.

**What happened:** MediaPipe failed exactly when we needed it most:
- Close-up shots → full body not visible → keypoints all zeros
- Motion blur during fight scenes → unstable/missing detection
- Dark scenes (noir genre) → detection failed entirely

These aren't edge cases — they're the *majority* of violence scenes in Korean action films.

**Decision:** Pivot to CNN-based visual feature extraction.

### Attempt 2: Optical Flow (parallel exploration)

Farneback dense optical flow to capture pixel-level motion between frames. Optimized for speed (320×240 resize, reduced pyramid levels → ~8× speedup). Promising visualization — violence clips showed large, chaotic flow vectors vs. calm flow in neg_easy.

But compressing all pixel flow to a single magnitude value per frame pair lost too much spatial information. Val F1 plateaued at 0.66 even with clip_length=8. Treated as an informative negative result rather than a dead end.

### Attempt 3: CNN Feature Extraction ✅

**ResNet50 (frozen, ImageNet pretrained)**:
```python
resnet = models.resnet50(weights='IMAGENET1K_V1')
resnet = torch.nn.Sequential(*list(resnet.children())[:-1])
resnet.eval().to(device)
# → 2048-dim feature vector per frame
```

4 consecutive frames → clip shape `(4, 2048)` → 2 seconds of context at 2fps. Why 4? Minimum unit to capture one complete action (punch, kick, grab).

**Per-movie normalization:** Each movie has its own lighting and color temperature. A StandardScaler fit per movie prevents the model from learning "this dark-colored movie = violence."

**Neg_hard decision:** Originally 3 classes (violence / neg_hard / neg_easy). neg_hard (play-fighting, training scenes) was annotated as a boundary class but collapsed into neg_easy for training — because the real-world task is binary, and keeping neg_hard as a separate class created more noise than signal.

### Sequential Model Experiments

All models trained on Google Colab Pro (T4 GPU). Movie-level split to prevent data leakage — clips from the same movie in both train and val would let the model memorize visual style rather than learn violence patterns.

| Model | Test F1 (violence) | Test Acc | Notes |
|---|---|---|---|
| Logistic Regression | 0.61 | 0.68 | mean+max pool → flat (4096,) |
| Linear SVM | 0.608 | 0.68 | same flattening strategy |
| LSTM | 0.62 | 0.71 | hidden=256, layers=2, dropout=0.3 |
| TCN | 0.57 | 0.69 | channels=[128,128,256], kernel=2 |
| BiLSTM | 0.59 | 0.71 | bidirectional=True |
| Transformer (base) | 0.62 | 0.72 | nhead=8, layers=2, dropout=0.3 |
| **Transformer (final)** | **0.65** | **0.74** | dropout=0.5, weight_decay=1e-4, CosineAnnealing |

**Key insight on baselines:** LogReg/SVM with mean+max pooling achieved 0.68 accuracy — surprisingly competitive. This means: (1) ResNet50 features are rich enough that even a linear classifier does well, (2) the Transformer's +6%p gain comes specifically from learning temporal order, not just feature quality.

### Overfitting Problem & Fix

After training the base Transformer: Train Acc ≈ 0.99, Val Acc ≈ 0.66 → **gap of 0.33**. Root cause: movie-level domain shift (the model learned movie-specific visual styles rather than violence patterns) combined with limited training data.

Fixes applied:
- Dropout: 0.3 → **0.5**
- **Weight decay 1e-4** (Adam optimizer)
- **CosineAnnealing LR scheduler** (T_max=20)
- Best checkpoint saving (val_acc)

Result: Train Acc 0.79 vs Val Acc 0.73 → **gap reduced to 0.06**

### Final Model: ViolenceTransformerFinal

Extended to export attention weights per forward pass:
```python
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
```

**Attention visualization finding:**
- Violence clips → high attention concentrated on f3 (peak moment), near-zero on f4
- Neg_easy clips → attention evenly distributed across f1, f2, f3

The model learned to find the *peak frame* within a clip, not just classify based on average appearance.

---

## 📊 Per-Movie Analysis

Ran 2-proportion z-test on upper vs. lower performing test movies:

- Upper cluster (5 movies) avg F1: **0.671**
- Lower cluster (4 movies) avg F1: **0.547** → gap of 0.124
- Recall: 0.639 → 0.502 (Δ 0.137) — recall drop drives most of the gap
- Significance: Recall z=6.13, p=8.6×10⁻¹⁰

Root cause: not model conservatism but **representation mismatch** — lower-cluster movies (psychological violence, dark/night scenes, stylized cinematography) were underrepresented in training data.

Threshold tuning (t=0.25 instead of default 0.5) halved the gap to 0.062, but the remaining delta reflects a data distribution issue, not a hyperparameter issue.

---

## 🎮 Demo App

Upload any video → get violence timeline with [start, end] segments.

**Post-processing:**
- Gaussian smoothing on per-clip probabilities (neighboring clips pull up short dips)
- Hysteresis dual-threshold: activate at `t_high=0.45`, keep segment alive until below `t_low=0.30`
- Output: time range, duration, peak probability per segment

---

## 🛠️ Tech Stack

| Component | Tools |
|---|---|
| Data collection | `yt-dlp`, `ffmpeg` |
| Annotation | Custom Flask app (`violence_annotator`) |
| Storage | HuggingFace (`DEteam4/datasetVer3`), MinIO (S3-compatible) |
| Feature extraction | PyTorch, ResNet50 (torchvision) |
| Training | Google Colab Pro (T4 GPU) |
| Models | LSTM, TCN, BiLSTM, Transformer Encoder |
| Baselines | scikit-learn (LogReg, LinearSVC) |
| Demo | Streamlit |
| Analysis | matplotlib, scipy (z-test) |

---

## 💡 Lessons Learned

**What worked:**
- Movie-level split is non-negotiable for video datasets — clip-level split is data leakage by another name
- Per-movie normalization matters more than global normalization when data comes from different sources
- Simple baselines (LogReg on mean+max pooled features) are worth running first — they set a realistic ceiling
- Attention weight extraction costs almost nothing and gives interpretable diagnostics for free

**What didn't work as expected:**
- Pose detection fails hardest exactly in the scenes you care about most (fast, dark, close-up)
- Longer clip lengths help optical flow (clip_len=8 > 4 > 2) but don't close the gap with CNN
- TCN underperformed BiLSTM despite parallel computation advantage — likely because 4-frame sequences are too short for dilated convolutions to shine
- VideoMAE (explored by a teammate) showed promise but was too heavy for our compute budget

**What we'd do differently:**
- End-to-end fine-tuning instead of frozen ResNet50 — appearance features alone miss motion
- Collect full films instead of review clips for cleaner, longer violence segments
- Multi-annotator cross-checking to reduce inter-annotator subjectivity on neg_hard boundaries

---

## 🔗 Resources

- Dataset: [HuggingFace DEteam4/datasetVer3](https://huggingface.co/datasets/DEteam4/datasetVer3)
- KMRB Classification Criteria: http://ors.kmrb.or.kr/guide/case/classstandards.do
