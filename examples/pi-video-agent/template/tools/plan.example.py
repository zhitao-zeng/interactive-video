"""可选的镜头表→请求 JSON 构建器：复制、改 SHOTS、按需使用；它只写请求，不提交。

    python tools/plan.example.py c01 c02
    python tools/media.py requests/c01-v1.json

SD 的参考音频总时长超过 15.2 秒会在这里直接报错；H3 不受这条限制。派生的镜头表、参考图
和提示词仍由你决定，这个文件不是必须遵循的格式。
"""
import json
import subprocess
import sys
from pathlib import Path

SHOTS = [
    {
        "id": "s01a",
        "action": "video",                 # video | h3
        "endpoint": "pool",                # 仅 h3
        "seconds": 10,
        "output": "media/s01a-v1.mp4",
        "references": ["input/知遥-身份基准-v1.1.png", "input/知遥-综合视觉卡-v1.1.png"],
        "purposes": ["face identity", "body and clothing"],
        "audios": [],                      # video -> reference_audios; h3 -> audios
        "first_frame": None,               # 仅 video，且必须同时出现在 references 里
        "prompt": "…镜头、动作、运镜和原文台词…",
    },
]


def audio_seconds(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        text=True).strip()
    return float(out)


def build(shot, take="v1"):
    action = shot.get("action", "video")
    refs = [str(p) for p in shot.get("references", [])]
    audios = [str(p) for p in shot.get("audios", [])]
    if action == "video" and audios:
        total = sum(audio_seconds(p) for p in audios)
        if total > 15.2:
            raise SystemExit(f'{shot["id"]}: reference audio total {total:.2f}s > 15.2s; trim the clips')
    request = {"action": action, "prompt": shot["prompt"], "references": refs,
               "purposes": shot.get("purposes", []), "seconds": shot["seconds"], "output": shot["output"]}
    if action == "h3":
        request["endpoint"] = shot.get("endpoint", "pool")
        request["ratio"] = shot.get("ratio", "9:16")
        if audios:
            request["audios"] = audios
    else:
        if audios:
            request["reference_audios"] = audios
    if shot.get("first_frame"):
        request["first_frame"] = shot["first_frame"]
    path = Path("requests") / f'{shot["id"]}-{take}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    return path


if __name__ == "__main__":
    wanted = set(sys.argv[1:])
    for shot in SHOTS:
        if not wanted or shot["id"] in wanted:
            print(build(shot))
