"""Import one YouTube track using the same tools as Frame Scale."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from typing import Callable
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
import uuid


class ImportCancelled(Exception):
    pass


def check_cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ImportCancelled


def stop_download(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=4, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _tool(name: str) -> str | None:
    tools_dir = os.getenv("PULSO_TOOLS_DIR")
    if tools_dir:
        bundled = Path(tools_dir) / (name + ".exe")
        if bundled.is_file():
            return str(bundled)
    local = os.getenv("LOCALAPPDATA")
    if local:
        bundled = Path(local) / "Programs/FrameScale/bin/runtime/bin" / (name + ".exe")
        if bundled.is_file():
            return str(bundled)
    return shutil.which(name)


def find_ffmpeg() -> str:
    path = _tool("ffmpeg")
    if not path:
        raise RuntimeError("Instale o FFmpeg com ffprobe ou o runtime do Frame Scale para importar áudio.")
    return path


def _downloader() -> list[str]:
    if importlib.util.find_spec("yt_dlp"):
        return [sys.executable, "-m", "yt_dlp"]
    installed = _tool("yt-dlp")
    if installed:
        return [installed]
    raise RuntimeError('Instale yt-dlp[default] no Python do jogo ou instale o Frame Scale para importar links.')


def _youtube_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
    if parts.scheme not in {"http", "https"} or parts.hostname not in hosts:
        raise ValueError("Cole o link de um vídeo ou de uma playlist do YouTube.")
    params = parse_qs(parts.query)
    path = parts.path.strip("/").split("/")
    video = None
    if parts.hostname == "youtu.be":
        video = path[0]
    elif path == ["watch"]:
        video = params.get("v", [None])[0]
    elif len(path) == 2 and path[0] in {"embed", "shorts", "live"}:
        video = path[1]
    if video and re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        return "https://www.youtube.com/watch?v=" + video
    playlist = params.get("list", [""])[0]
    if path == ["playlist"] and re.fullmatch(r"[A-Za-z0-9_-]{2,128}", playlist):
        return "https://www.youtube.com/playlist?list=" + playlist
    raise ValueError("O endereço precisa identificar um vídeo ou uma playlist do YouTube.")



def youtube_video_id(url: str) -> str:
    canonical = _youtube_url(url)
    return parse_qs(urlsplit(canonical).query).get("v", [""])[0]


def browse_playlist(url: str) -> dict:
    """Read public track metadata without fetching audio or analyzing tracks."""
    canonical = _youtube_url(url)
    if urlsplit(canonical).path != "/playlist":
        raise ValueError("Cole um endereço de playlist do YouTube.")
    command = _downloader() + [
        "--ignore-config", "--no-plugin-dirs", "--no-remote-components", "--no-update",
        "--flat-playlist", "--dump-single-json", "--skip-download", "--no-warnings",
        "--socket-timeout", "15", "--extractor-retries", "1", "--encoding", "utf-8",
        "--", canonical,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=90,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ValueError("O YouTube demorou para abrir a playlist. Tente de novo.") from error
    if result.returncode:
        raise ValueError(_message(result.stderr))
    playlist = json.loads(result.stdout)
    songs = []
    seen = set()
    for entry in playlist.get("entries", []):
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id", "")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen
                or entry.get("availability") in {"private", "premium_only", "subscriber_only"}
                or entry.get("title") in {"[Private video]", "[Deleted video]"}):
            continue
        seen.add(video_id)
        songs.append({"videoId": video_id, "title": entry.get("title") or video_id,
                      "artist": entry.get("channel") or entry.get("uploader") or "",
                      "duration": entry.get("duration") or 0})
    if not songs:
        raise ValueError("A playlist não tem faixas públicas disponíveis.")
    return {"title": playlist.get("title") or "Playlist", "tracks": songs}


def save_cover(video_id: str, target: Path) -> bool:
    """A capa é opcional. Uma falha de imagem não interrompe o áudio."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return False
    for size in ("maxresdefault", "hqdefault"):
        try:
            with urlopen(f"https://i.ytimg.com/vi/{video_id}/{size}.jpg", timeout=5) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if raw.startswith(b"\xff\xd8") and len(raw) <= 4 * 1024 * 1024:
                target.write_bytes(raw)
                return True
        except OSError:
            continue
    return False

