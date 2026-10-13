"""Actual local Gradio upload API -> frozen pretrained cartoon video pipeline.

Deliberately sends the FULL moving movie to the real UI endpoint using
Gradio's own client upload protocol. No monkeypatch, no artificial detections.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from gradio_client import Client, handle_file

from scripts.cartoon_video_recognize import OUT_DEFAULT, ROOT
from scripts.cartoon_video_ui import build_demo


def main() -> None:
    candidates = [
        f for f in OUT_DEFAULT.glob("big_buck_bunny_montage.*")
        if f.suffix.lower() in {".mp4", ".ogv"} and f.stat().st_size > 100_000
    ]
    if len(candidates) != 1:
        raise RuntimeError("Previous REAL video test must leave one complete film clip")
    actual_video = candidates[0]
    web = build_demo()
    report_dir = ROOT / "reports/cartoon-video-upload"
    report_dir.mkdir(parents=True, exist_ok=True)
    try:
        web.launch(server_name="127.0.0.1", server_port=7873,
                   share=False, prevent_thread_lock=True, quiet=True,
                   show_error=True)
        client = Client("http://127.0.0.1:7873", verbose=False)
        # Gradio 5's VideoData payload has a nested 'video' FileData field;
        # a bare file object passes FileData validation but fails VideoData.
        response = client.predict(
            {"video": handle_file(str(actual_video.resolve()))},
            api_name="/recognize_cartoon_video")
        if not isinstance(response, (tuple, list)) or len(response) != 3:
            raise AssertionError("Gradio UI did not return video, gallery, report")
        preview, gallery, result = response
        if not isinstance(result, dict):
            raise AssertionError("UI did not return the actual inference report")
        if result.get("status") != "PASS":
            raise AssertionError("Actual uploaded video failed recognition")
        if result.get("frames_decoded", 0) < 4:
            raise AssertionError("Uploaded file was not genuinely decoded")
        if result.get("distinct_timestamps_with_buck", 0) < 2:
            raise AssertionError("No evidence of matches at different video times")
        if not gallery:
            raise AssertionError("Video uploaded but UI produced no visible frame gallery")
        if not preview:
            raise AssertionError("UI did not produce a playable annotated preview")
        if result.get("preview_codec") != "h264_browser_compatible":
            raise AssertionError("Video browser preview was not encoded H.264")
        report = {
            "status": "PASS",
            "submitted_media": actual_video.name,
            "actual_gradio_endpoint": "/recognize_cartoon_video",
            "full_video_uploaded": True,
            "frames_decoded": result["frames_decoded"],
            "distinct_matching_timestamps": result["distinct_timestamps_with_buck"],
            "preview_codec": result["preview_codec"],
            "gallery_items": len(gallery),
        }
        (report_dir / "result.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        web.close()


if __name__ == "__main__":
    main()
