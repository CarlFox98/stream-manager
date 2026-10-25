"""CPU / RAM / GPU monitoring (psutil where available, PowerShell/CIM fallback)."""
import subprocess


def get_system_stats(state):
    try:
        import psutil
        state["system"]["cpu"] = round(psutil.cpu_percent(interval=0.5), 1)
        mem = psutil.virtual_memory()
        state["system"]["ram_pct"] = round(mem.percent, 1)
        state["system"]["ram_used_gb"] = round(mem.used / (1024**3), 1)
        state["system"]["ram_total_gb"] = round(mem.total / (1024**3), 1)
    except ImportError:
        # Fallback: PowerShell CIM/WMI
        try:
            out = subprocess.run(
                ["powershell", "-noprofile", "-command",
                 "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage"],
                capture_output=True, text=True, timeout=5)
            cpu_str = out.stdout.strip()
            if cpu_str.isdigit():
                state["system"]["cpu"] = int(cpu_str)
        except Exception: pass
        try:
            script = (
                "$os = Get-CimInstance Win32_OperatingSystem; "
                "$total = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1); "
                "$free  = [math]::Round($os.FreePhysicalMemory / 1MB, 1); "
                "$used  = $total - $free; "
                "$pct   = [math]::Round(($used / $total) * 100, 1); "
                "Write-Output \"$total|$used|$pct\""
            )
            out = subprocess.run(
                ["powershell", "-noprofile", "-command", script],
                capture_output=True, text=True, timeout=5)
            parts = out.stdout.strip().split("|")
            if len(parts) == 3:
                state["system"]["ram_total_gb"] = float(parts[0])
                state["system"]["ram_used_gb"] = float(parts[1])
                state["system"]["ram_pct"] = float(parts[2])
        except Exception: pass


import json as _json, re as _re

# Names that are integrated graphics. On a Ryzen 7000 desktop the iGPU is listed
# first as plain "AMD Radeon(TM) Graphics", so "first controller" showed that
# instead of the RX 7900 XTX actually doing the encoding.
_IGPU = _re.compile(r"^(amd radeon\(tm\) graphics|amd radeon graphics|intel\(r\) (uhd|hd|iris)|"
                    r"intel\(r\) graphics)", _re.I)
_SKIP = ("virtual", "remote", "basic", "parsec", "meta", "mirror")


def pick_gpu(adapters):
    """Choose the GPU to show from [{"Name": str, "AdapterRAM": int}, ...].

    Discrete beats integrated; then more reported VRAM wins. AdapterRAM is a
    32-bit field Windows caps near 4 GB, so it only breaks ties.
    """
    cands = [a for a in adapters if a.get("Name")
             and not any(w in a["Name"].lower() for w in _SKIP)] or [a for a in adapters if a.get("Name")]
    if not cands:
        return ""
    cands.sort(key=lambda a: (bool(_IGPU.match(a["Name"].strip())), -(a.get("AdapterRAM") or 0)))
    return cands[0]["Name"].strip()


def get_gpu_stats(state):
    try:
        out = subprocess.run(
            ["powershell", "-noprofile", "-command",
             "Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=5)
        raw = _json.loads(out.stdout.strip() or "[]")
        name = pick_gpu(raw if isinstance(raw, list) else [raw])
        if name:
            state["system"]["gpu"] = name
    except Exception: pass
