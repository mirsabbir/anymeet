"""Where local models run: the graphics card when PyTorch can use one (Apple GPU
through MPS, NVIDIA through CUDA), else the processor. ANYMEET_DEVICE=cpu, set by
the apps' "Run local models on" setting, keeps everything on the processor."""
import os


def torch_device() -> str:
    import torch

    if os.environ.get("ANYMEET_DEVICE", "gpu") != "cpu":
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    return "cpu"


def check() -> dict:
    """What `transcribe --device` prints: the device local models will use, and why
    the graphics card can't be used when it was asked for."""
    import torch

    device = torch_device()
    result = {"device": device, "torch": torch.__version__}
    if device == "cuda":
        result["name"] = torch.cuda.get_device_name(0)
    elif device == "cpu" and os.environ.get("ANYMEET_DEVICE", "gpu") != "cpu":
        result["problem"] = ("this PyTorch build has no CUDA support" if torch.version.cuda is None
                             else "PyTorch found no usable NVIDIA graphics card (check the driver)")
    return result
