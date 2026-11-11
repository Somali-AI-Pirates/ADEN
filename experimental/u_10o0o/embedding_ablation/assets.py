"""Download only pinned public model assets; never invokes a hosted model."""

import argparse

from huggingface_hub import snapshot_download

MODELS = {
    "snowflake": (
        "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
        "55ec6e9358a56d56af759bc8372e970caf8c305f",
    ),
    "kure_v3": ("nlpai-lab/KURE-v3", "73d5e710bc0b1f324a60746922cbad6d38f480d4"),
    "nemotron": (
        "nvidia/Nemotron-3-Embed-8B-BF16",
        "d1f2f25730bbd775b99b29185134bc86653bf2d1",
    ),
    "qwen3": ("Qwen/Qwen3-Embedding-8B", "1d8ad4ca9b3dd8059ad90a75d4983776a23d44af"),
}


def download(name):
    model_id, revision = MODELS[name]
    print(f"Downloading {name} at {revision}", flush=True)
    path = snapshot_download(
        model_id,
        revision=revision,
        allow_patterns=[
            "*.json",
            "*.safetensors",
            "*.txt",
            "*.model",
            "README.md",
            "LICENSE",
        ],
        ignore_patterns=["onnx/*", "openvino/*", "onnx*", "*.onnx", "*.bin"],
        max_workers=4,
    )
    print(f"Ready: {path}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs="+", choices=tuple(MODELS))
    for name in parser.parse_args().models:
        download(name)
