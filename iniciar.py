"""Iniciador Windows. Reaproveita o Python e as ferramentas do Frame Scale."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
ADDRESS = "http://localhost:8765"
def main():
    try:
        with urlopen(ADDRESS + "/api/state", timeout=1.5) as response:
            state = json.load(response)
        if "songs" in state and "lanUrl" in state:
            webbrowser.open(ADDRESS)
            return 0
    except (OSError, ValueError):
        pass
    if sys.version_info < (3, 10):
        print("Instale o Python 3.10 ou superior de 64 bits para abrir o Pulso.")
        return 1
    from importer import _tool
    python = sys.executable
    env = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "Pulso" / "python-env"
    isolated = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    need_ytdlp = not _tool("yt-dlp")
    missing = any(not importlib.util.find_spec(p) for p in ("numpy", "onnxruntime"))
    if missing or (need_ytdlp and not importlib.util.find_spec("yt_dlp")):
        if not isolated.exists():
            subprocess.check_call([python, "-m", "venv", str(env)])
        python = str(isolated)
        required = ["numpy", "onnxruntime"]
        if need_ytdlp:
            required.append("yt-dlp[default]")
        check = "import numpy, onnxruntime" + (", yt_dlp" if need_ytdlp else "")
        if subprocess.run([python, "-c", check], capture_output=True).returncode:
            print("Preparando as dependências no computador. O primeiro início requer internet.", flush=True)
            subprocess.check_call([python, "-m", "pip", "install", *required])
    if not _tool("ffmpeg"):
        print("Para importar músicas novas, instale o FFmpeg e o ffprobe no PATH. As músicas originais já estão prontas.")
    return subprocess.call([python, "-B", str(ROOT / "server.py"), *sys.argv[1:]], cwd=ROOT)

if __name__ == "__main__":
    raise SystemExit(main())
