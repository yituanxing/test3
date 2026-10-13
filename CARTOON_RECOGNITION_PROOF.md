# 动画角色识别最小实验（只做这一件事）

**目标**：用现成预训练 DINOv2 ViT-S/14，判断《Big Buck Bunny》中同一只 Buck 兔子在不同电影截图里是不是同一角色；另用两只其他小动物作负例。**无需训练模型。**

运行脚本：`python -m scripts.verify_cartoon_character`（需安装 PyTorch CPU、torchvision、Pillow、requests、numpy，首次执行下载官方模型和影片截图）。

**验证条件**：单独选择一张 Buck 截图作为参考，模型不知道任何名字/标签；另外四张来自电影的不同截图，其中两张 Buck 和两张其他角色。根据实际模型余弦相似度排名，要求前两名均为 Buck。会输出原始余弦分数、排名和并排图片。若做不到，**如实失败，不自动调阈值或虚构成功**。

**当前范围**：只有裁剪好的动画人物图片跨画面匹配。不增加人脸检测、自动截图、追踪、摄像头、场所底库、GB28181 接入、模型训练。图片裁剪属于已知的目标选择，不能以本实验宣称“自动定位角色”已经实现。

图片来源：Wikimedia Commons 的《Big Buck Bunny》原片截图；© 2008 Blender Foundation / www.bigbuckbunny.org，CC BY 3.0（注明出处）。模型使用 Meta 官方预训练 DINOv2 ViT-S/14，未微调。CI 仅在本 PR 标题包含 `[cartoon]` 时使用现有工作流的独立 job 执行。
