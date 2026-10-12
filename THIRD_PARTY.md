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
