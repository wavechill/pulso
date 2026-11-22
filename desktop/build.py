"""Build the Windows x64 portable folder from pinned upstream dependencies.

Run on Windows with 64-bit Python 3.14 and pip:
    python desktop/build.py NEW_DESTINATION BUILD_CACHE
"""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
WHEELS = (
    "numpy==2.5.4", "onnxruntime-directml==1.24.4", "yt-dlp==2026.10.26",
    "yt-dlp-ejs==0.8.0", "brotli==1.2.1", "certifi==2026.10.23",
    "charset-normalizer==3.5.2", "flatbuffers==25.12.19", "idna==3.20",
    "mutagen==1.48.1", "packaging==26.3", "protobuf==7.36.2",
    "pycryptodomex==3.24.2", "requests==2.34.2", "urllib3==2.8.0",
    "websockets==17.4",
)
PYTHON = (
    "https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip",
    "d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15",
)
WEBVIEW = (
    "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.4258.31/microsoft.web.webview2.1.0.4258.31.nupkg",
    "56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9",
)
FIXED_VERSION = "158.0.4370.33"
FIXED_DIRECTORY = f"Microsoft.WebView2.FixedVersionRuntime.{FIXED_VERSION}.x64"
FIXED_WEBVIEW = (
    "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
    f"ecc2761e-1380-46ee-9552-995b039e4e47/{FIXED_DIRECTORY}.cab",
    "835da387df67797f710881f553bb6934d98f24d08e370456f48d38ed4e072b62",
)
FFMPEG = (
    "https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-2026-11-20-12-40/"
    "ffmpeg-n8.1.3-39-g660b5aa7db-win64-lgpl-shared-8.1.zip",
    "bf6109642e7da71905cadcb0b8dbab8c2a0a72f8a1a4c88c2dbcda771aa358cd",
)
DENO = (
    "https://github.com/denoland/deno/releases/download/v2.9.7/deno-x86_64-pc-windows-msvc.zip",
    "a0c3101b4158d1dfb7d6a78a7bf0f3de80c96bb423c152beec8beb22786f2238",
)


def download_file(target, url, expected):
    def verified(file):
        if not file.is_file():
            return False
        with file.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == expected

    if not verified(target):
        draft = target.with_suffix(".part")
        try:
            with urlopen(url, timeout=120) as response, draft.open("wb") as file:
                shutil.copyfileobj(response, file)
            if not verified(draft):
                raise RuntimeError(f"Checksum incorreto em {target.name}")
            draft.replace(target)
        finally:
            draft.unlink(missing_ok=True)
    return target


def download(target, url, expected):
    return ZipFile(download_file(target, url, expected))


def bundle_webview(runtime, downloads):
    cab = download_file(downloads / f"webview2-fixed-{FIXED_VERSION}-x64.cab", *FIXED_WEBVIEW)
    runtime = runtime.resolve()
    package = runtime / FIXED_DIRECTORY
    destination = runtime / "webview2"
    if destination.exists() or package.exists():
        raise FileExistsError("O destino do WebView2 precisa estar livre.")
    subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/expand.exe"),
                    str(cab.resolve()), "-F:*", str(runtime)], check=True, stdout=subprocess.DEVNULL)
    if package.resolve().parent != runtime or not (package / "msedgewebview2.exe").is_file():
        raise RuntimeError("O pacote da Microsoft não contém a distribuição esperada.")
    package.rename(destination)


def copy_member(archive, name, output):
    with archive.open(name) as source, output.open("wb") as dest:
        shutil.copyfileobj(source, dest)


def build(output, work):
    output.mkdir(parents=True, exist_ok=False)
    downloads = work / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    python = output / "runtime" / "python"
    tools = output / "runtime" / "media"
    notices = output / "Licenças"
    app = output / "app"
    for directory in (python, tools, notices, app):
        directory.mkdir(parents=True)
    print("Empacotando Python e a análise local.", flush=True)
    with download(downloads / "python-3.14.7-embed-amd64.zip", *PYTHON) as archive:
        archive.extractall(python)
    (python / "python314._pth").write_text("python314.zip\n.\npackages\n..\\..\\app\n", encoding="ascii")
    wheels = work / "wheels"
    wheels.mkdir(exist_ok=True)
    install = [sys.executable, "-m", "pip", "--disable-pip-version-check"]
    subprocess.run(
        install + ["download", "--only-binary=:all:", "--dest", str(wheels), *WHEELS],
        check=True,
    )
    subprocess.run(
        install + ["install", "--no-index", "--find-links", str(wheels),
                   "--no-compile", "--target", str(python / "packages"), *WHEELS],
        check=True,
    )
    print("Empacotando as ferramentas de áudio e a janela do jogo.", flush=True)
    with download(downloads / "ffmpeg.zip", *FFMPEG) as archive:
        for member in archive.namelist():
            name = Path(member).name
            if "/bin/" in member and (name.endswith(".dll") or name in ("ffmpeg.exe", "ffprobe.exe")):
                copy_member(archive, member, tools / name)
            if name == "LICENSE.txt":
                copy_member(archive, member, notices / "FFmpeg.txt")
    with download(downloads / "deno.zip", *DENO) as archive:
        copy_member(archive, "deno.exe", tools / "deno.exe")
    with urlopen("https://raw.githubusercontent.com/denoland/deno/v2.9.7/LICENSE.md", timeout=30) as remote:
        (notices / "Deno.txt").write_bytes(remote.read())
    with download(downloads / "webview2.zip", *WEBVIEW) as archive:
        for name in ("Core", "WinForms"):
            dll = f"Microsoft.Web.WebView2.{name}.dll"
            copy_member(archive, "lib/net462/" + dll, output / dll)
        copy_member(archive, "runtimes/win-x64/native/WebView2Loader.dll", output / "WebView2Loader.dll")
        for name in ("LICENSE", "NOTICE"):
            copy_member(archive, name + ".txt", notices / f"WebView2-{name}.txt")
    bundle_webview(output / "runtime", downloads)
    for name in ("web", "demo", "models", "arrangements"):
        shutil.copytree(ROOT / name, app / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("server.py", "importer.py", "music_folder.py", "analysis_engine.py", "instrumental.py", "performances.py", "reductions.py", "song_phrases.py", "vocal_pitch.py", "audio_formats.py"):
        shutil.copy2(ROOT / name, app / name)
    shutil.copy2(ROOT / "LEIA-ME.txt", output)
    shutil.copy2(ROOT / "desktop" / "ORIGENS.txt", notices)
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run(
        [str(framework), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
         "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll", "/r:System.Net.Http.dll",
         "/r:" + str(output / "Microsoft.Web.WebView2.Core.dll"),
         "/r:" + str(output / "Microsoft.Web.WebView2.WinForms.dll"),
         "/win32manifest:" + str(ROOT / "desktop" / "app.manifest"),
         "/win32icon:" + str(ROOT / "desktop" / "Pulso.ico"),
         "/out:" + str(output / "Pulso.exe"), str(ROOT / "desktop" / "Pulso.cs"),
         str(ROOT / "desktop" / "WindowFrame.cs")],
        check=True,
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.platform != "win32" or sys.version_info[:2] != (3, 14):
        raise SystemExit(__doc__)
    build(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
