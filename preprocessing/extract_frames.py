"""
extract_frames.py
- 다운받은 .mp4 영상에서 2fps로 프레임 추출
- 출력: dataset/frames/{movie_id}/frame_{N:06d}.jpg

실행:
    python extract_frames.py --video_dir ../dataset/videos --out_dir ../dataset/frames
    python extract_frames.py --video ../dataset/videos/Asura.mp4  # 단일 파일
"""

import argparse
import subprocess
from pathlib import Path


def extract_frames(video_path: str, out_dir: str, fps: int = 2) -> int:
    video_path = Path(video_path)
    movie_id   = video_path.stem
    out_path   = Path(out_dir) / movie_id
    out_path.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(out_path / "frame_%06d.jpg"),
        "-y", "-loglevel", "error"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[ERROR] {movie_id}: {result.stderr.strip()}")
        return 0

    n = len(list(out_path.glob("frame_*.jpg")))
    print(f"[OK] {movie_id}: {n}프레임 추출 → {out_path}")
    return n


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video",     help="단일 mp4 파일 경로")
    group.add_argument("--video_dir", help="mp4 파일이 모인 디렉토리")
    parser.add_argument("--out_dir",  default="../dataset/frames", help="프레임 출력 루트")
    parser.add_argument("--fps",      type=int, default=2)
    args = parser.parse_args()

    if args.video:
        videos = [args.video]
    else:
        videos = sorted(Path(args.video_dir).glob("*.mp4"))
        print(f"총 {len(videos)}개 영상 발견")

    total_frames = 0
    for v in videos:
        total_frames += extract_frames(str(v), args.out_dir, args.fps)

    print(f"\n완료: 총 {total_frames:,}프레임 추출")


if __name__ == "__main__":
    main()
