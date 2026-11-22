"""Audio formats shared by the importer, player storage and converter."""

import subprocess

from importer import find_ffmpeg


FORMATS = {
    "wav": ("-c:a", "pcm_s16le"),
    "m4a": ("-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"),
    "mp3": ("-c:a", "libmp3lame", "-q:a", "2", "-ac", "2"),
    "flac": ("-c:a", "flac", "-compression_level", "5"),
    "ogg": ("-c:a", "libvorbis", "-q:a", "5"),
    "opus": ("-c:a", "libopus", "-b:a", "128k", "-ac", "2"),
}

MEDIA_TYPES = {"." + key for key in FORMATS} | {
    ".aac", ".adts", ".aif", ".aifc", ".aiff", ".alac", ".amr", ".ape",
    ".au", ".caf", ".m4b", ".mka", ".mp2", ".mp4", ".oga", ".wave", ".webm", ".wma",
}


def encode_audio(source, destination, *, filters=None):
    options = FORMATS[destination.suffix[1:].lower()]
    command = [
        find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0",
        "-vn", "-sn", "-dn", *(["-af", filters] if filters else []),
        *options, "-threads", "2", str(destination),
    ]
    try:
        process = subprocess.run(command, capture_output=True, timeout=300,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ValueError("A conversão demorou demais. Tente um áudio mais curto.") from error
    if process.returncode or not destination.is_file() or destination.stat().st_size == 0:
        raise ValueError("O arquivo não contém áudio válido para este formato de saída.")
