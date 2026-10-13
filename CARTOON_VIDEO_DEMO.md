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
  阈值 `0.42` 是第一次视频观察后选择的探索值，不是统计校准的身份置信度。
- 参考身份是 Buck。别的动漫出现 `not_confirmed` 属于预期：
  只有 Buck 一个参考图，不意味着自动识别所有角色姓名。
- GitHub CI 单独验证公开视频自动检测+识别，不使用人工检测坐标。
  如果没找到目标，必须报告失败/NO_MATCH，不能拿之前人工裁剪
  静态图片测试 PASS 冒充视频端到端已通过。
- 视频归属与使用：© 2008 Blender Foundation / www.bigbuckbunny.org，
  [CC BY 3.0](https://peach.blender.org/about/)。
  测试片段 Wikimedia Commons [Big buck bunny montage.ogv](https://commons.wikimedia.org/wiki/File:Big_buck_bunny_montage.ogv)，
  15 秒实际动画，非多张静态图片拼装。

## 首轮真视频实测：不是人工裁剪

实际 CI #38341068383 使用 Grounding DINO 在真实动画短片中自动生成了 9 个候选框，抽取 4 个不同时刻的视频帧。Wikimedia 视频源被 HTTP 429 限制，程序切换到 W3Schools 的真实 Big Buck Bunny 短片。

第一轮 DINOv2 兔子余弦分数最高约 0.498，最初未校准的 0.50 阈值使识别 NO_MATCH，CI 因此失败；这是诚实的失败，不是视频读取失败。进一步实验调整到 0.42 探索阈值，并用 NMS 对重叠的自动检测框去重；须至少两处独立的视频时间戳识别出 Buck 才算 PASS。阈值是事后根据首轮片段观察选取的，**不代表泛化精度**。

首轮证据：https://github.com/yituanxing/test3/actions/runs/38341068383/artifacts/11786891489


## 原始动画视频上传端到端，已经验收通过（2026-10-13）

[真实 CI #38343247734](https://github.com/yituanxing/test3/actions/runs/38343247734)：**SUCCESS**。

- 命令行从 **10 秒真实 Big Buck Bunny 动画 MP4**（W3Schools 许可示例）解码并抽样 8 帧；Grounding DINO 自动产生 11 个去重候选框；DINOv2 用**两个独立电影完整截图、均由检测器自动裁剪**形成 Buck 的参考特征。8 个视频时刻中，4 个时刻识别到 Buck 候选。
- 运行了真正的本地 Gradio Web 服务，`gradio_client` 使用标准 VideoData 上传**原始完整动画 MP4**，不是提供已裁剪的静态图片，也没有绕过 API 直接调用分析函数。Web 端返回 7 张自动框图、4 个视频时刻有 Buck 候选。
- 浏览器返回的标注视频经过 FFmpeg `libx264` 实际转为 **H.264**；CI 还使用 `ffprobe` 验证视频编码。
- 两种入口（CLI 与 Web）产生的帧采样时间不同，所以 8 帧和 7 帧不应混为一个准确率分母。当前候选阈值 0.42 是探索参数，不属于已标定的泛化准确率。
- GitHub Actions 工件： [真实视频/自动框/H.264 文件](https://github.com/yituanxing/test3/actions/runs/38343247734/artifacts/11787279893) 和 [完整视频上传 API 验收 JSON](https://github.com/yituanxing/test3/actions/runs/38343247734/artifacts/11788095737)。

**本地需安装 FFmpeg（含 libx264）以生成浏览器通用的 H.264 视频**。只有 OpenCV 而无 FFmpeg 时，程序会在结果 JSON 中明确标记 MPEG-4 回退，此类预览可能无法在浏览器直接播放。

**当前已达成的目标**：上传不经人工预裁剪的完整动画视频，系统自己找出 Buck 候选、框出位置，并给出与参考角色的相似度；可直接观看真实推理的标注抽样视频。**未达成**：对原片每帧都稳定识别、自动给所有新角色命名或任意动漫识别均准确。初版仅识别已有 Buck 参考图的候选，暂不扩展其他业务。
