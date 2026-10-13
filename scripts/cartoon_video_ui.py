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

    if not video_filepath or not Path(video_filepath).is_file():
        raise gr.Error("请先上传一个完整动画视频")
    out = Path(tempfile.mkdtemp(prefix="zd-cartoon-video-"))
    try:
        result = run(Path(video_filepath), out)
    except Exception as exc:
        # No invented recognitions on missing boxes or unavailable model.
        raise gr.Error(f"分析失败：{type(exc).__name__}；参见本地 result.json") from exc
    annotated = out / "annotated_samples.mp4"
    gallery = sorted((out / "frames").glob("*.jpg"))
    # Gallery reveals exact decoder frames with model-created rectangles.
    return (str(annotated) if annotated.exists() else None,
            [str(p) for p in gallery],
            result)


def main():
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
        images = gr.Gallery(label="逐帧实际检测框", columns=3)
        report = gr.JSON(label="真实检测位置、分数及原始帧时间")
        button.click(fn=analyze_uploaded_video, inputs=video,
                     outputs=[preview, images, report])
    demo.launch(server_name="127.0.0.1", share=False)


if __name__ == "__main__":
    main()
