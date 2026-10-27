from pathlib import Path
import json
import os
import sys
import time


def main():
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if job.get("action") != "audio":
        raise ValueError("Clean Eureka worker accepts only final-audio jobs")
    config = job["runtime"]
    os.environ.update(
        CUDA_VISIBLE_DEVICES="",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    import torch

    torch.set_num_threads(8)
    sys.path.insert(0, config["audio_source_root"])
    import soundfile
    import torchaudio

    def load(path, *args, **kwargs):
        data, sample_rate = soundfile.read(path, dtype="float32", always_2d=True)
        return torch.from_numpy(data.T.copy()), sample_rate

    torchaudio.load = load
    from eureka_infer.api import EurekaAudio

    model = EurekaAudio(config["audio_model_dir"], device="cpu")
    for row in job["jobs"]:
        output = Path(row["output"])
        if output.exists():
            continue
        started = time.monotonic()
        response = model.generate(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "audio_url", "audio_url": {"url": row["audio"]}},
                        {"type": "text", "text": row["question"]},
                    ],
                }
            ],
            max_new_tokens=row.get("max_new_tokens", 240),
            do_sample=False,
        )
        temporary = output.with_suffix(".partial.json")
        temporary.write_text(
            json.dumps(
                {
                    "model": "Eureka-Audio-Instruct",
                    "device": "cpu",
                    "actual_audio": row["audio"],
                    "source_windows": row["source_windows"],
                    "scope": row.get(
                        "scope", "final delivered movie window; not human listening"
                    ),
                    "inference_seconds": round(time.monotonic() - started, 3),
                    "response": response,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
        print(row["id"], flush=True)


if __name__ == "__main__":
    main()