def _message(detail: str) -> str:
    detail = detail.lower()
    if any(word in detail for word in ("private", "sign in", "login", "members", "age-restricted")):
        return "O YouTube pediu autenticação para esta faixa. Você pode importar o arquivo de áudio."
    if any(word in detail for word in ("403", "429", "unavailable", "geo-restricted", "copyright")):
        return "O YouTube restringiu o acesso a esta faixa. Tente outro link ou importe o arquivo de áudio."
    if "requested format" in detail or "signature" in detail:
        return "O YouTube não forneceu um formato de áudio disponível. Atualize o yt-dlp ou escolha um arquivo."
    if any(word in detail for word in ("timeout", "timed out", "connect", "resolve", "network")):
        return "Falha de conexão ao baixar a música. Verifique a rede e tente novamente."
    return "O download do áudio falhou. Confira o link, atualize o yt-dlp ou importe a música como arquivo."


def import_youtube(
    url: str, dest_dir: Path, progress: Callable[[str], None], cancel=None
) -> tuple[Path, str, str]:
    """Download a video and save its decoded audio as a PCM WAV."""
    url = _youtube_url(url)
    command = _downloader()
    ffmpeg = find_ffmpeg()
    deno = _tool("deno")
    node = _tool("node")
    runtime = ["--js-runtimes", "deno:" + deno] if deno else []
    if not deno and node:
        runtime = ["--js-runtimes", "node:" + node]
    dest_dir = Path(dest_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    progress("Consultando o YouTube…")
    with tempfile.TemporaryDirectory(prefix="youtube-", dir=dest_dir) as scratch:
        args = command + [
            "--ignore-config", "--no-plugin-dirs", "--no-remote-components", "--no-update",
            "--no-playlist", "--playlist-items", "1", "--no-simulate",
            "--match-filters", "!is_live & !is_upcoming",
            "--socket-timeout", "20", "--retries", "2", "--extractor-retries", "2",
            "--abort-on-unavailable-fragments", "--no-colors", "--encoding", "utf-8",
            "--ffmpeg-location", str(Path(ffmpeg).parent), *runtime,
            "--format", "ba/b", "--extract-audio", "--audio-format", "wav",
            "--paths", scratch, "--output", "musica.%(ext)s",
            "--newline", "--progress", "--progress-delta", "1",
            "--progress-template", "download:PULSO_DOWNLOAD:%(progress._percent_str)s",
            "--print", "post_process:PULSO_PROCESS:%(id)s",
            "--print", "after_move:PULSO_META:%(.{id,title,track,artist,creator,uploader,channel})j",
            "--", url,
        ]
        metadata = {}
        diagnostic = ""
        with subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=os.name != "nt",
        ) as process:
            done = threading.Event()

            def watch_cancel():
                while not done.is_set():
                    if cancel.wait(0.12):
                        stop_download(process)
                        return

            watcher = None
            if cancel is not None:
                watcher = threading.Thread(target=watch_cancel, daemon=True)
                watcher.start()
            try:
                for line in process.stdout:
                    line = line.strip()
                    if line.startswith("PULSO_DOWNLOAD:"):
                        percent = line.partition(":")[2].strip()
                        progress(f"Baixando áudio · {percent}" if percent != "NA" else "Baixando áudio…")
                    elif line.startswith("PULSO_PROCESS:"):
                        progress("Preparando o arquivo de áudio…")
                    elif line.startswith("PULSO_META:"):
                        metadata = json.loads(line.partition(":")[2])
                    elif line.startswith(("ERROR:", "WARNING:")):
                        diagnostic = line
                return_code = process.wait()
            finally:
                done.set()
                if watcher is not None:
                    watcher.join()
                stop_download(process)
            check_cancelled(cancel)
            if return_code:
                raise RuntimeError(_message(diagnostic))
        downloaded = Path(scratch) / "musica.wav"
        if not downloaded.is_file() or not downloaded.stat().st_size:
            raise RuntimeError("Não há faixa de áudio disponível neste link. Escolha um vídeo gravado ou um arquivo.")
        target = dest_dir / (uuid.uuid4().hex + ".wav")
        os.replace(downloaded, target)
        title = metadata.get("track") or metadata.get("title") or "Faixa do YouTube"
        artist = (metadata.get("artist") or metadata.get("creator")
                  or metadata.get("uploader") or metadata.get("channel") or "YouTube")
        save_cover(metadata.get("id") or "", dest_dir / "cover.jpg")
        check_cancelled(cancel)
        return target, title, artist.removesuffix(" - Topic")
