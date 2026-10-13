# 动画视频自动识别（仅做这个目标）

**使用效果**：上传/输入原始动画 `MP4 / OGV / MOV`，程序自行解码并抽样；
[Grounding DINO Tiny](https://huggingface.co/IDEA-Research/grounding-dino-tiny)
直接在**完整原始画面**中预测兔子/小动物的目标框并自动裁剪；
已有预训练 [DINOv2 ViT-S/14](https://github.com/facebookresearch/dinov2)
比较裁下的动物与 Buck 参考图的余弦相似度，输出每一帧自动框选结果、
每个候选的裁剪图、相似度和可播放的**抽样帧标注视频**。

没有训练模型，**没有手动指定视频里的任何角色矩形坐标**。
仅靠模型检测框通常不能做到像素级完美分割，如有必要可用
[Grounded SAM 2](https://github.com/IDEA-Research/Grounded-SAM-2) 的
现成 Grounding DINO + SAM 2 分割/视频追踪；第一版先看检测框是否够用，
不额外引入 SAM 2 的运行与维护开销。

## 命令行（独立验证）

```bash
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
python -m pip install -r requirements-cartoon-video.txt

# 自己的原始动画视频，不要求任何提前裁剪
python -m scripts.cartoon_video_recognize --video /path/to/cartoon.mp4

# 公共授权的《Big Buck Bunny》15s 原始活动画面演示
python -m scripts.cartoon_video_recognize --demo-video
```

运行结果位于 `reports/cartoon-video-proof/`：

- `result.json`：原视频时间戳、Grounding DINO **真实检测框**、检测置信度、
  DINOv2 **真实余弦相似度**，以及未找到候选时的 `NO_MATCH`。
- `frames/*.jpg`：带检测框的原视频抽样帧。
- `crops/*.jpg`：完全由检测框自动裁出的角色图。
- `annotated_samples.mp4`：抽样帧生成的视频，只为展示检测效果，
  **不是**原片等时长逐帧生成的视频。
- `reference_auto_crop.jpg`：参考图也由 Grounding DINO 自动裁剪，
  不用人工画框。

## 拖入视频的本地页面

```bash
python -m pip install 'gradio>=5,<7'
python -m scripts.cartoon_video_ui
```

打开 `http://127.0.0.1:7860`，上传动画视频，点击“开始识别”，
看带框抽样视频、逐帧证据图以及每个目标的真实相似度。
网站只绑定本地地址，不会上传视频到网络服务。
首轮运行需要联网下载两种官方预训练模型；两种推理均在本机 CPU 执行。

## 真值与验收边界

- **不是“完美识别”承诺**：开放词汇检测器可能漏掉画风复杂的角色；
  阈值 `0.50` 暂时只是人工检查用的探索值，不是统计校准的身份置信度。
- 参考身份是 Buck。别的动漫出现 `not_confirmed` 属于预期：
  只有 Buck 一个参考图，不意味着自动识别所有角色姓名。
- GitHub CI 单独验证公开视频自动检测+识别，不使用人工检测坐标。
  如果没找到目标，必须报告失败/NO_MATCH，不能拿之前人工裁剪
  静态图片测试 PASS 冒充视频端到端已通过。
- 视频归属与使用：© 2008 Blender Foundation / www.bigbuckbunny.org，
  [CC BY 3.0](https://peach.blender.org/about/)。
  测试片段 Wikimedia Commons [Big buck bunny montage.ogv](https://commons.wikimedia.org/wiki/File:Big_buck_bunny_montage.ogv)，
  15 秒实际动画，非多张静态图片拼装。
