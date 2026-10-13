"""Local upload UI for the standalone cartoon VIDEO auto-detection proof.

Start (after requirements-cartoon-video.txt + CPU torch installed):
  pip install 'gradio>=5,<7'
  python -m scripts.cartoon_video_ui

Only a local 127.0.0.1 site; no account, database or remote uploads.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


def analyze_uploaded_video(video_filepath: str):
    import gradio as gr
    from scripts.cartoon_video_recognize import run
    from scripts.cartoon_video_track_existing import analyze, render_evidence

    if not video_filepath or not Path(video_filepath).is_file():
        raise gr.Error("请先上传一个完整动画视频")
    out = Path(tempfile.mkdtemp(prefix="zd-cartoon-video-"))
    try:
        result = run(Path(video_filepath), out)
        # ByteTrack groups already model-detected boxes across the uploaded
        # video. We never overwrite the raw DINOv2 similarities or verdicts.
        tracked = analyze(result)
        render_evidence(tracked, out)
        (out / "tracked_result.json").write_text(
            __import__("json").dumps(tracked, indent=2, ensure_ascii=False),
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
    annotated = out / "annotated_samples.mp4"
    gallery = sorted((out / "tracked_frames").glob("*.jpg"))
    # Gallery includes raw detector boxes plus purple temporal support boxes,
    # which never replace the original raw match classification.
    return (str(annotated) if annotated.exists() else None,
            [str(p) for p in gallery],
            result)


def build_demo():
    """The SAME genuine video-upload interface used by CI acceptance."""
    import gradio as gr
    with gr.Blocks(title="动画片：自动找角色并判断是否为 Buck") as demo:
        gr.Markdown(
            "## 上传完整动画视频 → 预训练模型自动找角色 → 判断是否与 Buck 相似\n"
            "采用 Grounding DINO + DINOv2；不训练，也不要求手动裁剪。"
            " 当前是实验：高相似度不代表可靠身份结论。"
        )
        video = gr.Video(label="选择动画视频（原始完整画面）", sources=["upload"])
        button = gr.Button("开始识别", variant="primary")
        preview = gr.Video(label="模型自动框选后的抽样视频（不是原片全帧渲染）")
        images = gr.Gallery(label="模型自动检测框（紫色为跨帧辅助判断）", columns=3)
        report = gr.JSON(label="真实检测位置、分数及原始帧时间")
        button.click(fn=analyze_uploaded_video, inputs=video,
                     outputs=[preview, images, report],
                     api_name="recognize_cartoon_video")
    return demo


def main():
    build_demo().launch(server_name="127.0.0.1", share=False)


if __name__ == "__main__":
    main()
