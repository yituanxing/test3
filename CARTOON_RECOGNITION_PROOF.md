# 动画角色识别最小实验（只做这一件事）

**目标**：用现成预训练 DINOv2 ViT-S/14，判断《Big Buck Bunny》中同一只 Buck 兔子在不同电影截图里是不是同一角色；另用两只其他小动物作负例。**无需训练模型。**

运行脚本：`python -m scripts.verify_cartoon_character`（需安装 PyTorch CPU、torchvision、Pillow、requests、numpy，首次执行下载官方模型和影片截图）。

**验证条件**：单独选择一张 Buck 截图作为参考，模型不知道任何名字/标签；另外三张来自电影的不同截图，其中两张 Buck 和一张其他角色（Ricky）。根据实际模型余弦相似度排名，要求前两名均为 Buck。会输出原始余弦分数、排名和并排图片。若做不到，**如实失败，不自动调阈值或虚构成功**。

**当前范围**：只有裁剪好的动画人物图片跨画面匹配。不增加人脸检测、自动截图、追踪、摄像头、场所底库、GB28181 接入、模型训练。图片裁剪属于已知的目标选择，不能以本实验宣称“自动定位角色”已经实现。

图片来源：Wikimedia Commons 的《Big Buck Bunny》原片截图；© 2008 Blender Foundation / www.bigbuckbunny.org，CC BY 3.0（注明出处）。模型使用 Meta 官方预训练 DINOv2 ViT-S/14，未微调。CI 仅在本 PR 标题包含 `[cartoon]` 时使用现有工作流的独立 job 执行。

## 已实测结果（GitHub Actions）

验证运行：[CI #38338156836](https://github.com/yituanxing/test3/actions/runs/38338156836)，结果 **PASS**，代码提交 `65a9269`。

参考图为 Buck 正面肖像，查询图像由模型实际加载并编码，无训练或标注答案输入：

| 查询截图 | 事先真实角色 | 余弦相似度 | 排名 |
| --- | --- | ---: | ---: |
| Buck 与另一动物同框的侧脸（仅裁剪 Buck） | 同一只 Buck | 0.600106 | 1 |
| Buck 草地场景 | 同一只 Buck | 0.539010 | 2 |
| Ricky 小动物 | 另一角色 | 0.434082 | 3 |

正例最低与负例分数差：`0.104928`。测试要求的前两名均为 Buck，**实测通过**。
并排可视化证据及机器可读 JSON 可从上述 CI 工件 `cartoon-pretrained-recognition-evidence` 下载。

这只证明 **一只动画角色在三张不同电影截图间的视觉相似性排序成功**；
并不证明任意角色、自动角色定位、电影视频跟踪、真人脸部身份识别、或跨摄像头已上线。
