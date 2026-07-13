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
        except: pass
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
        except: pass


def get_gpu_stats(state):
    try:
        out = subprocess.run(
            ["powershell", "-noprofile", "-command",
             "Get-CimInstance Win32_VideoController | "
             "Where-Object { $_.Name -notlike '*Virtual*' -and $_.Name -notlike '*Remote*' -and $_.Name -notlike '*Basic*' } | "
             "Select-Object -First 1 | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=5)
        name = out.stdout.strip()
        if not name:
            # fallback: first controller
            out = subprocess.run(
                ["powershell", "-noprofile", "-command",
                 "Get-CimInstance Win32_VideoController | Select-Object -First 1 | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5)
            name = out.stdout.strip()
        if name:
            state["system"]["gpu"] = name
    except: pass
