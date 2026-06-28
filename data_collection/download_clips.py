"""
유튜브 공식 채널 클립 수집기
- yt-dlp로 채널별 클립 병렬 다운로드
- OpenCV로 N초 간격 프레임 추출
- 등급 메타데이터 파싱 후 폴더 분리 저장
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import yt_dlp

log = logging.getLogger("youtube_collector")

# ── 설정 ─────────────────────────────────────────────────
CHANNELS = {
    "KBS드라마":  "https://www.youtube.com/@KBSDrama",
    "MBC드라마":  "https://www.youtube.com/@mbcdrama",
    "tvN":        "https://www.youtube.com/@tvN",
    "JTBC드라마": "https://www.youtube.com/@JTBCDrama",
}

GRADE_KEYWORDS = {
    "전체관람가": "ALL",
    "12세":"12",
    "15세": "15",
    "청소년관람불가": "19",
}

FRAME_INTERVAL_SEC = 3   # 프레임 추출 간격 (초)
MAX_CLIPS_PER_CH   = 30  # 채널당 최대 클립 수


class YoutubeCollector:
    def __init__(self, output_dir: str = "data/raw/youtube", max_workers: int = 4):
        self.output_dir  = Path(output_dir)
        self.max_workers = max_workers
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 채널에서 클립 URL 목록 수집 ──────────────────
    def _fetch_clip_urls(self, channel_url: str) -> list[str]:
        ydl_opts = {
            "quiet":       True,
            "extract_flat": True,
            "playlistend": MAX_CLIPS_PER_CH,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            entries = info.get("entries", [])
            return [f"https://www.youtube.com/watch?v={e['id']}" for e in entries if e.get("id")]

    # ── 2. 단일 클립 다운로드 + 메타데이터 저장 ────────
    def _download_clip(self, url: str, save_dir: Path) -> Path | None:
        ydl_opts = {
            "format":        "mp4/bestvideo[ext=mp4]",
            "outtmpl":       str(save_dir / "%(id)s.%(ext)s"),
            "writeinfojson": True,
            "ignoreerrors":  True,
            "quiet":         True,
            "sleep_interval": 2,          # rate limit 방지
            "max_sleep_interval": 5,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            # 다운로드된 mp4 파일 경로 반환
            mp4_files = list(save_dir.glob("*.mp4"))
            return mp4_files[-1] if mp4_files else None
        except Exception as e:
            log.warning(f"다운로드 실패 {url}: {e}")
            return None

    # ── 3. 메타데이터에서 등급 파싱 ─────────────────────
    def _parse_grade(self, json_path: Path) -> str:
        if not json_path.exists():
            return "UNKNOWN"
        try:
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            text = meta.get("title", "") + " " + meta.get("description", "")
            for keyword, grade in GRADE_KEYWORDS.items():
                if keyword in text:
                    return grade
        except Exception:
            pass
        return "UNKNOWN"

    # ── 4. 영상에서 프레임 추출 ──────────────────────────
    def _extract_frames(self, video_path: Path, grade: str, channel: str) -> int:
        frame_dir = self.output_dir / "frames" / channel / grade / video_path.stem
        frame_dir.mkdir(parents=True, exist_ok=True)

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        interval = max(1, int(fps * FRAME_INTERVAL_SEC))

        idx, saved = 0, 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            if idx % interval == 0:
                out = frame_dir / f"frame_{idx:06d}.jpg"
                cv2.imwrite(str(out), frame)
                saved += 1
            idx += 1
        cap.release()
        return saved

    # ── 5. 단일 클립 처리 (다운로드 → 등급 파싱 → 프레임 추출) ──
    def _process_clip(self, url: str, channel: str) -> dict:
        tmp_dir = self.output_dir / "tmp" / channel
        tmp_dir.mkdir(parents=True, exist_ok=True)

        video_path = self._download_clip(url, tmp_dir)
        if not video_path:
            return {"url": url, "status": "download_failed"}

        json_path = video_path.with_suffix(".info.json")
        grade     = self._parse_grade(json_path)
        n_frames  = self._extract_frames(video_path, grade, channel)

        # tmp 영상 삭제 (프레임만 보존)
        video_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)

        log.info(f"[YouTube] {channel} | 등급:{grade} | 프레임:{n_frames}장 | {url}")
        return {"url": url, "channel": channel, "grade": grade, "frames": n_frames, "status": "ok"}

    # ── 6. 채널별 ThreadPool 병렬 수집 ──────────────────
    def _collect_channel(self, channel: str, channel_url: str):
        log.info(f"[YouTube] {channel} URL 목록 수집 중...")
        urls = self._fetch_clip_urls(channel_url)
        log.info(f"[YouTube] {channel} — {len(urls)}개 클립 발견")

        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._process_clip, url, channel): url for url in urls}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as e:
                    log.error(f"클립 처리 오류: {e}")

        ok    = sum(1 for r in results if r["status"] == "ok")
        total = sum(r.get("frames", 0) for r in results)
        log.info(f"[YouTube] {channel} 완료 — {ok}/{len(urls)} 클립, 총 {total}장")

    # ── 7. 전체 실행 ─────────────────────────────────────
    def run(self):
        # 채널별로도 병렬 실행
        with ThreadPoolExecutor(max_workers=min(len(CHANNELS), self.max_workers)) as pool:
            futures = {
                pool.submit(self._collect_channel, ch, url): ch
                for ch, url in CHANNELS.items()
            }
            for fut in as_completed(futures):
                ch = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    log.error(f"[YouTube] 채널 {ch} 실패: {e}", exc_info=True)
        log.info("[YouTube] 전체 채널 수집 완료")
