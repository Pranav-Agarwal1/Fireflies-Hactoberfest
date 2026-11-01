"""Conservative model planning, with explicit overrides for offline/unknown hosts."""
import math
import platform
import subprocess
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Hardware:
    system: str
    machine: str
    ram_gb: float
    unified_memory: bool
    vram_gb: Optional[float] = None


def _output(args):
    return subprocess.check_output(args, text=True, timeout=10, stderr=subprocess.DEVNULL).strip()


def detect_hardware():
    try:
        return _detect_hardware()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ValueError("Hardware detection unavailable; supply --ram-gb and --unified (Mac), or --vram-gb (PC).") from exc


def _detect_hardware():
    system, machine = platform.system(), platform.machine()
    unified = system == "Darwin" and machine.lower() in ("arm64", "aarch64")
    if system == "Darwin":
        ram = int(_output(["sysctl", "-n", "hw.memsize"])) / 1024 ** 3
    elif system == "Linux":
        import os
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    elif system == "Windows":
        ram = int(_output(["powershell", "-NoProfile", "-Command",
                           "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])) / 1024 ** 3
    else:
        raise ValueError("Unknown hardware. Supply --ram-gb (and optionally --vram-gb).")
    vram = None
    if not unified:
        try:
            # Use one device: memory is not automatically pooled across GPUs.
            sizes = _output(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
            vram = max(float(x) / 1024 for x in sizes.splitlines())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return Hardware(system, machine, round(ram, 2), unified, vram)


def select_model(hw, override=None, context=None):
    if not math.isfinite(hw.ram_gb) or hw.ram_gb <= 0:
        raise ValueError("RAM must be a positive finite number.")
    if hw.vram_gb is not None and (not math.isfinite(hw.vram_gb) or hw.vram_gb <= 0):
        raise ValueError("VRAM must be a positive finite number.")
    # Budget includes model weights, bounded KV cache and working memory.
    budget = max(0, hw.ram_gb - 6) if hw.unified_memory or hw.vram_gb is None else min(hw.vram_gb * .85, hw.ram_gb - 4)
    if hw.unified_memory or hw.vram_gb is None:
        tier = "high" if hw.ram_gb >= 48 else "medium" if hw.ram_gb >= 24 else "low"
    else:
        tier = "high" if budget >= 26 else "medium" if budget >= 13 else "low"
    candidates = {
        "low": ("qwen2.5-coder:7b", 8192, 8),
        "medium": ("qwen2.5-coder:14b", 16384, 13),
        "high": ("qwen3-coder:30b", 32768, 26),
    }
    model, default_context, required = candidates[tier]
    if not hw.unified_memory and hw.vram_gb is not None and tier == "low":
        required = 6.5
    if budget < required:
        raise ValueError("Insufficient conservative memory budget for 7B; need ~8 GiB after reserves.")
    if override:
        if "cloud" in override.lower():
            raise ValueError("Cloud model tags are disabled; choose a local Ollama model.")
        model = override
    if context is not None and (context < 2048 or context > default_context):
        raise ValueError("Context must be between 2048 and the tier limit (%s)." % default_context)
    return {"hardware": asdict(hw), "tier": tier, "model": model,
            "context": context or default_context, "quantization": "operator_supplied" if override else "Q4_K_M",
            "budget_gb": round(budget, 2), "override": bool(override),
            "note": "Capacity estimate, not a benchmark. Close other memory-heavy apps. "
                    "Explicit model overrides require your own memory validation."}
