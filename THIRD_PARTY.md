# Third-party dependencies and responsibility boundaries

- **MediaMTX** (`bluenviron/mediamtx`) — upstream container, RTSP relay; MIT. We have supplied a Compose recipe, not vendored its source or tested the Docker image in this build environment. https://github.com/bluenviron/mediamtx
- **FFmpeg/FFprobe** — system-installed binaries; video processing and probing. Licensing depends on build configuration; review packaging and redistribution before distribution. https://ffmpeg.org/
- **OpenCV** — installed as `opencv-python-headless`, decoding interface and Haar fallback. Model and library license attribution supplied by package. https://opencv.org/
- **YuNet 2023mar** — OpenCV Zoo model adapter and opt-in installer; model weights not bundled. The official model directory declares MIT. https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
- **WVP + ZLMediaKit** — considered for the dedicated GB28181 phase, deliberately not added to the base bundle. https://github.com/648540858/wvp-GB28181-pro and https://github.com/ZLMediaKit/ZLMediaKit

The host operator is responsible for upstream license notices, consent for any recorded persons, local-access restrictions, and checking versions and security patches before non-demo deployment.

## V0.7 离线测试素材

- `samples/astronaut-public-domain.png`：来自 scikit-image `skimage.data.astronaut()`，原始图片属于 NASA Great Images 项目，scikit-image 文档注明无已知版权限制、公共领域；原图为宇航员 Eileen Collins，供**人脸检测测试**而非身份识别使用。
- `samples/astronaut-face-test.mp4`：用 FFmpeg 从上述图片生成的静态图像循环 H.264 视频；不属于人员监控录像。

## GB28181 协议栈复用（上游依赖，不将源码混入 test3）

- WVP-GB28181-PRO，https://github.com/648540858/wvp-GB28181-pro ，GitHub 仓库标记 MIT，自维护 SIP 注册、Catalog、Play、Playback；在上游独立启动，不把服务内置代码复制进 test3。test3 的 `app/gb28181.py` 只使用其公开 API。
- ZLMediaKit，https://github.com/ZLMediaKit/ZLMediaKit ，MIT 类许可证及额外保留软件标识要求；它负责 GB28181 的 RTP PS/TS 收流与 RTSP 分发。官方项目建议商用前另行确认第三方库依赖许可证。
- 不提交国标设备密码、SIP 登录信息、WVP API Key、生产相机录像或人脸特征。GitHub CI 的 RTSP 公共样例不构成对真实 GB SIP 设备的协议一致性认证。
