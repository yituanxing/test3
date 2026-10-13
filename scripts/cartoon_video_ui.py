"""Local upload UI for the standalone cartoon VIDEO auto-detection proof.

Start (after requirements-cartoon-video.txt + CPU torch installed):
  pip install 'gradio>=5,<7'
  python -m scripts.cartoon_video_ui

Only a local 127.0.0.1 site; no account, database or remote uploads.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def analyze_uploaded_video(video_filepath: str):
    import gradio as gr
    from scripts.cartoon_video_recognize import ROOT, run
    from scripts.cartoon_video_track_existing import analyze, render_evidence
    from scripts.cartoon_consensus_match import evaluate_consensus
    from scripts.cartoon_consensus_render import render_consensus_preview

    if not video_filepath or not Path(video_filepath).is_file():
        raise gr.Error("请先上传一个完整动画视频")
    out = Path(tempfile.mkdtemp(prefix="zd-cartoon-video-"))
    try:
        fixture = ROOT / "reports/cartoon-reference-fixtures"
        frozen_refs = [fixture / "reference_auto_crop.jpg",
                       fixture / "reference_auto_crop_buck_bow.jpg"]
        result = run(Path(video_filepath), out,
                     reference_crops=frozen_refs if all(x.is_file() for x in frozen_refs)
                     else None)
        # This is an extra scoring rule on the SAME original model output.
        # Preserve original per-reference scores and raw decisions for audit.
        consensus = evaluate_consensus(result)
        (out / "consensus_result.json").write_text(
            json.dumps(consensus, indent=2, ensure_ascii=False),
            encoding="utf-8")
        preview_path = render_consensus_preview(out, consensus)
        result["two_pose_consensus"] = {
            "rule": consensus["method"],
            "status": "EXPERIMENTAL_NOT_CALIBRATED",
            "cutoff_unchanged": consensus["model_cutoff_unmodified"],
            "raw_match_timestamps": consensus["raw_positive_timestamps"],
            "consensus_match_timestamps": consensus["consensus_positive_timestamps"],
            "original_positive_frames_rejected":
                consensus["original_positive_frames_rejected_by_consensus"],
            "first_unseen_video_audit": "PR4: 5m30-5m55 fixed-source A/B; evidence in CI",
            "warning": "No thresholds tuned on this user's video; not proof of identity.",
            "frames": consensus["frames"],
        }
        # ByteTrack groups already model-detected boxes across the uploaded
        # video. We never overwrite the raw DINOv2 similarities or verdicts.
        tracked = analyze(result)
        render_evidence(tracked, out)
        (out / "tracked_result.json").write_text(
            json.dumps(tracked, indent=2, ensure_ascii=False),
            encoding="utf-8")
        result["temporal_consistency"] = {
            "tracker": tracked["tracker"],
            "raw_matching_timestamps": tracked["raw_matching_timestamps"],
            "temporally_supported_timestamps":
                tracked["temporally_supported_timestamps"],
            "extra_timestamps_recovered":
                tracked["additional_temporally_supported_timestamps"],
            "weak_observations_recovered":
                tracked["weak_dino_observations_recovered"],
            "caution": "track evidence, not an independently verified identity",
        }
    except Exception as exc:
        # No invented recognitions on missing boxes or unavailable model.
        raise gr.Error(f"分析失败：{type(exc).__name__}；参见本地 result.json") from exc
    gallery = sorted((out / "consensus_frames").glob("*.jpg"))
    # Consensus frames keep the original model boxes. Magenta marks rejected
    # MAX-only matches, while raw ByteTrack remains visible only in JSON
    # diagnostics and never silently promotes a consensus rejection.
    return (str(preview_path) if preview_path.exists() else None,
            [str(p) for p in gallery],
            result)


def build_demo():
    """The SAME genuine video-upload interface used by CI acceptance."""
    import gradio as gr
    with gr.Blocks(title="动画片：自动找角色并判断是否为 Buck") as demo:
        gr.Markdown(
            "## 上传完整动画视频 → 预训练模型自动找角色 → 判断是否与 Buck 相似\n"
            "采用 Grounding DINO + DINOv2；不训练，也不要求手动裁剪。"
            " 默认展示两张 Buck 参考特征的谐均值共识；粉色框表示原先取最大分数会命中、共识方案予以拒绝。"
            " 原始分数和判定仍保留在 JSON，未在上传视频上调整阈值。"
        
        )
        video = gr.Video(label="选择动画视频（原始完整画面）", sources=["upload"])
        button = gr.Button("开始识别", variant="primary")
        preview = gr.Video(label="双参考共识结果（紫红框：原模型误匹配候选被拒绝；抽样视频）")
        images = gr.Gallery(label="实际抽样帧：原始模型框 + 新共识拒绝框", columns=3)
        report = gr.JSON(label="真实检测位置、分数及原始帧时间")
        button.click(fn=analyze_uploaded_video, inputs=video,
                     outputs=[preview, images, report],
                     api_name="recognize_cartoon_video")
    return demo


def main():
    build_demo().launch(server_name="127.0.0.1", share=False)


if __name__ == "__main__":
    main()
