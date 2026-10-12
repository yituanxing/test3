"""Use upstream FFprobe, never parse video containers ourselves."""
import json
import subprocess


def probe_video(uri: str):
    try:
        completed = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration,format_name:stream=index,codec_type,codec_name,width,height,avg_frame_rate',
            '-of', 'json', '-rw_timeout', '5000000', uri
        ], capture_output=True, text=True, timeout=9, check=False)
    except FileNotFoundError as e:
        raise ValueError('未安装 FFmpeg/ffprobe。请先安装 FFmpeg。') from e
    except subprocess.TimeoutExpired as e:
        raise ValueError('媒体探测超时，检查视频源是否在线。') from e
    if completed.returncode != 0:
        raise ValueError((completed.stderr or '媒体格式无法识别').strip()[:400])
    obj = json.loads(completed.stdout)
    return {'format': obj.get('format', {}),
            'video_streams': [s for s in obj.get('streams', []) if s.get('codec_type') == 'video']}
