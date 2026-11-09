"""只在安装阶段联网下载；运行时语音转写使用本地模型。"""
from pathlib import Path
import hashlib
import os

BACKEND = Path(__file__).resolve().parents[1]
os.environ.setdefault("HF_HOME", str(BACKEND / "storage" / "huggingface"))
os.environ.setdefault("HF_XET_CACHE", str(BACKEND / "storage" / "huggingface" / "xet"))
from huggingface_hub import snapshot_download

MODEL_REPO = "Systran/faster-whisper-small"
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_SHA256 = "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"

if __name__ == "__main__":
    directory = BACKEND / "storage" / "models" / "faster-whisper-small"
    snapshot_download(MODEL_REPO, revision=MODEL_REVISION, local_dir=directory, token=False, max_workers=2,
                      allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.*", "preprocessor_config.json", "README.md"])
    with (directory / "model.bin").open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != MODEL_SHA256:
            raise RuntimeError("语音模型文件校验失败，请检查下载。")
    print("本机语音模型已就绪。")
