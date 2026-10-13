"""Display true video frames with the NEW two-pose consensus clearly overlaid.

Grounding DINO boxes and old DINOv2 scores remain inspectable in source JSON.
Pink borders mark old MAX-only Buck candidates REJECTED by consensus; other
boxes remain unchanged. Preview uses ORIGINAL sampled frames, not fabricated
synthetic or slideshow identity data.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path
import cv2


def render_consensus_preview(source: Path, result: dict) -> Path:
    frames=source/"consensus_frames"
    frames.mkdir(parents=True,exist_ok=True)
    raw_video=source/"consensus_samples_raw.mp4"
    final_video=source/"consensus_samples.mp4"
    writer=None
    try:
        for sample in result["frames"]:
            t=float(sample["second"])
            image=cv2.imread(str(source/"frames"/f"t{t:.2f}.jpg"))
            if image is None:
                raise RuntimeError(f"Real decoded frame not found at t={t:.2f}")
            for row in sample["proposals"]:
                if not (row["raw_buck_candidate"] and
                        not row["consensus_buck_candidate"]):
                    continue
                x0,y0,x1,y1=row["box_xyxy"]
                cv2.rectangle(image,(x0,y0),(x1,y1),(255,0,255),4)
                cv2.putText(
                    image,f"MAX ONLY, harmonic={row['consensus_score']:.3f}",
                    (x0,max(15,y0-10)),cv2.FONT_HERSHEY_SIMPLEX,
                    .45,(255,0,255),2)
            is_buck=bool(sample["consensus_predicted_buck"])
            cv2.putText(
                image,
                f"t={t:.1f}s  two-pose: {'BUCK' if is_buck else 'NOT CONFIRMED'}",
                (8,max(22,image.shape[0]-14)),
                cv2.FONT_HERSHEY_SIMPLEX,.52,(255,255,255),2)
            cv2.imwrite(str(frames/f"t{t:.2f}.jpg"),image)
            if writer is None:
                writer=cv2.VideoWriter(str(raw_video),
                    cv2.VideoWriter_fourcc(*"mp4v"),2.0,
                    (image.shape[1],image.shape[0]))
                if not writer.isOpened():
                    raise RuntimeError("Cannot create new consensus preview")
            writer.write(image)
    finally:
        if writer is not None:
            writer.release()
    if not raw_video.exists():
        raise RuntimeError("No sampled frames to render")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Need ffmpeg with libx264 for playable H264 preview")
    subprocess.run([
        "ffmpeg","-hide_banner","-loglevel","error","-nostdin","-y",
        "-i",str(raw_video),"-an","-c:v","libx264",
        "-pix_fmt","yuv420p","-movflags","+faststart",str(final_video)
    ],capture_output=True,check=True,timeout=90)
    raw_video.unlink(missing_ok=True)
    return final_video
