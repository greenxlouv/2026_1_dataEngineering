"""
upload_to_hf.py
- 프레임 이미지 / 어노테이션 txt → HuggingFace 데이터셋 업로드
- 대상 repo: DEteam4/datasetVer3

실행:
    python upload_to_hf.py --type frames   --movie Asura --frames_dir ../dataset/frames
    python upload_to_hf.py --type ann      --movie Asura --ann_dir ../dataset/annotations
    python upload_to_hf.py --type frames   --all --frames_dir ../dataset/frames
    python upload_to_hf.py --type ann      --all --ann_dir ../dataset/annotations
"""

import argparse
from pathlib import Path
from huggingface_hub import HfApi, login

REPO_ID   = "DEteam4/datasetVer3"
REPO_TYPE = "dataset"


def upload_frames(api, movie_id, frames_dir):
    movie_path = Path(frames_dir) / movie_id
    if not movie_path.exists():
        print(f"[SKIP] 프레임 디렉토리 없음: {movie_path}")
        return

    frames = sorted(movie_path.glob("frame_*.jpg"))
    if not frames:
        print(f"[SKIP] 프레임 없음: {movie_id}")
        return

    print(f"[UPLOAD] {movie_id}: {len(frames)}프레임 업로드 중...")
    for frame in frames:
        api.upload_file(
            path_or_fileobj=str(frame),
            path_in_repo=f"frames/{movie_id}/{frame.name}",
            repo_id=REPO_ID,
            repo_type=REPO_TYPE,
        )
    print(f"[OK] {movie_id}: 프레임 업로드 완료")


def upload_annotation(api, movie_id, ann_dir):
    ann_path = Path(ann_dir) / f"{movie_id}.txt"
    if not ann_path.exists():
        print(f"[SKIP] 어노테이션 없음: {ann_path}")
        return

    api.upload_file(
        path_or_fileobj=str(ann_path),
        path_in_repo=f"annotations/{movie_id}.txt",
        repo_id=REPO_ID,
        repo_type=REPO_TYPE,
    )
    print(f"[OK] {movie_id}: 어노테이션 업로드 완료")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=["frames", "ann"],
                        help="업로드 타입: frames(프레임) / ann(어노테이션)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--movie", help="단일 영화 ID (예: Asura)")
    group.add_argument("--all",   action="store_true", help="전체 일괄 업로드")
    parser.add_argument("--frames_dir", default="../dataset/frames")
    parser.add_argument("--ann_dir",    default="../dataset/annotations")
    args = parser.parse_args()

    login()  # HuggingFace 토큰 입력
    api = HfApi()

    if args.movie:
        movies = [args.movie]
    else:
        if args.type == "frames":
            movies = sorted([d.name for d in Path(args.frames_dir).iterdir() if d.is_dir()])
        else:
            movies = sorted([p.stem for p in Path(args.ann_dir).glob("*.txt")])
        print(f"총 {len(movies)}개 영화 처리")

    for movie_id in movies:
        if args.type == "frames":
            upload_frames(api, movie_id, args.frames_dir)
        else:
            upload_annotation(api, movie_id, args.ann_dir)

    print("\n전체 업로드 완료")


if __name__ == "__main__":
    main()
