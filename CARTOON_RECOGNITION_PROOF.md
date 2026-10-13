# 动画片角色识别 — 已有实测结果

**目标仅有一个**：不训练模型，使用 Meta 预训练 DINOv2 ViT-S/14 比较《Big Buck Bunny》中同一只兔子 Buck 在不同场景、不同姿态的视觉特征，检查是否比另一只动画角色更相似。

代码：`scripts/verify_cartoon_character.py`。运行命令：`python -m scripts.verify_cartoon_character`（需 PyTorch、torchvision、Pillow、requests、numpy，联网下载模型和截图；依赖源站限流时需要稍后重试）。

## 本次扩展后的实际结果

**GitHub Actions [#38339829398](https://github.com/yituanxing/test3/actions/runs/38339829398)：PASS。**
代码提交 `832ca290`。

以 Buck 正面肖像为参考，DINOv2 **只接收人工裁好的 RGB 图像，不接收真实角色标签**，得到：

| 查询图像 | 正确答案 | 余弦相似度 | 排名 |
| --- | --- | ---: | ---: |
| Buck 在森林中的侧脸 | 同一角色 | **0.600106** | 1 |
| Buck 在树林中拉弓 | 同一角色 | **0.555034** | 2 |
| Buck 在草地上 | 同一角色 | **0.539010** | 3 |
| Buck 与小动物同框画面中裁剪出的**棕色小动物** | 不同角色 | **0.332137** | 4 |

三个不同姿态的 Buck 正例均排在不同角色之前，最难正例与负例的余弦分数差为 `0.206873`。相较于初版，这次新增了兔子拉弓动作，并把负例换成**同一画面中实际出现的另一只角色**，不再使用只有背景的错误裁剪。

**原始证据**： [CI 工件（result.json + evidence.jpg）](https://github.com/yituanxing/test3/actions/runs/38339829398/artifacts/11786473101)。图中能直接对照模型看到了哪只动物，也能排除用纯背景冒充不同角色的无效测试。

**数据来源与许可**：Wikimedia Commons 发布的 Blender Foundation《Big Buck Bunny》原片截图；© 2008 Blender Foundation / www.bigbuckbunny.org，CC BY 3.0。程序保存截图来源 URL，样例文件和模型首次联网获取；未训练或微调模型。

## 结论边界

**已证明**：使用现成视觉模型，Buck 的三张不同电影场景截图在这个固定测试集中，相比另一动画角色，有明显更高的视觉相似度。

**没有证明**：所有动画角色都能识别、跨视频自动检测角色、跟踪、跨真实摄像头身份判定，也没有给出真实业务可用的统一阈值。测试样本很少，棕色角色的负例与一张 Buck 正例出自**同一个电影画面**；因此不能把这次的 3/3 称为普遍准确率。

本阶段只做动画识别；不延伸到国标摄像头、人员底库、角色自动跟踪或业务预警。
