"""Servidor local do Pulso. A biblioteca é do anfitrião e as salas vivem em memória."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import ipaddress
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from audio_formats import FORMATS, MEDIA_TYPES, encode_audio
from music_folder import MusicFolder, publish_audio, signature

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DIFFICULTIES = ("easy", "medium", "hard")
MIN_ROOM_PLAYERS = 2
MAX_ROOM_PLAYERS = 7
MAX_UPLOAD = 256 * 1024 * 1024
MIME = {".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac"}
IMPORT_WORKERS = ThreadPoolExecutor(max_workers=1, thread_name_prefix="musica")
CONVERTER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="conversor")
CONVERTER_SLOT = threading.BoundedSemaphore(1)
UPLOAD_SLOTS = threading.BoundedSemaphore(3)
LOCK = threading.RLock()
JOBS = {}
ROOMS = {}
DATA = Path(os.environ.get("PULSO_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Pulso")
SONGS = {}
HIDDEN_SONGS = set()
LEVEL = 1
MUSIC_FOLDER = None
LAN_LOCK = threading.Lock()
PENDING_DOWNLOADS = {}
ACTIVE_DOWNLOAD = None
ACTIVE_CANCEL = None


class Problem(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def now_ms():
    return round(time.time() * 1000)


def json_read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def json_write(path, obj):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        draft = Path(f.name)
        try:
            json.dump(obj, f, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except BaseException:
            f.close()
            draft.unlink(missing_ok=True)
            raise
    try:
        os.replace(draft, path)
    finally:
        draft.unlink(missing_ok=True)


def load_library():
    global LEVEL
    (DATA / "songs").mkdir(parents=True, exist_ok=True)
    (DATA / "imports").mkdir(exist_ok=True)
    HIDDEN_SONGS.clear()
    try:
        hidden = json_read(DATA / "hidden-songs.json")
        if isinstance(hidden, list):
            HIDDEN_SONGS.update(item for item in hidden if isinstance(item, str))
    except (ValueError, OSError):
        pass
    for base in (ROOT / "demo", DATA / "songs"):
        for entry in sorted(base.glob("*/info.json")):
            try:
                info = json_read(entry)
                audio = next(entry.parent.glob("audio.*"))
                if not (entry.parent / "chart.json").is_file() or info["id"] != entry.parent.name:
                    continue
                SONGS[info["id"]] = (info, audio, entry.parent / "chart.json")
            except (KeyError, ValueError, OSError, StopIteration) as error:
                logging.warning("Ignorando entrada incompleta %s: %s", entry.parent, error)
    try:
        LEVEL = max(1, int(json_read(DATA / "progress.json")["level"]))
    except (KeyError, ValueError, OSError, TypeError):
        LEVEL = 1


MAP_UPDATES = {}
MAP_GUARD = threading.Lock()


def current_song(song_id):
    """Upgrade older cached maps once, sharing the result across local/LAN players."""
    entry = get_song(song_id)
    info, audio, chart_path = entry
    from analysis_engine import VERSION
    if info.get("analysisVersion", 0) >= VERSION:
        return entry
    with MAP_GUARD:
        lock = MAP_UPDATES.setdefault(song_id, threading.Lock())
    with lock:
        entry = get_song(song_id)
        info, audio, chart_path = entry
        if info.get("analysisVersion", 0) >= VERSION:
            return entry
        chart = json_read(chart_path)
        if chart.get("analysisVersion", 0) < VERSION:
            from analysis_engine import analyze
            from importer import find_ffmpeg
            os.environ.setdefault("FFMPEG_PATH", find_ffmpeg())
            chart = analyze(audio)
            json_write(chart_path, chart)
        refreshed = dict(info, bpm=chart["bpm"], engine=chart["engine"], analysisVersion=chart["analysisVersion"])
        json_write(chart_path.with_name("info.json"), refreshed)
        with LOCK:
            info.update(refreshed)
    return entry


def get_song(song_id):
    entry = SONGS.get(song_id) if isinstance(song_id, str) else None
    if entry is None:
        raise Problem(404, "A música não está na biblioteca.")
    return entry


def set_song_hidden(song_id, hidden):
    if type(hidden) is not bool:
        raise Problem(400, "Informe se a faixa deve ficar oculta.")
    with LOCK:
        get_song(song_id)
        changed = HIDDEN_SONGS | {song_id} if hidden else HIDDEN_SONGS - {song_id}
        if changed != HIDDEN_SONGS:
            json_write(DATA / "hidden-songs.json", sorted(changed))
            HIDDEN_SONGS.clear()
            HIDDEN_SONGS.update(changed)
    return {"hidden": hidden}


def delete_song(song_id):
    get_song(song_id)
    with MAP_GUARD:
        map_lock = MAP_UPDATES.setdefault(song_id, threading.Lock())
    folder_lock = MUSIC_FOLDER.lock if MUSIC_FOLDER else contextlib.nullcontext()
    with map_lock, folder_lock, LOCK:
        info, audio, _ = get_song(song_id)
        directory = audio.parent
        if (info["kind"] == "demo" or directory.is_symlink() or directory.name != song_id
                or directory.resolve().parent != (DATA / "songs").resolve()):
            raise Problem(409, "Faixas originais podem ser ocultadas no Player.")
        for room in ROOMS.values():
            if room["songId"] == song_id:
                update_room(room)
                if room["state"] in ("lobby", "playing"):
                    raise Problem(409, "Encerre a sala que está usando esta faixa antes de excluir.")
        folder_jobs = MUSIC_FOLDER.pending.values() if MUSIC_FOLDER else ()
        if any(state.get("songId") == song_id and (state["status"] == "saving" or job in folder_jobs)
               for job, state in JOBS.items()):
            raise Problem(409, "Aguarde a faixa terminar de ser salva.")

        originals = MUSIC_FOLDER.originals(song_id) if MUSIC_FOLDER else []
        retired = Path(tempfile.mkdtemp(prefix="remocao-", dir=DATA / "imports"))
        moves = [(directory, retired / "faixa")]
        moves.extend((path, retired / path.name) for path in originals)
        moved = []
        try:
            for source, target in moves:
                source.rename(target)
                moved.append((source, target))
            set_song_hidden(song_id, False)
        except OSError as error:
            for source, target in reversed(moved):
                target.rename(source)
            retired.rmdir()
            raise Problem(409, "O Windows não liberou os arquivos. Feche a faixa e tente de novo.") from error
        del SONGS[song_id]

    try:
        shutil.rmtree(retired)
    except OSError as error:
        raise Problem(409, "A faixa saiu da biblioteca, mas não foi possível apagar todos os arquivos.") from error
    return {"deleted": True}


def set_job(job, **updates):
    with LOCK:
        JOBS[job].update(updates)


def new_job(title="", video_id=""):
    with LOCK:
        for key, value in list(JOBS.items()):
            if key != ACTIVE_DOWNLOAD and value["status"] in ("done", "error", "cancelled") and value["created"] < time.time() - 3600:
                del JOBS[key]
        job = secrets.token_urlsafe(15)
        JOBS[job] = {"status": "queued", "message": "Na fila para análise", "created": time.time(),
                     "title": title, "videoId": video_id}
        return job


def save_music_copy(song_id, source=None):
    info, audio, _ = SONGS[song_id]
    if not MUSIC_FOLDER or info["kind"] == "demo" or info.get("musicFile"):
        return
    name = MUSIC_FOLDER.save(source or audio, info["title"], song_id)
    with LOCK:
        updated = dict(info, musicFile=name)
        json_write(audio.with_name("info.json"), updated)
        info.update(updated)


def restore_downloads():
    with LOCK:
        downloads = [i for i, (info, _, _) in SONGS.items() if info["kind"] == "youtube"]
    for song_id in downloads:
        try:
            save_music_copy(song_id)
        except OSError:
            logging.exception("Falha ao copiar uma faixa para a pasta Músicas")


def run_import(job, folder, source, original_title, url, copy_to_music=True, cancel=None):
    from importer import ImportCancelled, check_cancelled, import_youtube, find_ffmpeg

    def progress(message):
        with LOCK:
            check_cancelled(cancel)
            set_job(job, message=str(message)[:320])

    def prepare_save(song_id):
        check_cancelled(cancel)
        set_job(job, status="saving", message="Salvando na biblioteca…", songId=song_id)

    try:
        with LOCK:
            check_cancelled(cancel)
            set_job(job, status="running")
        if folder is None:
            folder = Path(tempfile.mkdtemp(dir=DATA / "imports"))
        from analysis_engine import analyze
        # O analisador e o importador devem usar a mesma instalação do FFmpeg.
        os.environ.setdefault("FFMPEG_PATH", find_ffmpeg())
        if url:
            progress("Buscando áudio do vídeo")
            source, title, artist = import_youtube(url, folder, progress, cancel=cancel)
            source = Path(source)
            set_job(job, title=title)
        else:
            title, artist = original_title, "Arquivo local"
        progress("Preparando áudio")
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        song_id = digest.hexdigest()[:32]
        with LOCK:
            exists = song_id in SONGS
        if not exists:
            asset = folder / "asset"
            asset.mkdir()
            # Keep the external WAV intact. Lossless FLAC keeps the LAN
            # transfer smaller while using the very same samples for mapping.
            audio = asset / ("audio.flac" if url else "audio.m4a")
            encode_audio(source, audio)
            data = analyze(audio, progress)
            if any(not data["charts"].get(d) for d in DIFFICULTIES):
                raise ValueError("O áudio não tem eventos musicais suficientes para gerar as três dificuldades.")
            info = {"id": song_id, "title": title, "artist": artist or ("YouTube" if url else "Arquivo local"),
                    "duration": data["duration"], "analysisVersion": data["analysisVersion"], "bpm": data.get("bpm", 0), "engine": data.get("engine", ""),
                    "kind": "youtube" if url else "file", "created": now_ms()}
            if JOBS[job]["videoId"]:
                info["videoId"] = JOBS[job]["videoId"]
            cover = folder / "cover.jpg"
            if cover.is_file():
                cover.replace(asset / "cover.jpg")
                info["cover"] = f"/api/songs/{song_id}/cover"
            json_write(asset / "chart.json", data)
            json_write(asset / "info.json", info)
            dest = DATA / "songs" / song_id
            with LOCK:
                prepare_save(song_id)
                os.replace(asset, dest)
                SONGS[song_id] = (info, dest / audio.name, dest / "chart.json")
        else:
            with LOCK:
                info, existing_audio, _ = SONGS[song_id]
                prepare_save(song_id)
                if JOBS[job]["videoId"] and not info.get("videoId") and info.get("kind") == "youtube":
                    updated = dict(info, videoId=JOBS[job]["videoId"])
                    json_write(existing_audio.with_name("info.json"), updated)
                    info.update(updated)
        message = "Música pronta para jogar"
        if copy_to_music:
            try:
                save_music_copy(song_id, source)
            except OSError:
                logging.exception("Falha ao copiar uma faixa para a pasta Músicas")
                message = "Faixa pronta. Confira se a pasta Músicas permite gravação."
        set_job(job, status="done", message=message, songId=song_id)
        return song_id
    except ImportCancelled:
        set_job(job, status="cancelled", message="Download cancelado")
    except Exception as exc:
        if cancel is not None and cancel.is_set():
            set_job(job, status="cancelled", message="Download cancelado")
        else:
            logging.exception("Erro ao importar a música")
            set_job(job, status="error", message=str(exc)[:450] or "Não foi possível importar essa faixa.")
    finally:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)


def original_song_file(info, cached):
    original = MUSIC_FOLDER.original(info.get("musicFile"), info["id"]) if MUSIC_FOLDER else None
    return original or cached


def convert_job(job, folder, source, title, target_format):
    try:
        set_job(job, status="running", message="Convertendo o áudio…")
        suffix = "." + target_format
        temporary = folder / ("converted" + suffix)
        encode_audio(source, temporary)
        directory = MUSIC_FOLDER.path / "Convertidas"
        directory.mkdir(exist_ok=True)
        saved = publish_audio(temporary, directory, title, suffix)
        set_job(job, status="done", message="Arquivo salvo em Músicas › Convertidas", fileName=saved.name)
    except (OSError, ValueError, RuntimeError) as error:
        set_job(job, status="error", message=str(error)[:320])
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        CONVERTER_SLOT.release()


def import_from_music_folder(job, path, stamp):
    folder = None
    song_id = None
    record_error = False
    try:
        if stamp[0] > MAX_UPLOAD:
            raise ValueError("Use um arquivo de áudio de até 256 MB.")
        set_job(job, status="running", message="Lendo arquivo da pasta Músicas")
        folder = Path(tempfile.mkdtemp(dir=DATA / "imports"))
        copy = folder / ("original" + path.suffix.lower())
        shutil.copyfile(path, copy)
        if signature(path) != stamp or copy.stat().st_size != stamp[0]:
            raise OSError("O arquivo ainda está sendo copiado.")
        song_id = run_import(job, folder, copy, path.stem, None, copy_to_music=False)
        record_error = song_id is None
    except ValueError as error:
        record_error = True
        set_job(job, status="error", message=str(error))
    except OSError:
        record_error = True
        set_job(job, status="error", message="Aguarde a cópia terminar e clique em Atualizar músicas.")
    finally:
        if folder:
            shutil.rmtree(folder, ignore_errors=True)
        try:
            MUSIC_FOLDER.complete(path.name, stamp, song_id, record_error)
        except OSError:
            logging.exception("Falha ao gravar o índice da pasta Músicas")


def scan_music_folder(retry=False):
    def enqueue(path, stamp):
        job = new_job(path.stem)
        set_job(job, fromMusicFolder=True)
        IMPORT_WORKERS.submit(import_from_music_folder, job, path, stamp)
        return job
    return MUSIC_FOLDER.scan(enqueue, retry)


def watch_music_folder(stopping):
    while not stopping.is_set():
        try:
            scan_music_folder()
        except OSError:
            logging.warning("A pasta Músicas está temporariamente indisponível.")
        stopping.wait(1.2)


def music_folder_state():
    pending = MUSIC_FOLDER.status()
    with LOCK:
        failures = [job for job in JOBS.values() if job.get("fromMusicFolder") and job["status"] == "error"]
        return {"pending": pending, "revision": len(SONGS), "failed": bool(failures)}


def save_download_queue():
    json_write(DATA / "youtube-queue.json", [
        {"job": job, "videoId": JOBS[job]["videoId"], "title": JOBS[job]["title"],
         "created": JOBS[job]["created"]}
        for job in PENDING_DOWNLOADS
    ])


def start_next_download():
    global ACTIVE_DOWNLOAD, ACTIVE_CANCEL
    # Only one video is handed to the worker at a time. Local files can join
    # the queue between videos, even during a large playlist import.
    if ACTIVE_DOWNLOAD is None and PENDING_DOWNLOADS:
        ACTIVE_DOWNLOAD = next(iter(PENDING_DOWNLOADS))
        ACTIVE_CANCEL = threading.Event()
        IMPORT_WORKERS.submit(run_download, ACTIVE_DOWNLOAD, ACTIVE_CANCEL)


def run_download(job, cancel):
    global ACTIVE_DOWNLOAD, ACTIVE_CANCEL
    vid = JOBS[job]["videoId"]
    with LOCK:
        saved = next((s for s, _, _ in SONGS.values() if s.get("videoId") == vid), None)
    try:
        with LOCK:
            if cancel.is_set():
                set_job(job, status="cancelled", message="Download cancelado")
                return
            if saved and saved["id"] in SONGS:
                set_job(job, status="done", message="Já está na biblioteca", title=saved["title"], songId=saved["id"])
                return
        run_import(job, None, None, None, "https://www.youtube.com/watch?v=" + vid, cancel=cancel)
    finally:
        with LOCK:
            PENDING_DOWNLOADS.pop(job, None)
            ACTIVE_DOWNLOAD = None
            ACTIVE_CANCEL = None
            try:
                save_download_queue()
            except OSError:
                logging.exception("Falha ao atualizar a fila de downloads")
            start_next_download()


def cancel_download(job):
    with LOCK:
        state = JOBS.get(job) if isinstance(job, str) else None
        if not state or not state.get("videoId"):
            raise Problem(404, "Download não encontrado.")
        if state["status"] in ("cancelled", "cancelling"):
            return dict(state, id=job)
        if job not in PENDING_DOWNLOADS or state["status"] not in ("queued", "running"):
            raise Problem(409, "Esta faixa já saiu da etapa de download.")
        before = PENDING_DOWNLOADS.copy()
        PENDING_DOWNLOADS.pop(job)
        try:
            save_download_queue()
        except OSError as error:
            PENDING_DOWNLOADS.clear()
            PENDING_DOWNLOADS.update(before)
            raise Problem(503, "Não foi possível atualizar a fila.") from error
        if job == ACTIVE_DOWNLOAD and state["status"] == "running":
            set_job(job, status="cancelling", message="Cancelando download…")
        else:
            set_job(job, status="cancelled", message="Download cancelado")
        if job == ACTIVE_DOWNLOAD:
            ACTIVE_CANCEL.set()
        return dict(state, id=job)


def restore_download_queue():
    try:
        items = json_read(DATA / "youtube-queue.json")
    except (ValueError, OSError):
        return
    if not isinstance(items, list):
        return
    with LOCK:
        for item in items:
            if not isinstance(item, dict):
                continue
            job, vid = item.get("job"), item.get("videoId")
            if not isinstance(job, str) or not isinstance(vid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            JOBS[job] = {"status": "queued", "message": "Na fila para análise", "created": time.time(),
                         "title": str(item.get("title") or vid)[:180], "videoId": vid}
            PENDING_DOWNLOADS[job] = None
        start_next_download()


def enqueue_tracks(entries):
    """Queue one download per video. The worker keeps running when the tab closes."""
    if not isinstance(entries, list) or not entries:
        raise Problem(400, "Selecione ao menos uma faixa.")
    clean = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("videoId"), str):
            raise Problem(400, "Faixa inválida.")
        vid = entry["videoId"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            raise Problem(400, "Identificador de vídeo inválido.")
        clean[vid] = str(entry.get("title") or vid)[:180]
    jobs = []
    new_downloads = []
    with LOCK:
        saved = {item.get("videoId"): item for item, _, _ in SONGS.values() if item.get("videoId")}
        active = {j["videoId"]: k for k, j in JOBS.items() if j.get("videoId") and j["status"] in ("queued", "running", "saving")}
        for vid, title in clean.items():
            job = active.get(vid)
            if not job:
                job = new_job(title, vid)
                if vid in saved:
                    set_job(job, status="done", message="Já está na biblioteca", title=saved[vid]["title"], songId=saved[vid]["id"])
                else:
                    PENDING_DOWNLOADS[job] = None
                    new_downloads.append(job)
            jobs.append(job)
        if new_downloads:
            try:
                save_download_queue()
            except OSError as error:
                for job in new_downloads:
                    PENDING_DOWNLOADS.pop(job)
                    JOBS.pop(job)
                raise Problem(503, "Não foi possível gravar a fila de músicas.") from error
            start_next_download()
    return jobs


def get_room(code):
    room = ROOMS.get(code.upper())
    if not room:
        raise Problem(404, "Sala não encontrada. Confira o código e o endereço do anfitrião.")
    update_room(room)
    return room


def battle_chart(path):
    """Enrich a copy at read time so old and new maps use identical battle rules."""
    chart = json_read(path)
    for notes in chart["charts"].values():
        for note in notes:
            note["kind"] = "sync" if note.get("sync") else "normal"
        eligible = [n for n in notes if n["kind"] == "normal"]
        if not eligible:
            continue
        start, end = eligible[0]["t"], eligible[-1]["t"]
        spacing = min(4.0, max(0.65, (end - start) / 6))
        target = start + spacing * 0.65
        special = 0
        for note in eligible:
            if note["t"] < target:
                continue
            note["kind"] = ("heal", "critical", "boost")[special % 3]
            special += 1
            target = note["t"] + spacing
    return chart


def emit(room, kind, who, amount, target):
    room["eventId"] += 1
    room["events"].append({
        "id": room["eventId"], "type": kind, "player": who,
        "amount": round(amount, 2), "target": target,
    })


ENERGY_MIN = 20.0
ENERGY_MAX = 100.0
ENERGY_DRAIN = 22.0


def update_power(player, song_time):
    """Energy is integrated on the shared song clock, independent of polling rate."""
    if not player["powerActive"]:
        return
    player["energy"] = max(0.0, (player["powerUntil"] - song_time) * ENERGY_DRAIN)
    if player["energy"] <= 0:
        player["powerActive"] = False


def stop_power(player, song_time):
    update_power(player, song_time)
    if player["powerActive"]:
        start, _ = player["powerWindows"][-1]
        player["powerWindows"][-1] = (start, song_time)
    player["powerActive"] = False
    player["powerKey"] = False
    player["powerUntil"] = 0.0


def powered_at(player, song_time):
    # Hit packets can arrive just after release. Judge the attack against the
    # activation interval that actually covered its note, including across retries.
    return any(start <= song_time < end for start, end in reversed(player["powerWindows"]))


def toggle_power(room, who, active):
    if type(active) is not bool:
        raise Problem(400, "Informe se o poder está pressionado.")
    player = room["players"][who]
    t = elapsed(room)
    if not active:
        stop_power(player, t)
        return
    if (room["state"] != "playing" or player["finished"] or t < 0 or t >= room["duration"]
            or player["powerKey"]):
        return
    player["powerKey"] = True
    if player["energy"] < ENERGY_MIN:
        return
    player["powerActive"] = True
    player["powerUntil"] = t + player["energy"] / ENERGY_DRAIN
    player["powerWindows"].append((t, player["powerUntil"]))


def start_battle(room):
    # Give all clients time to receive the start, count down for three
    # seconds, leave the rail clear for 220 ms and allow a full 2.3s approach.
    team_scale = len(room["players"]) / MIN_ROOM_PLAYERS
    room["maxHealth"] = round(room["maxHealth"] * team_scale)
    room["health"] = float(room["maxHealth"])
    room["maxShield"] = round(0.16 * room["maxHealth"])
    room["startAt"] = now_ms() + 5870
    room["state"] = "playing"
    for player in room["players"]:
        notes = room["charts"][player["difficulty"]]
        player["completedHolds"] = set()
        player["judgments"] = bytearray(len(notes))  # 0 pending, 1 hit, 2 miss
        # Balance each player's total potential independently of map density.
        # Exact weights account for boost windows and critical notes on this map.
        weights = 0
        boost_until = -1
        for note in notes:
            if note["kind"] == "boost":
                boost_until = note["t"] + 6
            weights += (3 if note["kind"] == "critical" else 1) * (2 if note["t"] <= boost_until else 1)
        player["baseDamage"] = room["damageBudget"] / max(1, weights)
        density = len(notes) / max(1.0, room["duration"])
        player["energyPerHit"] = min(5.0, max(3.0, 8.0 / max(0.1, density)))
        player["missCost"] = min(10, max(2, round(10 / max(0.1, density))))


def elapsed(room):
    return (now_ms() - room["startAt"]) / 1000 if room["startAt"] is not None else -4.0


def update_battle(room, song_time, final=False):
    if not room["phaseTwo"] and song_time >= room["shift"] and room["health"] > 0:
        room["phaseTwo"] = True
        room["shieldHealth"] = room["maxShield"]
        emit(room, "shieldUp", None, room["maxShield"], "shield")
    # Leave time for keyboard calibration, the 180 ms batch, retry and LAN jitter.
    # The cursor visits each index at most once across all polls.
    cutoff = float("inf") if final else song_time - 0.95
    for idx, player in enumerate(room["players"]):
        if player["left"]:
            continue
        update_power(player, song_time)
        notes = room["charts"][player["difficulty"]]
        cursor = player["cursor"]
        missing = 0
        while cursor < len(notes) and notes[cursor]["t"] < cutoff:
            if not player["judgments"][cursor]:
                player["judgments"][cursor] = 2
                missing += 1
            cursor += 1
        player["cursor"] = cursor
        if missing:
            player["misses"] += missing
            cost = min(player["health"], missing * player["missCost"])
            player["health"] -= cost
            if cost:
                emit(room, "miss", idx, cost, idx)
                if player["health"] == 0:
                    emit(room, "down", idx, 0, idx)


def leave_room(room, who):
    if who == 0:
        room["state"] = "closed"
        for player in room["players"]:
            stop_power(player, elapsed(room))
    elif room["state"] == "lobby":
        del room["players"][who]
    elif room["state"] == "playing":
        player = room["players"][who]
        stop_power(player, elapsed(room))
        player.update(left=True, finished=True)


def update_room(room):
    now = now_ms()
    players = room["players"]
    if room["state"] in ("lobby", "playing"):
        if now - players[0]["seen"] > 75000:
            leave_room(room, 0)
        else:
            for idx in range(len(players) - 1, 0, -1):
                if not players[idx]["left"] and now - players[idx]["seen"] > 75000:
                    leave_room(room, idx)
    if room["state"] != "playing":
        return
    song_time = elapsed(room)
    final = all(p["finished"] for p in players) or song_time > room["duration"] + 15
    update_battle(room, song_time, final)
    if final:
        room["state"] = "finished"
        for player in players:
            stop_power(player, song_time)
        if room["health"] <= 0:
            global LEVEL
            next_level = max(LEVEL, room["level"] + 1)
            if next_level > LEVEL:
                json_write(DATA / "progress.json", {"level": next_level})
                LEVEL = next_level



def new_player(body):
    diff = body.get("difficulty", "medium")
    if diff not in DIFFICULTIES:
        raise Problem(400, "Escolha uma dificuldade válida.")
    name = body.get("name") or "Jogador"
    if not isinstance(name, str):
        raise Problem(400, "Nome inválido.")
    return {"token": secrets.token_urlsafe(24), "name": name.strip()[:30] or "Jogador", "difficulty": diff,
            "seen": now_ms(), "ready": False, "score": 0, "hits": 0, "finished": False, "left": False,
            "energy": 0.0, "powerActive": False, "powerKey": False, "powerUntil": 0.0,
            "powerWindows": deque(maxlen=32), "holdsCompleted": 0,
            "syncs": set(), "health": 100, "misses": 0, "boostUntil": 0, "damage": 0.0, "cursor": 0}


def phase_transition(chart):
    wave = chart.get("waveform", [])
    size = len(wave)
    if size < 20:
        return chart["duration"] / 2
    # Procura uma mudança de intensidade na região central do arranjo.
    span = max(2, size // 18)
    def contrast(i):
        return abs(sum(wave[i:i + span]) - sum(wave[i - span:i]))
    pivot = max(range(round(size * 0.38), round(size * 0.68)), key=contrast)
    return chart["duration"] * pivot / size


def room_create(body):
    info, _, chart_path = get_song(body.get("songId"))
    p = new_player(body)
    for old, room in list(ROOMS.items()):
        if now_ms() - max(player["seen"] for player in room["players"]) > 86400000:
            del ROOMS[old]
    if len(ROOMS) >= 256:
        raise Problem(429, "Há muitas salas abertas. Tente novamente mais tarde.")
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    while code in ROOMS:
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    chart = battle_chart(chart_path)
    base_hp = round(240 + chart["duration"] * 5)
    max_hp = round(base_hp * (1 + min(0.18, 0.024 * (LEVEL - 1))))
    easy_notes = chart["charts"]["easy"]
    # Keep enough playable attacks after the phase change, even on recordings
    # with a long outro, for the shield to be beatable.
    shift = min(phase_transition(chart), easy_notes[int(0.65 * len(easy_notes))]["t"])
    room = {"code": code, "songId": info["id"], "state": "lobby", "startAt": None, "players": [p],
            "level": LEVEL, "health": float(max_hp), "maxHealth": max_hp, "damageBudget": base_hp * 0.69,
            "charts": chart["charts"], "duration": chart["duration"], "shift": shift, "phaseTwo": False,
            "shieldHealth": 0.0, "maxShield": round(0.16 * max_hp), "events": deque(maxlen=64),
            "eventId": 0, "syncs": set()}
    ROOMS[code] = room
    return {"code": code, "token": p["token"], "me": 0}


def room_join(room, body):
    if room["state"] != "lobby" or len(room["players"]) >= MAX_ROOM_PLAYERS:
        raise Problem(409, "Esta sala já começou, encerrou ou está cheia.")
    p = new_player(body)
    room["players"].append(p)
    return {"code": room["code"], "token": p["token"], "me": len(room["players"]) - 1}


def identify(room, token):
    for idx, p in enumerate(room["players"]):
        if not p["left"] and secrets.compare_digest(p["token"], token):
            p["seen"] = now_ms()
            return idx, p
    raise Problem(403, "Entre nesta sala para continuar.")


def snapshot(room, me):
    song_time = elapsed(room)
    players = []
    for p in room["players"]:
        visible = {k: p[k] for k in ("name", "difficulty", "ready", "score", "hits", "finished", "left",
                                      "health", "misses", "boostUntil")}
        visible.update(maxHealth=100, damage=round(p["damage"], 2),
                       energy=round(p["energy"], 3), powerActive=p["powerActive"],
                       powerUntil=p["powerUntil"] if p["powerActive"] else 0,
                       holdsCompleted=p["holdsCompleted"],
                       multiplier=2 if p["boostUntil"] > 0 and song_time <= p["boostUntil"] else 1)
        players.append(visible)
    return {
        "code": room["code"], "songId": room["songId"], "state": room["state"], "serverTime": now_ms(),
        "startAt": room["startAt"], "me": me, "players": players,
        "limit": MAX_ROOM_PLAYERS, "minimum": MIN_ROOM_PLAYERS,
        "boss": {"health": round(room["health"], 2), "maxHealth": room["maxHealth"], "level": room["level"],
                 "phase": 2 if room["phaseTwo"] else 1, "shield": room["shieldHealth"] > 0,
                 "shieldHealth": round(room["shieldHealth"], 2), "maxShield": room["maxShield"]},
        "events": list(room["events"]),
        "won": room["state"] == "finished" and room["health"] <= 0,
    }


def heal(room, who, target, amount):
    player = room["players"][target]
    gained = min(amount, 100 - player["health"])
    if gained:
        revive = player["health"] == 0
        player["health"] += gained
        emit(room, "heal", who, gained, target)
        if revive:
            emit(room, "revive", who, gained, target)


def attack(room, who, amount, kind):
    player = room["players"][who]
    if room["shieldHealth"] > 0:
        absorbed = min(amount, room["shieldHealth"])
        room["shieldHealth"] -= absorbed
        player["damage"] += absorbed
        emit(room, kind, who, absorbed, "shield")
        if room["shieldHealth"] == 0:
            emit(room, "shieldBreak", who, 0, "shield")
        amount -= absorbed
    if room["health"] > 0 and amount > 0:
        damage = min(amount, room["health"])
        room["health"] -= damage
        player["damage"] += damage
        emit(room, kind, who, damage, "boss")
        if room["health"] == 0:
            emit(room, "bossDown", who, 0, "boss")


def register_hits(room, who, indices, holds=None):
    if holds is None:
        holds = []
    if not isinstance(indices, list) or not isinstance(holds, list) or len(indices) + len(holds) > 256:
        raise Problem(400, "Lote de notas inválido.")
    p = room["players"][who]
    notes = room["charts"][p["difficulty"]]
    if any(type(i) is not int or not 0 <= i < len(notes) for i in indices + holds):
        raise Problem(400, "Índice de nota inválido.")
    song_time = elapsed(room)
    update_battle(room, song_time)
    for i in sorted(set(indices)):
        note = notes[i]
        if p["judgments"][i] or note["t"] > song_time + 0.45:
            continue
        p["judgments"][i] = 1
        kind = note["kind"]
        if kind == "heal":
            heal(room, who, who, 20)
            teammates = (idx for idx, mate in enumerate(room["players"]) if idx != who and not mate["left"])
            target = min(teammates, key=lambda idx: room["players"][idx]["health"], default=None)
            if target is not None:
                heal(room, who, target, 8)
        if kind == "boost":
            p["boostUntil"] = note["t"] + 6
            emit(room, "boost", who, 2, who)
        factor = 2 if p["boostUntil"] > 0 and note["t"] <= p["boostUntil"] else 1
        if kind == "critical":
            factor *= 3
        power = powered_at(p, note["t"])
        if not power and not p["powerActive"]:
            p["energy"] = min(ENERGY_MAX, p["energy"] + p["energyPerHit"])
        base = p["baseDamage"] * (0.5 if p["health"] == 0 else 1) * (2 if power else 1)
        attack(room, who, base * factor, "critical" if kind == "critical" else "hit")
        if kind == "sync":
            stamp = round(note["t"] * 1000)
            p["syncs"].add(stamp)
            if stamp not in room["syncs"] and any(
                stamp in mate["syncs"] for idx, mate in enumerate(room["players"]) if idx != who
            ):
                room["syncs"].add(stamp)
                bonus = min(room["shieldHealth"], room["maxShield"] / 3) if room["shieldHealth"] > 0 else base
                attack(room, who, bonus, "sync")
        p["hits"] += 1
        p["score"] += 100 * factor

    for i in sorted(set(holds)):
        note = notes[i]
        duration = note.get("duration", 0)
        if not duration or p["judgments"][i] != 1 or i in p["completedHolds"]:
            continue
        end = note["t"] + duration
        # Support the release grace in the client and one LAN batch/retry.
        if not end - 0.14 <= song_time <= end + 1.25:
            continue
        p["completedHolds"].add(i)
        p["holdsCompleted"] += 1
        p["score"] += 50
        damage = 0.5 * p["baseDamage"] * (0.5 if p["health"] == 0 else 1)
        attack(room, who, damage * (2 if powered_at(p, end) else 1), "sustain")



def room_action(room, me, p, action, body):
    if action == "power":
        toggle_power(room, me, body.get("active"))
    elif action == "leave":
        if room["state"] in ("lobby", "playing"):
            leave_room(room, me)
            update_room(room)
        return snapshot(room, None)
    elif action == "ready" and room["state"] == "lobby":
        p["ready"] = True
    elif action == "start":
        if me != 0:
            raise Problem(403, "Somente o anfitrião pode iniciar a partida.")
        if room["state"] == "lobby":
            if len(room["players"]) < MIN_ROOM_PLAYERS or not all(x["ready"] for x in room["players"]):
                raise Problem(409, "A partida precisa de pelo menos dois jogadores e todos devem estar prontos.")
            start_battle(room)
    elif action in ("hits", "finish") and room["state"] == "playing":
        if action == "finish":
            if elapsed(room) >= room["duration"] - 0.45:
                p["finished"] = True
                stop_power(p, elapsed(room))
        elif not p["finished"]:
            register_hits(room, me, body.get("indices", []), body.get("holds", []))
        update_room(room)
    elif action not in ("ready", "hits", "finish"):
        raise Problem(404, "Ação desconhecida.")
    return snapshot(room, me)


def local_ipv4s():
    ips = set()
    with contextlib.suppress(OSError):
        ips.update(i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            first = sock.getsockname()[0]
            ips.add(first)
    except OSError:
        first = None
    return sorted((ip for ip in ips if not ip.startswith(("127.", "169.254."))), key=lambda ip: (ip != first, ip))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"
    lan_url = ""
    lan_server = None

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def enable_lan(self):
        with LAN_LOCK:
            if self.lan_url:
                return self.lan_url
            port = self.server_address[1]
            for ip in local_ipv4s():
                try:
                    peer = Server((ip, port), Request)
                except OSError:
                    continue
                self.lan_url = peer.lan_url = f"http://{ip}:{port}"
                peer.valid_hosts = {f"{ip}:{port}"}
                self.lan_server = peer
                threading.Thread(target=peer.serve_forever, daemon=True).start()
                return self.lan_url
        raise Problem(503, "Conecte este computador a uma rede local para criar uma sala.")


class Request(BaseHTTPRequestHandler):
    server_version = "Pulso/1"
    timeout = 30

    def log_message(self, *_):
        pass

    def send_json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def serve_file(self, path, download_name=None):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if match and any(match.groups()):
                first, last = match.groups()
                if first:
                    start, end = int(first), min(int(last), end) if last else end
                else:
                    start = max(0, size - int(last))
                status = 206
            if status != 206 or start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        mime = MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if download_name:
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(download_name, safe=""))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 128 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def content_length(self, limit):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise Problem(400, "Tamanho inválido.")
        if not 0 <= size <= limit:
            raise Problem(413, "O arquivo ultrapassa o limite de 256 MB." if limit == MAX_UPLOAD else "Solicitação muito grande.")
        return size

    def form(self):
        if self.headers.get_content_type() != "application/json":
            raise Problem(415, "Envie dados em JSON.")
        try:
            result = json.loads(self.rfile.read(self.content_length(32768)) or b"{}")
        except (ValueError, UnicodeError):
            raise Problem(400, "JSON inválido.")
        if not isinstance(result, dict):
            raise Problem(400, "Dados inválidos.")
        return result

    def is_host(self):
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def guard(self):
        host = self.headers.get("Host", "").lower()
        if host not in self.server.valid_hosts:
            raise Problem(403, "Endereço do anfitrião inválido.")
        origin = self.headers.get("Origin")
        if self.command == "POST" and origin and origin.rstrip("/").lower() != "http://" + host:
            raise Problem(403, "Origem da solicitação inválida.")

    def read_audio_upload(self, folder):
        if self.headers.get_content_type() != "application/octet-stream":
            raise Problem(415, "Envie um arquivo de áudio.")
        size = self.content_length(MAX_UPLOAD)
        filename = unquote(self.headers.get("X-Filename", ""))[:256].replace("\\", "/").split("/")[-1]
        suffix = Path(filename).suffix.lower()
        if size == 0 or suffix not in MEDIA_TYPES:
            raise Problem(400, "Selecione um arquivo de áudio de formato compatível.")
        source = folder / ("original" + suffix)
        with source.open("wb") as file:
            while size > 0:
                chunk = self.rfile.read(min(size, 256 * 1024))
                if not chunk:
                    raise Problem(400, "O envio foi interrompido.")
                file.write(chunk)
                size -= len(chunk)
        return source, Path(filename).stem

    def open_folder(self, path):
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except (OSError, AttributeError) as error:
            raise Problem(503, "Abra no Explorador a pasta " + str(path)) from error
        return {"opened": True}

    def converter_job(self, source_type):
        if not CONVERTER_SLOT.acquire(blocking=False):
            raise Problem(409, "O conversor está preparando outro arquivo.")
        folder = None
        try:
            body = self.form() if source_type == "song" else {}
            output = body.get("format") if source_type == "song" else self.headers.get("X-Format")
            if not isinstance(output, str) or output not in FORMATS:
                raise Problem(400, "Escolha o formato de saída.")
            folder = Path(tempfile.mkdtemp(dir=DATA / "imports"))
            if source_type == "song":
                info, cached, _ = get_song(body.get("songId"))
                source, title = original_song_file(info, cached), info["title"]
            else:
                source, title = self.read_audio_upload(folder)
            job = new_job(title)
            set_job(job, conversion=True)
            CONVERTER.submit(convert_job, job, folder, source, title, output)
            return {"job": job}
        except BaseException:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            CONVERTER_SLOT.release()
            raise

    def import_job(self, method):
        if not self.is_host():
            raise Problem(403, "Importe músicas no computador anfitrião usando localhost.")
        if method in ("browse", "batch", "youtube"):
            from importer import browse_playlist, youtube_video_id
            body = self.form()
            if method == "batch":
                return {"jobs": enqueue_tracks(body.get("tracks"))}
            url = body.get("url")
            if not isinstance(url, str) or len(url) > 2048:
                raise Problem(400, "Cole um link de vídeo ou playlist do YouTube.")
            try:
                if method == "browse":
                    return browse_playlist(url)
                vid = youtube_video_id(url)
            except (ValueError, RuntimeError) as error:
                raise Problem(400, str(error))
            if not vid:
                raise Problem(400, "Abra a playlist para escolher as faixas.")
            return {"job": enqueue_tracks([{"videoId": vid, "title": "Vídeo do YouTube"}])[0]}
        if not UPLOAD_SLOTS.acquire(blocking=False):
            raise Problem(429, "Há três músicas na fila. Aguarde uma importação terminar.")
        folder = None
        try:
            folder = Path(tempfile.mkdtemp(dir=DATA / "imports"))
            source, title = self.read_audio_upload(folder)
            job = new_job(title)
            IMPORT_WORKERS.submit(run_import, job, folder, source, title, None)
        except BaseException:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            UPLOAD_SLOTS.release()
        return {"job": job}

    def dispatch(self):
        self.guard()
        path = unquote(urlsplit(self.path).path)
        parts = path.strip("/").split("/")
        get = self.command in ("GET", "HEAD")
        if not path.startswith("/api/") and get:
            file = (WEB / (path.lstrip("/") or "index.html")).resolve()
            if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                raise Problem(404, "Arquivo não encontrado.")
            self.serve_file(file)
            return
        result = None
        if get and path == "/api/state":
            with LOCK:
                songs = [dict(v[0], hidden=v[0]["id"] in HIDDEN_SONGS) for v in SONGS.values()]
                songs.sort(key=lambda s: (-s.get("created", 0), {"orbita":0,"vidro-azul":1,"mare-baixa":2}.get(s["id"], 3)))
                result = {"songs": songs, "isHost": self.is_host(), "lanUrl": self.server.lan_url, "level": LEVEL,
                          "musicFolder": self.is_host() and MUSIC_FOLDER is not None, "desktopProtocol": 1}
        elif not get and path == "/api/lan":
            if not self.is_host():
                raise Problem(403, "O anfitrião ativa o modo cooperativo neste computador.")
            self.form()
            result = {"lanUrl": self.server.enable_lan()}
        elif path.startswith("/api/music-folder"):
            if not self.is_host() or MUSIC_FOLDER is None:
                raise Problem(403, "A pasta Músicas fica no computador anfitrião.")
            if get and path == "/api/music-folder":
                result = music_folder_state()
            elif not get and path == "/api/music-folder/scan":
                self.form()
                try:
                    queued = scan_music_folder(retry=True)
                except OSError as error:
                    raise Problem(409, "A pasta Músicas está indisponível.") from error
                result = dict(music_folder_state(), queued=queued)
            elif not get and path == "/api/music-folder/open":
                self.form()
                result = self.open_folder(MUSIC_FOLDER.path)
        elif len(parts) >= 3 and parts[1] == "converter":
            if not self.is_host() or MUSIC_FOLDER is None:
                raise Problem(403, "O conversor está disponível no computador anfitrião.")
            if not get and parts[2] in ("song", "file"):
                result = self.converter_job(parts[2])
            elif not get and parts[2] == "open":
                self.form()
                result = self.open_folder(MUSIC_FOLDER.path / "Convertidas")
            elif get and len(parts) == 4 and parts[2] == "result":
                with LOCK:
                    job = JOBS.get(parts[3], {})
                    name = job.get("fileName") if job.get("conversion") else None
                if not name:
                    raise Problem(404, "A conversão ainda não está disponível.")
                directory = (MUSIC_FOLDER.path / "Convertidas").resolve()
                file = (directory / name).resolve()
                if file.parent != directory or not file.is_file():
                    raise Problem(404, "O arquivo convertido foi movido.")
                self.serve_file(file, name)
                return
        elif get and path == "/api/time":
            result = {"serverTime": now_ms()}
        elif not get and len(parts) == 4 and parts[1] == "songs" and parts[3] in ("visibility", "delete"):
            if not self.is_host():
                raise Problem(403, "Gerencie músicas no computador da biblioteca.")
            body = self.form()
            if parts[3] == "visibility":
                result = set_song_hidden(parts[2], body.get("hidden"))
            elif body.get("confirm") is True:
                result = delete_song(parts[2])
            else:
                raise Problem(400, "Confirme a exclusão da faixa.")
        elif get and len(parts) == 4 and parts[1] == "songs" and parts[3] in ("audio", "chart", "cover", "download"):
            info, audio, chart = current_song(parts[2]) if parts[3]=="chart" else get_song(parts[2])
            if parts[3] == "download":
                original = original_song_file(info, audio)
                name = original.name if original != audio else info["title"] + audio.suffix
                self.serve_file(original, name)
                return
            file = {"audio": audio, "chart": chart, "cover": chart.with_name("cover.jpg")}[parts[3]]
            if not file.is_file():
                raise Problem(404, "Arquivo não encontrado.")
            if parts[3] == "chart":
                self.send_json(200, battle_chart(file))
            else:
                self.serve_file(file)
            return
        elif get and len(parts) == 3 and parts[1] == "jobs":
            with LOCK:
                if parts[2] not in JOBS:
                    raise Problem(404, "Importação não encontrada.")
                if JOBS[parts[2]].get("conversion") and not self.is_host():
                    raise Problem(403, "A conversão fica no computador anfitrião.")
                result = JOBS[parts[2]].copy()
        elif get and path == "/api/import/queue":
            if not self.is_host():
                raise Problem(403, "A fila de importações pertence ao anfitrião.")
            with LOCK:
                result = {"jobs": [dict(item, id=key) for key, item in JOBS.items() if not item.get("conversion")
                                    and (item["status"] in ("queued", "running", "saving", "cancelling") or item["created"] > time.time()-86400)]}
        elif not get and path == "/api/import/cancel":
            if not self.is_host():
                raise Problem(403, "A fila de importações pertence ao anfitrião.")
            result = {"job": cancel_download(self.form().get("jobId"))}
        elif not get and len(parts) == 3 and parts[1] == "import" and parts[2] in ("file", "youtube", "browse", "batch"):
            result = self.import_job(parts[2])
        elif len(parts) >= 2 and parts[1] == "rooms":
            body = {} if get else self.form()
            if len(parts)==2 and not get:
                current_song(body.get("songId"))
            with LOCK:
                if len(parts) == 2 and not get:
                    result = room_create(body)
                elif len(parts) in (3,4):
                    room = get_room(parts[2])
                    action = parts[3] if len(parts) == 4 else None
                    if action == "info" and get:
                        result = {"code": room["code"], "songId": room["songId"], "state": room["state"],
                                  "title": get_song(room["songId"])[0]["title"],
                                  "count": len(room["players"]), "limit": MAX_ROOM_PLAYERS}
                    elif action == "join" and not get:
                        result = room_join(room, body)
                    else:
                        me, p = identify(room, self.headers.get("X-Player-Token", ""))
                        if get and not action:
                            result = snapshot(room, me)
                        elif not get and action:
                            result = room_action(room, me, p, action, body)
        if result is None:
            raise Problem(404, "Recurso não encontrado.")
        self.send_json(200, result)

    def handle_request(self):
        try:
            self.dispatch()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass
        except Problem as error:
            self.send_json(error.code, {"error": str(error), "message": str(error)})
        except Exception:
            logging.exception("Falha na requisição")
            self.send_json(500, {"error": "Falha no servidor local.", "message": "Falha no servidor local."})

    do_GET = do_HEAD = do_POST = handle_request


def main():
    global DATA, MUSIC_FOLDER
    parser = argparse.ArgumentParser(description="Pulso — jogo de ritmo local")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--desktop", action="store_true", help="Ativa a rede local somente ao criar a sala")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    if args.data_dir:
        DATA = args.data_dir
    interface = "127.0.0.1" if args.desktop else "0.0.0.0"
    with Server((interface, args.port), Request) as server:
        load_library()
        MUSIC_FOLDER = MusicFolder(DATA, MEDIA_TYPES, json_write)
        IMPORT_WORKERS.submit(restore_downloads)
        restore_download_queue()
        stopping = threading.Event()
        threading.Thread(target=watch_music_folder, args=(stopping,), daemon=True).start()
        port = server.server_address[1]
        ips = [] if args.desktop else local_ipv4s()
        names = ips + ["localhost", "127.0.0.1", socket.gethostname().lower()]
        server.valid_hosts = {f"{name}:{port}" for name in names}
        if port == 80:
            server.valid_hosts.update(names)
        server.lan_url = f"http://{ips[0]}:{port}" if ips else ""
        local = f"http://localhost:{port}"
        print(f"Pulso: {local}\nRede local: {server.lan_url or 'indisponível'}", flush=True)
        if not args.no_browser and not args.desktop:
            threading.Timer(0.3, webbrowser.open, args=(local,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopping.set()
            if server.lan_server:
                server.lan_server.shutdown()
                server.lan_server.server_close()


if __name__ == "__main__":
    main()
