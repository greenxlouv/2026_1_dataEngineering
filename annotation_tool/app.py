import os
import json
import subprocess
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__)

# ── 설정 ──────────────────────────────────────────────
FRAMES_DIR = Path("images")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
FRAMES_DIR.mkdir(exist_ok=True)

# ── 유틸 ──────────────────────────────────────────────
def get_movie_list():
    return sorted([d.name for d in FRAMES_DIR.iterdir() if d.is_dir()])

def get_frames(movie_id):
    d = FRAMES_DIR / movie_id
    if not d.exists():
        return []
    frames = sorted(d.glob("frame_*.jpg")) + sorted(d.glob("frame_*.png"))
    return [f.name for f in frames]

def extract_frames(video_path, movie_id, fps=2):
    out_dir = FRAMES_DIR / movie_id
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(out_dir / "frame_%06d.jpg"),
        "-y", "-loglevel", "error"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, len(get_frames(movie_id))

def load_annotations(movie_id):
    txt = OUTPUT_DIR / f"{movie_id}.txt"
    scenes = []
    if txt.exists():
        with open(txt) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) == 5:
                    scenes.append({
                        "movie_id": parts[0].strip(),
                        "scene_num": int(parts[1].strip()),
                        "start_frame": int(parts[2].strip()),
                        "end_frame": int(parts[3].strip()),
                        "label": parts[4].strip()
                    })
    return scenes

def save_annotations(movie_id, scenes):
    txt = OUTPUT_DIR / f"{movie_id}.txt"
    # neg_easy auto-fill: find gaps between annotated scenes
    frames = get_frames(movie_id)
    total = len(frames)
    if total == 0:
        return

    annotated = sorted(scenes, key=lambda s: s["start_frame"])
    full_scenes = []
    scene_num = 1
    prev_end = 1

    for sc in annotated:
        if sc["start_frame"] > prev_end:
            full_scenes.append({
                "movie_id": movie_id,
                "scene_num": scene_num,
                "start_frame": prev_end,
                "end_frame": sc["start_frame"] - 1,
                "label": "neg_easy"
            })
            scene_num += 1
        sc["movie_id"] = movie_id
        sc["scene_num"] = scene_num
        full_scenes.append(sc)
        scene_num += 1
        prev_end = sc["end_frame"] + 1

    if prev_end <= total:
        full_scenes.append({
            "movie_id": movie_id,
            "scene_num": scene_num,
            "start_frame": prev_end,
            "end_frame": total,
            "label": "neg_easy"
        })

    with open(txt, "w") as f:
        for sc in full_scenes:
            f.write(f"{sc['movie_id']}, {sc['scene_num']}, {sc['start_frame']}, {sc['end_frame']}, {sc['label']}\n")

    return full_scenes

# ── 라우트 ─────────────────────────────────────────────
@app.route("/")
def index():
    movies = get_movie_list()
    return render_template("index.html", movies=movies)

@app.route("/api/movies")
def api_movies():
    return jsonify(get_movie_list())

@app.route("/api/extract", methods=["POST"])
def api_extract():
    data = request.json
    video_path = data.get("video_path", "")
    movie_id = data.get("movie_id", "")
    if not video_path or not movie_id:
        return jsonify({"ok": False, "error": "missing params"})
    if not Path(video_path).exists():
        return jsonify({"ok": False, "error": f"파일 없음: {video_path}"})
    ok, count = extract_frames(video_path, movie_id)
    return jsonify({"ok": ok, "count": count, "movie_id": movie_id})

@app.route("/api/frames/<movie_id>")
def api_frames(movie_id):
    frames = get_frames(movie_id)
    return jsonify({"frames": frames, "total": len(frames)})

@app.route("/api/frame/<movie_id>/<frame_name>")
def api_frame(movie_id, frame_name):
    return send_from_directory(FRAMES_DIR / movie_id, frame_name)

@app.route("/api/annotations/<movie_id>")
def api_get_annotations(movie_id):
    scenes = load_annotations(movie_id)
    # only return manually annotated (non-neg_easy auto-fill) ones
    manual = [s for s in scenes if s["label"] in ("violence", "neg_hard")]
    return jsonify({"scenes": manual})

@app.route("/api/annotations/<movie_id>", methods=["POST"])
def api_save_annotations(movie_id):
    data = request.json
    scenes = data.get("scenes", [])
    save_annotations(movie_id, scenes)
    return jsonify({"ok": True, "saved": len(scenes)})

@app.route("/api/export/<movie_id>")
def api_export(movie_id):
    scenes = load_annotations(movie_id)
    return jsonify({"scenes": scenes, "count": len(scenes)})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
