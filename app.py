#!/usr/bin/env python3
"""KoShelf — local EPUB browser and organizer. Python 3.11+."""
import sys

if sys.version_info < (3, 11):
    sys.exit('KoShelf requires Python 3.11 or later.')

import argparse
import base64
import posixpath
import unicodedata
import re
import ctypes
import errno
import fcntl
import time
import uuid
import plistlib
import subprocess
import signal
import stat as statmod
import secrets
import socket
import json
import shutil
import tempfile
import gzip
import functools
import hashlib
from collections import OrderedDict
from xml.dom import minidom
from html.parser import HTMLParser
import os
from pathlib import Path, PurePosixPath
import threading
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from genre_cleanup import preview_genres, validate_mappings
import cloud_sync
import digest_index
import epub_split
import kindle_fonts
import kindle_delivery
import local_session
from epub_safety import EPUB_MIMETYPE, bounded_read, check_expanded_size, check_xml, image_mime, open_book, parse_xml
from library_fs import open_regular, open_state_text, paths_overlap, private_state_directory, private_state_subdirectory

OPF = 'http://www.idpf.org/2007/opf'
SERIES_POSITION = re.compile(r'[0-9]+(?:\.[0-9]+)*')


STATIC = Path(__file__).parent / 'static'
MAX_XML = 2 * 1024 * 1024
MAX_COVER = 12 * 1024 * 1024
CACHE = {}
COVER_CACHE = OrderedDict()
COVER_CACHE_SIZE = 0
MAX_COVER_CACHE = 96 * 1024 * 1024
COVER_LOCK = threading.Lock()
LOCK = threading.RLock()
COMPARE_LOCK = threading.Lock()
DELIVERY_LOCK = threading.Lock()
WARM_LOCK = threading.Lock()
WARM_READ_LOCK = threading.Lock()
WARM_STOP = threading.Event()
ROOT = None
ROOT_ID = None
AUTO_CONNECT = False
LOCAL_FIRST = False
EJECTED_VOLUME = None
# Long transfers publish counts here; /api/progress reads them without the library lock.
PROGRESS = {'label': '', 'done': 0, 'total': 0}
JOURNAL = Path(os.environ["KOSHELF_STATE_DIR"]) / "moves.jsonl" if "KOSHELF_STATE_DIR" in os.environ else Path(__file__).parent / "state" / "moves.jsonl"


class DescriptionText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li"}: self.parts.append("\n")
    def handle_data(self, data): self.parts.append(data)


def series_metadata(doc):
    """Return the primary series and its matching EPUB collection, if present."""
    tags = doc.findall(f'./{{{OPF}}}metadata/{{{OPF}}}meta')
    legacy = {tag.get('name'): tag.get('content', '').strip() for tag in tags
              if tag.get('name') in ('calibre:series', 'calibre:series_index')}
    refinements = {(tag.get('refines'), tag.get('property')): ''.join(tag.itertext()).strip()
                   for tag in tags if tag.get('refines')}
    legacy_name = legacy.get('calibre:series', '')
    legacy_position = legacy.get('calibre:series_index', '')
    collections = []
    for index, tag in enumerate(tags):
        if tag.get('property') != 'belongs-to-collection' or tag.get('refines'):
            continue
        name = ''.join(tag.itertext()).strip()
        ref = '#' + tag.get('id', '')
        kind = refinements.get((ref, 'collection-type'))
        if name and (kind == 'series' or (not kind and name == legacy_name)):
            collections.append((name, refinements.get((ref, 'group-position'), ''), index))
    matching = next((entry for entry in collections if entry[0] == legacy_name), None)
    if legacy_name:
        position = legacy_position or (matching[1] if matching else '')
        return legacy_name, position, matching[2] if matching else None, collections
    return (*collections[0], collections) if collections else ('', '', None, [])


def cover_item(doc):
    items = {node.get('id'): node for node in doc.findall(f'./{{{OPF}}}manifest/{{{OPF}}}item')}
    legacy = next((node.get('content') for node in doc.findall(f'./{{{OPF}}}metadata/{{{OPF}}}meta')
                   if node.get('name') == 'cover'), None)
    explicit = next((node for node in items.values() if 'cover-image' in node.get('properties', '').split()), items.get(legacy))
    if explicit is not None:
        return explicit
    return next((node for node in items.values() if 'cover' in (node.get('id') or '').lower()
                 and node.get('media-type', '').startswith('image/')), None)


def cover_entry(package, href):
    return posixpath.normpath(posixpath.join(posixpath.dirname(package), urllib.parse.unquote(href.split('#')[0])))


def clean_series_title(doc, title, series, position):
    """Suggest stripping an explicit series suffix from a simple title."""
    titles = doc.findall('./{%s}metadata/{http://purl.org/dc/elements/1.1/}title' % OPF)
    if len(titles) != 1 or titles[0].attrib or len(titles[0]) or not series or not position:
        return None
    position = re.sub(r'(?<=\d)\.0+$', '', position)
    number = re.escape(position)
    if len(position) == 1 and position in '123456789':
        words = ('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine')
        number = f'(?:{number}|{words[int(position) - 1]})'
    suffix = re.fullmatch(
        rf'(.+?)\s*(?::|[–—]| -)\s*(?:book|volume)\s+{number}\s+(?:in|of)\s+(.+)',
        title, flags=re.IGNORECASE,
    )
    if suffix and suffix[2].casefold().removeprefix('the ') == series.casefold().removeprefix('the '):
        return suffix[1].strip() or None
    return None


def metadata(path, *, load_cover=True, fallback_title=None, compare_contents=False):
    result = dict(title=fallback_title or path.stem, author='Unknown author', language='', series='', seriesPosition='', description='', genres=[], cover=None, warning=None)
    try:
        with open_book(path) as z:
            container = parse_xml(bounded_read(z, 'META-INF/container.xml', MAX_XML))
            package = next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('}rootfile'))
            doc = parse_xml(bounded_read(z, package, MAX_XML))
            if compare_contents:
                result['contents'] = sorted(
                    (entry.filename, entry.file_size, entry.CRC) for entry in z.infolist()
                    if entry.filename != package and not entry.is_dir()
                )
            ns = {'dc': 'http://purl.org/dc/elements/1.1/'}
            for field, tag in [('title', 'title'), ('author', 'creator'), ('language', 'language'), ('description', 'description')]:
                values = [''.join(e.itertext()).strip() for e in doc.findall('.//dc:' + tag, ns)]
                if any(values):
                    result[field] = ', '.join(v for v in values if v)
            description = DescriptionText()
            description.feed(result['description'])
            result['description'] = ''.join(description.parts).strip()
            result['authors'] = [v.strip() for e in doc.findall('.//dc:creator', ns) if (v := ''.join(e.itertext())).strip()]
            result['genres'] = [
                value for element in doc.findall('./opf:metadata/dc:subject', {**ns, 'opf': 'http://www.idpf.org/2007/opf'})
                if (value := ' '.join(''.join(element.itertext()).split()))
            ]
            result['series'], result['seriesPosition'], _, _ = series_metadata(doc)
            if suggested := clean_series_title(doc, result['title'], result['series'], result['seriesPosition']):
                result['cleanupTitle'] = suggested
            cover = cover_item(doc)
            if cover is not None:
                result['hasCover'] = True
                name = cover_entry(package, cover.get('href', ''))
                if compare_contents and name in z.namelist():
                    info = z.getinfo(name)
                    result['coverFingerprint'] = (info.file_size, info.CRC)
                if load_cover:
                    data = bounded_read(z, name, MAX_COVER)
                    mime = image_mime(data)
                    if mime:
                        result['cover'] = (mime, data)
    except (OSError, ValueError, KeyError, StopIteration, ET.ParseError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        result['warning'] = 'Some EPUB metadata could not be read. The original file has not been changed.'
    return result


CACHE_STATE = {'loaded': False, 'dirty': False}
MAX_SAVED_METADATA = 20000


def metadata_cache_path():
    return JOURNAL.with_name('metadata-cache.json')


@functools.cache
def metadata_format():
    # Any change to this file invalidates saved metadata, so new fields always appear.
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def load_metadata_cache():
    if CACHE_STATE['loaded']:
        return
    CACHE_STATE['loaded'] = True
    try:
        with open_state_text(metadata_cache_path()) as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or data.get('format') != metadata_format() or not isinstance(data.get('books'), list):
        return
    for entry in data['books']:
        if (isinstance(entry, list) and len(entry) == 4 and isinstance(entry[0], str)
                and isinstance(entry[1], int) and isinstance(entry[2], int)
                and isinstance(entry[3], dict) and isinstance(entry[3].get('title'), str)):
            CACHE.setdefault((entry[0], entry[1], entry[2]), entry[3])


def save_metadata_cache():
    if not CACHE_STATE['dirty']:
        return
    CACHE_STATE['dirty'] = False
    books = [[*key, {**{k: v for k, v in value.items() if k not in ('contents', 'coverFingerprint')}, 'cover': None}]
             for key, value in list(CACHE.items())[-MAX_SAVED_METADATA:]]
    path = metadata_cache_path()
    try:
        private_state_directory(path.parent)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.metadata-', delete=False, encoding='utf-8') as stream:
            temporary = Path(stream.name)
            try:
                os.fchmod(stream.fileno(), 0o600)
                json.dump({'format': metadata_format(), 'books': books}, stream, separators=(',', ':'))
                stream.flush()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError):
        CACHE_STATE['dirty'] = True


def cached_cover(path, info):
    global COVER_CACHE_SIZE
    # Include the file's identity so swapping in a different book of the same
    # size and timestamp does not preserve an earlier cover.
    key = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    with COVER_LOCK:
        if key in COVER_CACHE:
            COVER_CACHE.move_to_end(key)
            return COVER_CACHE[key]
    # Read outside the lock so one slow book does not hold up other covers.
    image = metadata(path)['cover']
    with COVER_LOCK:
        if key not in COVER_CACHE:
            COVER_CACHE[key] = image
            if image:
                COVER_CACHE_SIZE += len(image[1])
        while COVER_CACHE_SIZE > MAX_COVER_CACHE or len(COVER_CACHE) > 4096:
            _, evicted = COVER_CACHE.popitem(last=False)
            if evicted:
                COVER_CACHE_SIZE -= len(evicted[1])
    return image


def mounted_volumes():
    bases = [Path('/Volumes')] if os.sys.platform == 'darwin' else [Path('/media') / os.environ.get('USER', ''), Path('/run/media') / os.environ.get('USER', ''), Path('/mnt')]
    return sorted({volume for base in bases if base.is_dir() for volume in base.iterdir()
                   if volume.is_dir() and not volume.is_symlink()})


def discover():
    return [str(volume / 'books') for volume in mounted_volumes()
            if (volume / 'books').is_dir() and not (volume / 'books').is_symlink()]


def kindle_documents():
    candidates = []
    for volume in mounted_volumes():
        if volume == EJECTED_VOLUME or not volume.is_mount():
            continue
        documents = volume / 'documents'
        if (documents.is_dir() and not documents.is_symlink()
                and (volume.name.casefold() == 'kindle' or has_koreader(volume))):
            candidates.append(documents)
    return candidates[0] if len(candidates) == 1 else None


def has_koreader(volume):
    directory = volume / 'koreader'
    return directory.is_dir() and not directory.is_symlink() and any(
        (directory / name).is_file() and not (directory / name).is_symlink()
        for name in ('koreader.sh', 'reader.lua')
    )


def kindle_candidates(candidates=None):
    return [p for p in (discover() if candidates is None else candidates) if (Path(p).parent / 'documents').is_dir()
            and has_koreader(Path(p).parent)]


def mac_library():
    return Path.home() / 'KoShelf Library'


def kindle_library():
    global EJECTED_VOLUME
    if EJECTED_VOLUME and not EJECTED_VOLUME.exists():
        EJECTED_VOLUME = None
    candidates = [
        Path(path) for path in kindle_candidates()
        if Path(path).parent != EJECTED_VOLUME
    ]
    if len(candidates) == 1:
        try: return candidates[0].resolve(strict=True)
        except FileNotFoundError: pass
    return None


def sync_connection():
    global ROOT, ROOT_ID, EJECTED_VOLUME
    if EJECTED_VOLUME and not EJECTED_VOLUME.exists(): EJECTED_VOLUME = None
    connected = False
    if ROOT is not None:
        try:
            st = ROOT.lstat()
            connected = statmod.S_ISDIR(st.st_mode) and ROOT_ID == (st.st_dev, st.st_ino)
        except OSError: pass
    if not connected and LOCAL_FIRST:
        local = mac_library()
        if local.is_symlink():
            raise ValueError('The Mac library must not be a symbolic link.')
        local.mkdir(exist_ok=True)
        select_library(local); connected = True
    elif not connected and AUTO_CONNECT:
        candidates = [p for p in kindle_candidates() if not EJECTED_VOLUME or Path(p).parent != EJECTED_VOLUME]
        if len(candidates) == 1:
            try: select_library(candidates[0]); connected = True
            except OSError: pass
    return {'connected': connected, 'root': str(ROOT) if ROOT else None}


def device_info(root=None):
    root = ROOT if root is None else root
    if os.sys.platform != 'darwin' or root is None: return None
    # Only an external volume directly under /Volumes may be ejected.
    volume = next((p for p in [root, *root.parents] if p.parent == Path('/Volumes')), None)
    if volume is None or volume.is_symlink(): return None
    try:
        result = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', str(volume)], capture_output=True, timeout=10)
        if result.returncode: return None
        info = plistlib.loads(result.stdout)
        if info.get('MountPoint') != str(volume) or info.get('Internal') is not False or not (info.get('Ejectable') or info.get('Removable')): return None
        return {'volume': str(volume), 'device': info['DeviceIdentifier'], 'name': info.get('VolumeName', volume.name)}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired): return None


def eject_library(device_root=None):
    global ROOT, ROOT_ID, EJECTED_VOLUME
    if device_root is None:
        library_path('', allow_root=True)
        info = device_info()
    else:
        info = device_info(device_root)
    if not info: raise ValueError('This library is not an ejectable external macOS volume.')
    # The request handler holds LOCK: every scan and file operation has finished,
    # and no new operation can begin until diskutil completes. Never force eject.
    try:
        result = subprocess.run(['/usr/sbin/diskutil', 'eject', info['volume']], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('macOS has not confirmed ejection. Check Finder before unplugging.') from exc
    if result.returncode:
        raise ValueError('macOS could not eject the Kindle. Close other apps using it and try again. ' + result.stderr.strip())
    # Confirm unmount, not merely successful process creation.
    try:
        check = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', info['device']], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('Unable to confirm ejection. Check Finder before unplugging.') from exc
    if check.returncode == 0:
        try:
            state = plistlib.loads(check.stdout)
            if state.get('Mounted', False) or state.get('MountPoint'):
                raise ValueError('The Kindle is still mounted. Do not unplug it yet.')
        except plistlib.InvalidFileException as exc:
            raise ValueError('Unable to confirm ejection. Check Finder before unplugging.') from exc
    EJECTED_VOLUME = Path(info['volume'])
    if device_root is None or (ROOT and ROOT.is_relative_to(EJECTED_VOLUME)):
        ROOT = None
        ROOT_ID = None
        CACHE.clear()
    return {'ejected': True, 'name': info['name']}


def storage_usage():
    """Logical file sizes under the library; capacity from the containing filesystem.

    Hidden files and KOReader sidecars count, symlinks never do. File sizes can
    differ from allocated space reported by the operating system.
    """
    root = library_path('', allow_root=True)
    root_device = root.stat().st_dev
    folders = {'': {'bytes': 0, 'direct': 0, 'files': 0}}
    partial = False
    def failed(_):
        nonlocal partial
        partial = True
    for base, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        base = Path(base)
        rel = '' if base == root else base.relative_to(root).as_posix()
        stats = folders.setdefault(rel, {'bytes': 0, 'direct': 0, 'files': 0})
        allowed = []
        for name in dirs:
            child = base / name
            if child.is_symlink(): continue
            try:
                if child.stat().st_dev != root_device:
                    partial = True; continue
                allowed.append(name)
                folders.setdefault(child.relative_to(root).as_posix(), {'bytes': 0, 'direct': 0, 'files': 0})
            except OSError: partial = True
        dirs[:] = allowed
        for name in files:
            child = base / name
            if child.is_symlink(): continue
            try:
                stat = child.stat()
                stats['direct'] += stat.st_size
                stats['files'] += 1
            except OSError: partial = True
        stats['bytes'] = stats['direct']
    for rel in sorted(folders, key=lambda p: p.count('/') + bool(p), reverse=True):
        if not rel: continue
        parent = rel.rsplit('/', 1)[0] if '/' in rel else ''
        folders[parent]['bytes'] += folders[rel]['bytes']
        folders[parent]['files'] += folders[rel]['files']
    volume = None
    try:
        disk = shutil.disk_usage(root)
        volume = {'total': disk.total, 'used': disk.used, 'free': disk.free}
    except OSError: partial = True
    return {'folders': folders, 'volume': volume, 'partial': partial}


def warm_kindle(root):
    """Read EPUB metadata ahead of a switch without holding the library lock."""
    if not WARM_LOCK.acquire(blocking=False):
        return
    try:
        device = root.stat().st_dev
        for base, dirs, files in os.walk(root, followlinks=False):
            base = Path(base)
            dirs[:] = visible_folders(base, dirs, device)
            for name in files:
                book = base / name
                if name.startswith('.') or book.suffix.lower() != '.epub':
                    continue
                if WARM_STOP.is_set() or EJECTED_VOLUME == root.parent:
                    return
                try:
                    info = book.lstat()
                    if not statmod.S_ISREG(info.st_mode) or info.st_dev != device:
                        continue
                    key = (str(book), info.st_size, info.st_mtime_ns)
                    if key not in CACHE:
                        with WARM_READ_LOCK:
                            if WARM_STOP.is_set() or EJECTED_VOLUME == root.parent:
                                return
                            details = metadata(book, load_cover=False)
                        with LOCK:
                            if WARM_STOP.is_set() or EJECTED_VOLUME == root.parent:
                                return
                            if len(CACHE) < 4096 and key not in CACHE:
                                CACHE[key] = details
                                CACHE_STATE['dirty'] = True
                except OSError:
                    continue
    finally:
        WARM_LOCK.release()


READING_STATUSES = ('reading', 'abandoned', 'complete')
READING_SUMMARY = re.compile(rb'^    \["summary"\] = \{\r?\n(.*?)^    \},?[ \t]*\r?$', re.M | re.S)
READING_STATUS = re.compile(rb'^        \["status"\] = "[^"\r\n]*",?[ \t]*\r?$', re.M)
MAX_READING_SETTINGS = 8 * 1024 * 1024


def read_reading_settings(path):
    with open_regular(path) as stream:
        data = stream.read(MAX_READING_SETTINGS + 1)
    if len(data) > MAX_READING_SETTINGS:
        raise ValueError('Unsupported KOReader settings file.')
    return data


def write_reading_status(book, status):
    sidecar = book.with_suffix('.sdr')
    settings = sidecar / 'metadata.epub.lua'
    if sidecar.is_symlink() or settings.is_symlink():
        raise ValueError('KOReader settings must be in an adjacent folder without symbolic links.')
    if not settings.exists():
        if any(sidecar.glob('metadata.epub.lua.*')):
            raise ValueError('KOReader has a backup settings file. Open the book in KOReader first.')
        old = None
        data = b'return {\n}\n'
    else:
        old = data = read_reading_settings(settings)
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    line = b'        ["status"] = "' + status.encode('ascii') + b'",'
    summary = READING_SUMMARY.search(data)
    if not re.search(rb'^return \{\r?$', data, re.M):
        raise ValueError('Unsupported KOReader settings format.')
    if summary:
        block = summary[1]
        if READING_STATUS.search(block):
            block = READING_STATUS.sub(line + (b'\r' if newline == b'\r\n' else b''), block, count=1)
        else:
            if re.search(rb'^        \["status"\]', block, re.M):
                raise ValueError('Unsupported KOReader status formatting.')
            block = line + newline + block
        updated = data[:summary.start(1)] + block + data[summary.end(1):]
    else:
        end = re.search(rb'^}[ \t]*(?:\r?\n)?\Z', data, re.M)
        if not end or re.search(rb'^    \["summary"\]', data, re.M):
            raise ValueError('Unsupported KOReader settings format.')
        updated = data[:end.start()] + newline.join((b'    ["summary"] = {', line, b'    },', b'')) + data[end.start():]
    sidecar.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=sidecar, prefix='.koshelf-edit-', delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(updated)
            output.flush()
            os.fsync(output.fileno())
            if settings.is_symlink() or (read_reading_settings(settings) if settings.exists() else None) != old:
                raise ValueError('KOReader settings changed while saving. Try again.')
            if old is None:
                rename_exclusive(temporary, settings)
            else:
                os.replace(temporary, settings)
        finally:
            temporary.unlink(missing_ok=True)


def reading_status_queue():
    try:
        with open_state_text(JOURNAL.with_name('reading-status.json')) as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


def save_reading_status_queue(queue):
    private_state_directory(JOURNAL.parent)
    with tempfile.NamedTemporaryFile(mode='w', dir=JOURNAL.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(queue, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, JOURNAL.with_name('reading-status.json'))
        finally:
            temporary.unlink(missing_ok=True)


def set_reading_status(relative, status):
    if status not in READING_STATUSES:
        raise ValueError('Choose Reading, On hold, or Finished.')
    book = library_path(relative)
    if book.suffix.lower() != '.epub' or not book.is_file():
        raise ValueError('Choose an existing EPUB.')
    local = ROOT == mac_library().resolve()
    if not local and (not LOCAL_FIRST or ROOT != kindle_library()):
        raise ValueError('Open Mac Library or Kindle Library to change reading status.')
    if local:
        queue = reading_status_queue()
        queue[relative] = {'status': status, 'digest': file_digest(book), 'size': book.stat().st_size}
        save_reading_status_queue(queue)
    else:
        write_reading_status(book, status)
        local_book = mac_library() / relative
        queue = reading_status_queue()
        if relative in queue and local_book.is_file() and queue[relative]['digest'] == file_digest(book):
            queue[relative]['status'] = status
            save_reading_status_queue(queue)
    return {**koreader_reading(book), 'readingStatus': status, 'readingStatusPending': local}


def status_sync_plan(mac, kindle, transfers=None):
    if not mac.is_dir():
        return {}
    books = None
    plan = {}
    for original, edit in reading_status_queue().items():
        if books is None:
            books = backup_book_index(mac)
        try:
            local = backup_alignment_path(mac, original)
        except FileNotFoundError:
            local = None
        except ValueError:
            continue
        digest = edit['digest']
        if local is None or local.is_symlink() or not local.is_file() or sync_digest(local, local.stat()) != digest:
            matches = [path for path in books.get(edit['size'], []) if sync_digest(path, path.stat()) == digest]
            if len(matches) != 1:
                continue
            local = matches[0]
        relative = local.relative_to(mac).as_posix()
        try:
            destination = backup_alignment_path(kindle, relative)
        except FileNotFoundError:
            destination = None
        if (transfers or {}).get(relative) == digest or (destination is not None and destination.is_file() and not destination.is_symlink() and sync_digest(destination, destination.stat()) == digest):
            plan[original] = {**edit, 'path': relative}
    return plan


READING_CACHE = {}


def koreader_reading(book):
    """Read KOReader's reading fields for a book, rereading only changed settings files."""
    sidecar = book.with_suffix('.sdr')
    settings = sidecar / f'metadata.{book.suffix[1:].lower()}.lua'
    try:
        if sidecar.is_symlink():
            return {}
        info = settings.lstat()
    except OSError:
        return {}
    key = (str(settings), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    # FAT drives such as a Kindle keep times in 2-second steps, so a file
    # rewritten within that window can look unchanged. Reread recent files.
    if time.time() - info.st_mtime < 3:
        return parse_koreader_reading(settings)
    if key not in READING_CACHE:
        if len(READING_CACHE) > 8192:
            READING_CACHE.clear()
        READING_CACHE[key] = parse_koreader_reading(settings)
    return dict(READING_CACHE[key])


def parse_koreader_reading(settings):
    try:
        data = read_reading_settings(settings)
    except (OSError, ValueError):
        return {}

    # KOReader serializes top-level keys with four spaces, and summary keys
    # with eight. Anchoring both levels keeps nested annotation fields out.
    summary = re.search(rb'^    \["summary"\] = \{\r?\n(.*?)^    \},?[ \t]*\r?$',
                        data, re.MULTILINE | re.DOTALL)
    status = re.search(rb'^        \["status"\] = "(reading|complete|abandoned)",?[ \t]*\r?$',
                       summary[1], re.MULTILINE) if summary else None
    fraction = re.search(
        rb'^    \["percent_finished"\] = ([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?),?[ \t]*\r?$',
        data, re.MULTILINE
    )
    progress = float(fraction[1]) if fraction else None
    if progress is not None and not 0 <= progress <= 1:
        progress = None
    if status is None and progress is None:
        return {}
    return {'readingStatus': status[1].decode('ascii') if status else 'reading',
            'readingProgress': progress}


def visible_folders(base, names, device):
    folders = []
    for name in names:
        if name.startswith('.') or name.lower().endswith('.sdr'):
            continue
        try:
            info = (base / name).lstat()
        except OSError:
            continue
        if statmod.S_ISDIR(info.st_mode) and info.st_dev == device:
            folders.append(name)
    return folders


def scan():
    connection = sync_connection()
    if not connection['connected']:
        return dict(connected=False, root=str(ROOT) if ROOT else None, books=[], folders=[], candidates=discover())
    books, folders, seen = [], [], set()
    load_metadata_cache()
    status_edits = reading_status_queue() if ROOT == mac_library().resolve() else {}
    device = ROOT_ID[0]
    for base, dirs, files in os.walk(ROOT, followlinks=False):
        base = Path(base)
        dirs[:] = sorted(visible_folders(base, dirs, device))
        for d in dirs: folders.append((Path(base) / d).relative_to(ROOT).as_posix())
        for filename in sorted(files):
            p = base / filename
            if filename.startswith('.') or p.suffix.lower() != '.epub': continue
            try:
                stat = p.lstat()
                if not statmod.S_ISREG(stat.st_mode) or stat.st_dev != device:
                    continue
                rel = p.relative_to(ROOT).as_posix()
                key = (str(p), stat.st_size, stat.st_mtime_ns)
                seen.add(key)
                meta = CACHE.get(key)
                if meta is None:
                    meta = metadata(p, load_cover=False)
                    CACHE[key] = meta
                    CACHE_STATE['dirty'] = True
                reading = koreader_reading(p)
                if rel in status_edits and sync_digest(p, stat) == status_edits[rel]['digest']:
                    reading.update(readingStatus=status_edits[rel]['status'], readingStatusPending=True)
                books.append({**{k: v for k, v in meta.items() if k not in ('cover', 'description')}, **reading, 'hasDescription': bool(meta.get('description')), 'hasCover': meta.get('hasCover', meta['cover'] is not None), 'path': rel, 'folder': '' if p.parent == ROOT else p.parent.relative_to(ROOT).as_posix(), 'filename': filename, 'size': stat.st_size, 'modified': stat.st_mtime})
            except OSError: continue
    prefix = str(ROOT).rstrip('/') + '/'
    for key in [key for key in CACHE if key[0].startswith(prefix) and key not in seen]:
        del CACHE[key]
    while len(CACHE) > max(4096, len(seen)):
        oldest = next(key for key in CACHE if key not in seen)
        del CACHE[oldest]
        CACHE_STATE['dirty'] = True
    save_metadata_cache()
    if not ROOT.is_dir():
        return dict(connected=False, root=str(ROOT), books=[], folders=[], candidates=discover())
    candidates = discover()
    # The switch to a detected Kindle does not need a synchronous diskutil probe.
    # The actual eject still verifies the mount and its ownership with diskutil.
    detected_kindle = LOCAL_FIRST and os.sys.platform == 'darwin' and str(ROOT) in kindle_candidates(candidates)
    return dict(connected=True, root=str(ROOT), books=books, folders=folders,
                candidates=candidates, canEject=detected_kindle or device_info() is not None,
                localFirst=LOCAL_FIRST, macRoot=str(mac_library().resolve()) if LOCAL_FIRST else None)


def library_snapshot(known=None):
    """The library listing, or only its signature when the caller already has it."""
    listing = scan()
    signature = hashlib.sha256(json.dumps(listing, sort_keys=True, default=str).encode()).hexdigest()
    if known == signature:
        return {'unchanged': True, 'signature': signature}
    return {**listing, 'signature': signature}


def select_library(path):
    global ROOT, ROOT_ID
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir(): raise ValueError('Choose an existing folder')
    ROOT = root
    info = root.stat()
    ROOT_ID = (info.st_dev, info.st_ino)


def library_path(rel, allow_root=False):
    if ROOT is None: raise ValueError('Library unavailable. Reconnect your Kindle.')
    stat = ROOT.lstat()
    if not statmod.S_ISDIR(stat.st_mode): raise ValueError('Library unavailable. Reconnect your Kindle.')
    if ROOT_ID != (stat.st_dev, stat.st_ino):
        raise ValueError('The library changed. Choose your library folder again before making changes.')
    if not isinstance(rel, str) or '\x00' in rel: raise ValueError('Invalid path')
    if rel == '' and allow_root: return ROOT
    parts = rel.split('/')
    if not rel or Path(rel).is_absolute() or any(not part or part.startswith('.') or part.lower().endswith('.sdr') or '\\' in part for part in parts):
        raise ValueError('Choose a regular folder or EPUB inside your library')
    candidate = ROOT
    for part in parts:
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if statmod.S_ISLNK(info.st_mode): raise ValueError('Symbolic links cannot be modified')
        if info.st_dev != stat.st_dev: raise ValueError('Library paths cannot cross filesystems')
    candidate.resolve().relative_to(ROOT)
    return candidate


def cleanup_root():
    root = library_path('', allow_root=True)
    local = mac_library()
    if local.is_symlink() or root != local.resolve():
        raise ValueError('Open Mac Library to clean up metadata.')
    return root


def genre_cleanup_preview(body, *, fresh=False):
    root = cleanup_root()

    scope = body.get('scope', 'library')
    if scope == 'selection':
        selected = body.get('paths')
        if not isinstance(selected, list) or not selected or len(selected) > 10000:
            raise ValueError('Choose EPUBs to preview.')
        paths = set()
        for rel in selected:
            path = library_path(rel)
            if path.suffix.lower() != '.epub' or not statmod.S_ISREG(path.lstat().st_mode):
                raise ValueError('Choose existing EPUBs to preview.')
            paths.add(path)
    elif scope in ('library', 'folder'):
        folder = root if scope == 'library' else library_path(body['folder'], allow_root=True)
        if not folder.is_dir():
            raise ValueError('Choose an existing library folder.')
        if scope == 'folder' and folder == root:
            paths = {path for path in root.iterdir()
                     if not path.name.startswith('.') and path.suffix.lower() == '.epub'
                     and statmod.S_ISREG(path.lstat().st_mode)}
        else:
            paths = {path for group in backup_book_index(folder).values() for path in group}
    else:
        raise ValueError('Choose a library, folder, or selection to preview.')

    settings = JOURNAL.with_name('genre-mappings.json')
    save = body.get('saveMappings', False)
    if not isinstance(save, bool) or (save and 'mappings' not in body):
        raise ValueError('Provide genre mappings to save.')
    if 'mappings' in body:
        mappings = validate_mappings(body['mappings'])
    else:
        try:
            with open_state_text(settings) as stream:
                mappings = validate_mappings(json.load(stream))
        except FileNotFoundError:
            mappings = {}

    books = []
    skipped = 0
    for path in sorted(paths):
        try:
            info = path.lstat()
            if not statmod.S_ISREG(info.st_mode):
                skipped += 1
                continue
        except FileNotFoundError:
            skipped += 1
            continue
        key = (str(path), info.st_size, info.st_mtime_ns)
        book = None if fresh else CACHE.get(key)
        # A cached warning may be from a cover read, which is irrelevant here.
        if book is None or book.get('warning'):
            book = metadata(path, load_cover=False)
        if book.get('warning'):
            skipped += 1
            continue
        books.append({**book, 'path': path.relative_to(root).as_posix(), 'revision': cleanup_revision(info)})

    result = preview_genres(books, mappings)
    suggestions = {book['path']: book for book in result['books']}
    for book in books:
        if book['path'] not in suggestions and book.get('cleanupTitle'):
            suggestion = {key: book[key] for key in ('path', 'title', 'author')}
            suggestion.update(current=book['genres'], proposed=book['genres'], changes=[], unmapped=[])
            result['books'].append(suggestion)
            suggestions[book['path']] = suggestion
        if suggestion := suggestions.get(book['path']):
            suggestion['revision'] = book['revision']
            suggestion['proposedTitle'] = book.get('cleanupTitle')
    if save:
        private_state_directory(settings.parent)
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=settings.parent,
                                             prefix='.genre-mappings-', delete=False) as output:
                pending = Path(output.name)
                json.dump(mappings, output, ensure_ascii=False, sort_keys=True)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(pending, settings)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
    return {**result, 'mappings': mappings, 'scope': scope, 'scanned': len(paths), 'skipped': skipped}


def cleanup_revision(info):
    return f'{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}'


def apply_metadata_cleanup(body):
    cleanup_root()
    rel = body['path']
    path = library_path(rel)
    info = path.lstat()
    if cleanup_revision(info) != body['revision']:
        raise ValueError('This book changed since preview. Scan it again.')
    result = genre_cleanup_preview(
        {'scope': 'selection', 'paths': [rel], 'mappings': body['mappings']}, fresh=True,
    )
    suggestion = next(iter(result['books']), None)
    title, genres = body.get('title'), body.get('genres')
    if (not suggestion or suggestion['revision'] != body['revision']
            or (title is not None and title != suggestion['proposedTitle'])
            or (genres is not None and (not suggestion['changes'] or genres != suggestion['proposed']))):
        raise ValueError('The approved changes differ from the current preview. Scan again.')
    changes = {}
    if title is not None:
        changes['title'] = {'before': suggestion['title'], 'after': title}
    if genres is not None:
        changes['genres'] = {'before': suggestion['current'], 'after': genres}
    return edit_metadata(rel, title, None, info.st_size, info.st_mtime, genres,
                         cleanup=changes, expected_revision=body['revision'])


def assert_free(destination):
    if any(p.name.casefold() == destination.name.casefold() for p in destination.parent.iterdir()):
        raise ValueError(f'“{destination.name}” already exists in that folder. Nothing was overwritten.')


def create_folder(parent, name):
    base = library_path(parent, allow_root=True)
    if not base.is_dir(): raise ValueError('Parent folder no longer exists')
    if not isinstance(name, str) or not name.strip() or name != name.strip() or any(c in name for c in '/\\:') or any(ord(c) < 32 for c in name) or name.startswith('.') or name.lower().endswith('.sdr') or name.endswith('.') or len(name.encode('utf-8')) > 240:
        raise ValueError('Use a folder name without slashes, colons, leading dots, or surrounding spaces')
    dest = library_path((parent + '/' if parent else '') + name)
    assert_free(dest)
    dest.mkdir()
    return dest.relative_to(ROOT).as_posix()


def rename_exclusive(source, destination):
    """Same-volume atomic rename that refuses to replace any existing path."""
    libc = ctypes.CDLL(None, use_errno=True)
    if os.sys.platform == 'darwin':
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(os.fsencode(source), os.fsencode(destination), 4) # RENAME_EXCL
    elif os.sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = fn(-100, os.fsencode(source), -100, os.fsencode(destination), 1) # RENAME_NOREPLACE
    else:
        raise ValueError('Safe moves are not supported on this system')
    if result:
        code = ctypes.get_errno()
        if code in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL}:
            return rename_reserved(source, destination)
        raise OSError(code, os.strerror(code), str(destination))


def rename_reserved(source, destination):
    """FAT fallback: reserve a new destination, then replace only our empty placeholder.

    LOCK serializes app operations. Identity checks detect external replacement of
    the reservation; unlike RENAME_EXCL this fallback cannot eliminate the final
    check/rename race with other filesystem applications.
    """
    directory = source.is_dir()
    if directory:
        destination.mkdir()  # Exclusive; an existing name is never accepted.
    else:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    reservation = destination.stat()
    identity = (reservation.st_dev, reservation.st_ino)
    try:
        current = destination.lstat()
        if (current.st_dev, current.st_ino) != identity or destination.is_symlink():
            raise ValueError('Destination changed during the move. Please refresh.')
        if directory:
            if any(destination.iterdir()): raise ValueError('Destination folder changed during the move')
        elif current.st_size != 0:
            raise ValueError('Destination file changed during the move')
        os.rename(source, destination)
    except Exception:
        try:
            current = destination.lstat()
            if (current.st_dev, current.st_ino) == identity:
                if directory: destination.rmdir()
                elif current.st_size == 0: destination.unlink()
        except OSError: pass
        raise


def journal(event):
    with open_state_text(JOURNAL, 'a') as f:
        f.write(json.dumps({**event, 'time': time.time()}) + '\n')
        f.flush()
        os.fsync(f.fileno())


def rewrite_series(doc, meta, original, series, position):
    current_name, current_position, primary, collections = series_metadata(ET.fromstring(original))
    name = current_name if series is None else series
    if not name and position:
        raise ValueError('Enter a series name before setting its position')
    position = (current_position if position is None else position) if name else ''

    modern = doc.documentElement.getAttribute('version').startswith('3')
    prefix = (meta.prefix + ':') if meta.prefix else ''

    def add_meta(attrs, value=None):
        node = doc.createElementNS(OPF, prefix + 'meta')
        for key, text in attrs.items():
            node.setAttribute(key, text)
        if value is not None:
            node.appendChild(doc.createTextNode(value))
        meta.appendChild(node)
        return node

    tags = [node for node in meta.childNodes if node.nodeType == node.ELEMENT_NODE
            and node.namespaceURI == OPF and node.localName == 'meta']
    collection = tags[primary] if primary is not None and name else None
    removed = [tags[index] for _, _, index in collections] if not name else []
    removed_ids = {node.getAttribute('id') for node in removed if node.hasAttribute('id')}
    collection_id = collection.getAttribute('id') if collection is not None else ''
    legacy = False
    for node in tags:
        ref = node.getAttribute('refines').removeprefix('#')
        prop = node.getAttribute('property')
        if node.getAttribute('name') in ('calibre:series', 'calibre:series_index'):
            meta.removeChild(node)
            legacy = True
        elif node in removed or ref in removed_ids:
            meta.removeChild(node)
        elif collection_id and ref == collection_id and (
                prop in ('collection-type', 'group-position')
                or (name != current_name and prop in ('alternate-script', 'file-as'))):
            meta.removeChild(node)
    if collection is not None and name != current_name:
        for child in list(collection.childNodes):
            collection.removeChild(child)
        collection.appendChild(doc.createTextNode(name))

    if not name:
        return
    if modern or collection is not None:
        if not collection_id:
            ids = {node.getAttribute('id') for node in doc.getElementsByTagName('*')}
            collection_id = 'koshelf-series'
            while collection_id in ids:
                collection_id += '-series'
        if collection is None:
            collection = add_meta({'property': 'belongs-to-collection'}, name)
        collection.setAttribute('id', collection_id)
        ref = '#' + collection_id
        add_meta({'refines': ref, 'property': 'collection-type'}, 'series')
        if position:
            add_meta({'refines': ref, 'property': 'group-position'}, position)
    # Calibre-style fields provide compatibility with EPUB 2 and older readers.
    if not modern or legacy:
        add_meta({'name': 'calibre:series', 'content': name})
        if position:
            add_meta({'name': 'calibre:series_index', 'content': position})


def rewrite_opf(data, title, authors, genres=None, *, series=None, series_position=None):
    check_xml(data, allow_external_doctype=False)
    doc = minidom.parseString(data)
    dc = 'http://purl.org/dc/elements/1.1/'
    opf = 'http://www.idpf.org/2007/opf'
    metadata_nodes = doc.getElementsByTagNameNS(opf, 'metadata')
    if not metadata_nodes: raise ValueError('EPUB metadata section is missing')
    meta = metadata_nodes[0]
    if series is not None or series_position is not None:
        rewrite_series(doc, meta, data, series, series_position)
    removed_ids = set()
    fields = []
    sort_fields = set()
    values = []
    if title is not None:
        fields.append('title')
        sort_fields.add('calibre:title_sort')
        values.append(('title', title))
    if authors is not None:
        fields.append('creator')
        sort_fields.add('calibre:author_sort')
        values.extend(('creator', author) for author in authors)
    if genres is not None:
        fields.append('subject')
    for name in fields:
        for node in list(meta.childNodes):
            if node.nodeType == node.ELEMENT_NODE and node.namespaceURI == dc and node.localName == name:
                if node.getAttribute('id'): removed_ids.add(node.getAttribute('id'))
                meta.removeChild(node)
    # Remove refinements tied to old names (including sort names and alternate scripts).
    for node in list(meta.childNodes):
        if node.nodeType != node.ELEMENT_NODE: continue
        if node.getAttribute('refines').lstrip('#') in removed_ids or node.getAttribute('name') in sort_fields:
            meta.removeChild(node)
    meta.setAttribute('xmlns:dc', dc)
    if genres is not None:
        values.extend(('subject', genre) for genre in genres)
    for name, value in values:
        node = doc.createElementNS(dc, 'dc:' + name)
        node.appendChild(doc.createTextNode(value))
        meta.appendChild(node)
    return doc.toxml(encoding='utf-8')


def update_cover_package(data, package, archive, mime):
    """Reuse a compatible cover so existing cover pages still point to it."""
    check_xml(data, allow_external_doctype=False)
    doc = minidom.parseString(data)
    manifest = doc.documentElement.getElementsByTagNameNS(OPF, 'manifest')[0]
    meta = doc.documentElement.getElementsByTagNameNS(OPF, 'metadata')[0]
    items = list(manifest.getElementsByTagNameNS(OPF, 'item'))
    old_tags = [node for node in meta.getElementsByTagNameNS(OPF, 'meta')
                if node.getAttribute('name') == 'cover']
    selected = cover_item(ET.fromstring(data))
    names = set(archive.namelist())
    cover = None
    if selected is not None and selected.get('media-type') == mime:
        entry = cover_entry(package, selected.get('href', ''))
        if entry in names and entry not in {package, 'mimetype', 'META-INF/container.xml'}:
            # A malformed manifest can describe a chapter or another resource
            # as cover art. Only replace the resource if it is an actual image.
            try:
                existing_mime = image_mime(bounded_read(archive, entry, MAX_COVER))
            except ValueError:
                existing_mime = None
            if existing_mime == mime:
                cover = next((node for node in items if node.getAttribute('id') == selected.get('id')), None)
    if cover is None:
        extension = 'jpg' if mime == 'image/jpeg' else 'png'
        ids = {node.getAttribute('id') for node in doc.getElementsByTagName('*')}
        while True:
            identifier = 'koshelf-cover-' + uuid.uuid4().hex
            href = f'{identifier}.{extension}'
            entry = cover_entry(package, href)
            if identifier not in ids and entry not in names:
                break
        cover = doc.createElementNS(OPF, manifest.prefix + ':item' if manifest.prefix else 'item')
        for key, value in [('id', identifier), ('href', href), ('media-type', mime)]:
            cover.setAttribute(key, value)
        manifest.appendChild(cover)
    modern = doc.documentElement.getAttribute('version').startswith('3')
    for item in items:
        properties = item.getAttribute('properties').split()
        if 'cover-image' in properties:
            properties.remove('cover-image')
            if properties:
                item.setAttribute('properties', ' '.join(properties))
            else:
                item.removeAttribute('properties')
    if modern:
        properties = cover.getAttribute('properties').split()
        cover.setAttribute('properties', ' '.join([*properties, 'cover-image']))
    if old_tags or not modern:
        tag = old_tags[0] if old_tags else doc.createElementNS(OPF, meta.prefix + ':meta' if meta.prefix else 'meta')
        tag.setAttribute('name', 'cover')
        tag.setAttribute('content', cover.getAttribute('id'))
        if not old_tags:
            meta.appendChild(tag)
        for extra in old_tags[1:]:
            extra.parentNode.removeChild(extra)
    return doc.toxml(encoding='utf-8'), entry


def cover_upload(encoded):
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_COVER + 2) // 3):
        raise ValueError('Choose a JPEG or PNG up to 12 MB.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError('The selected cover could not be read.') from exc
    mime = image_mime(data)
    if len(data) > MAX_COVER or mime not in ('image/jpeg', 'image/png'):
        raise ValueError('Choose a JPEG or PNG up to 12 MB.')
    if mime == 'image/jpeg' and (len(data) < 20 or not data.endswith(b'\xff\xd9')):
        raise ValueError('The JPEG is incomplete.')
    if mime == 'image/png' and (len(data) < 45 or data[12:16] != b'IHDR' or data[-8:-4] != b'IEND'):
        raise ValueError('The PNG is incomplete.')
    return mime, data


def file_digest(path):
    digest = hashlib.sha256()
    with open_regular(path) as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def backup_book_index(root, sizes=None):
    """Index visible EPUB paths by size before reading any book contents."""
    device = root.stat().st_dev
    index = {}

    def onerror(exc):
        raise exc

    for base, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        base = Path(base)
        dirs[:] = [
            name for name in dirs
            if not name.startswith('.') and not name.lower().endswith('.sdr')
            and not (base / name).is_symlink() and (base / name).stat().st_dev == device
        ]
        for name in files:
            path = base / name
            if name.startswith('.') or path.suffix.lower() != '.epub':
                continue
            info = path.lstat()
            if statmod.S_ISREG(info.st_mode) and info.st_dev == device and (sizes is None or info.st_size in sizes):
                index.setdefault(info.st_size, []).append(path)
    return index


def backup_conflicts(root, target, *, two_way=False):
    if not target.exists():
        return []
    kindle = backup_book_index(root)
    mac = backup_book_index(target, kindle)
    groups = []
    for size, copies in mac.items():
        # Without a path in another folder there is no move to consider.
        if len({p.parent.relative_to(base) for base, paths in
                [(root, kindle[size]), (target, copies)] for p in paths}) < 2:
            continue
        by_digest = {}
        for side, base, paths in [('kindle', root, kindle[size]), ('backup', target, copies)]:
            for path in paths:
                digest = sync_digest(path, path.stat())
                by_digest.setdefault(digest, {'kindle': [], 'backup': []})[side].append(
                    path.relative_to(base).as_posix()
                )
        for digest, paths in by_digest.items():
            old = set(paths['backup'])
            missing = set(paths['kindle']) - old
            kindle_paths = set(paths['kindle'])
            moved_to_kindle = any(Path(a).parent != Path(b).parent for a in missing for b in old)
            moved_to_mac = two_way and any(
                Path(a).parent != Path(b).parent for a in old - kindle_paths for b in kindle_paths
            )
            if moved_to_kindle or moved_to_mac:
                groups.append({'digest': digest, 'kindle': sorted(paths['kindle']), 'backup': sorted(old)})
    return sorted(groups, key=lambda group: group['kindle'])


def backup_alignment_path(base, relative, *, create_parent=False):
    if not isinstance(relative, str) or '\x00' in relative:
        raise ValueError('Invalid library path')
    parts = relative.split('/')
    if any(part in {'', '.', '..'} or '\\' in part for part in parts):
        raise ValueError('Alignment requires folder and book names supported by KoShelf')
    path = base
    root_info = base.lstat()
    if not statmod.S_ISDIR(root_info.st_mode):
        raise ValueError('Choose a regular library folder')
    device = root_info.st_dev
    for part in parts[:-1]:
        path /= part
        if path.is_symlink():
            raise ValueError('An alignment path contains a symbolic link')
        if create_parent:
            path.mkdir(exist_ok=True)
        if path.stat().st_dev != device:
            raise ValueError('Alignment cannot cross filesystems')
    path /= parts[-1]
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if info.st_dev != device:
        raise ValueError('Alignment cannot cross filesystems')
    return path


def align_backup_book(root, target, group, choice):
    base, from_side, to_side = (target, 'backup', 'kindle') if choice == 'kindle' else (root, 'kindle', 'backup')
    source = backup_alignment_path(base, group[from_side][0])
    reference_base = root if base == target else target
    reference = backup_alignment_path(reference_base, group[to_side][0])
    for book, book_root in ((source, base), (reference, reference_base)):
        if (book.is_symlink() or not book.is_file() or book.stat().st_dev != book_root.stat().st_dev
                or file_digest(book) != group['digest']):
            raise ValueError('A book changed during review. Run the backup again.')
    destination = backup_alignment_path(base, group[to_side][0], create_parent=True)
    move_epub(source, destination, base)
    return base, source, destination


# macOS writes these on FAT drives such as a Kindle. They hold Finder
# metadata rather than books and never belong in a library sync or backup.
SYSTEM_JUNK = {'.DS_Store', '.Trashes', '.Spotlight-V100', '.fseventsd', '.TemporaryItems',
               '.apdisk', '.DocumentRevisions-V100', '.VolumeIcon.icns'}


def system_junk(name):
    return name.startswith('._') or name in SYSTEM_JUNK


def sync_version_path(relative, digest, *, create=False):
    """Where sync keeps a Kindle version that differs from the Mac copy.

    Recovery copies live in KoShelf's private data folder, mirroring the
    library path, so they never appear on the shelf or sync to iCloud Drive.
    """
    relative = PurePosixPath(relative)
    parts = relative.parts
    if not parts or relative.is_absolute() or any(part in ('', '.', '..') for part in parts):
        raise ValueError('Invalid sync path.')
    if create:
        folder = private_state_subdirectory(JOURNAL.parent, 'sync-versions', *parts[:-1])
    else:
        folder = JOURNAL.parent.joinpath('sync-versions', *parts[:-1])
    return backup_version(folder / parts[-1], digest)


MAX_READING_VERSIONS = 5


def prune_sync_versions(kindle, keep=MAX_READING_VERSIONS):
    """Keep only the newest recovery copies of each KOReader reading file.

    KOReader rewrites a book's .sdr settings on every reading session, so each
    sync saves another copy. EPUB copies, any copy that sync history needs for
    undo, and the copy that matches the Kindle's current file are always kept.
    """
    folder = JOURNAL.parent / 'sync-versions'
    try:
        if not statmod.S_ISDIR(folder.lstat().st_mode):
            return 0
    except FileNotFoundError:
        return 0
    needed = {action['backup'] for run in sync_history() for action in run['actions']
              if isinstance(action, dict) and isinstance(action.get('backup'), str)}
    groups = {}
    for base, dirs, files in os.walk(folder, followlinks=False):
        relative = PurePosixPath(Path(base).relative_to(folder).as_posix())
        if not any(part.lower().endswith('.sdr') for part in relative.parts):
            continue
        for name in files:
            match = re.fullmatch(r'(.+) \(backup ([0-9a-f]{16})\)(.*)', name)
            path = Path(base) / name
            if not match or str(path) in needed:
                continue
            try:
                info = path.lstat()
            except OSError:
                continue
            if statmod.S_ISREG(info.st_mode):
                saved = getattr(info, 'st_birthtime', info.st_ctime)
                original = relative / (match[1] + match[3])
                groups.setdefault(original, []).append((saved, name, match[2], path))
    removed = 0
    for original, versions in groups.items():
        # A version the Kindle has returned to keeps its old birth time, so keep it by content.
        current = None
        try:
            kindle_file = kindle.joinpath(*original.parts)
            info = kindle_file.lstat()
            if statmod.S_ISREG(info.st_mode):
                current = sync_digest(kindle_file, info)[:16]
        except (OSError, ValueError):
            pass
        for _, _, digest, path in sorted(versions, reverse=True)[keep:]:
            if digest == current:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def saved_sync_version(relative, digest, library_path_, *, fresh=False):
    """Find the saved Kindle version, including copies older releases kept in the library."""
    for version in (sync_version_path(relative, digest), backup_version(Path(library_path_), digest)):
        try:
            info = version.lstat()
        except FileNotFoundError:
            continue
        if statmod.S_ISREG(info.st_mode) and (file_digest(version) if fresh else sync_digest(version, info)) == digest:
            return version
    return None


def mac_edit_history(mac):
    """Earlier contents of Mac books that KoShelf edited, keyed by library path."""
    versions = {}
    libraries = {str(mac)}
    try:
        libraries.add(str(mac.resolve()))
        with open_state_text(JOURNAL) as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (event.get('operation') == 'edit-metadata' and event.get('status') == 'completed'
                        and event.get('library') in libraries and isinstance(event.get('source'), str)
                        and isinstance(event.get('sha256'), str)):
                    # Mac and Kindle volumes are case-insensitive, so match paths that way.
                    versions.setdefault(event['source'].casefold(), set()).add(event['sha256'])
    except FileNotFoundError:
        pass
    return versions


def reverted_kindle_updates():
    """Kindle replacements the user undid, as (casefolded path, Kindle digest) pairs.

    Sync must not reapply these on its own after the user chose the Kindle version.
    """
    return {(action['path'].casefold(), action['previous'])
            for run in sync_history() for action in run['actions']
            if action.get('kind') == 'replaced' and action.get('undone')
            and isinstance(action.get('path'), str) and isinstance(action.get('previous'), str)}


def stale_after_mac_edit(relative, kindle_digest, edits, reverted):
    """Whether this Kindle EPUB is a version from before an edit made in KoShelf."""
    key = relative.casefold()
    return (relative.lower().endswith('.epub') and kindle_digest in edits.get(key, ())
            and (key, kindle_digest) not in reverted)


def backup_version(path, digest):
    stem = path.stem.encode('utf-8')[:150].decode('utf-8', errors='ignore')
    return path.with_name(f'{stem} (backup {digest[:16]}){path.suffix}')


DIGESTS = digest_index.DigestIndex(lambda: JOURNAL.with_name('sync-digests.json'), lambda path: file_digest(path))


def sync_digest(path, info):
    return DIGESTS.digest(path, info)


MAX_IMPORT = 128 * 1024 * 1024


def import_destination(folder, name, size, digest):
    destination = library_path(folder, allow_root=True)
    if not destination.is_dir():
        raise ValueError('The destination folder is unavailable.')
    if (not isinstance(name, str) or not name.lower().endswith('.epub')
            or name.startswith('.') or any(c in name for c in '/\\:')
            or any(ord(c) < 32 for c in name) or len(name.encode()) > 240):
        raise ValueError('Choose an EPUB with a supported filename.')
    if not isinstance(size, int) or not 0 < size <= MAX_IMPORT:
        raise ValueError('Choose an EPUB smaller than 128 MiB.')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Invalid EPUB checksum.')
    book = destination / name
    for existing in backup_book_index(ROOT, {size}).get(size, []):
        if sync_digest(existing, existing.stat()) == digest:
            raise ValueError(f'Already on this shelf at {existing.relative_to(ROOT)}.')
    assert_free(book)
    assert_free(book.with_suffix('.sdr'))
    return book


def import_epub(query, stream, size):
    folder, name, digest, root = (query[key][0] for key in ('folder', 'name', 'digest', 'root'))
    if str(ROOT) != root or not 0 < size <= MAX_IMPORT:
        raise ValueError('The shelf changed or the EPUB exceeds the import size limit.')
    destination = import_destination(folder, name, size, digest)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.koshelf-edit-', dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            remaining = size
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError('The upload was interrupted.')
                output.write(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
        if file_digest(temporary) != digest:
            raise ValueError('The EPUB changed after preview.')
        if metadata(temporary, load_cover=False)['warning']:
            raise ValueError('This file could not be read as an EPUB.')
        import_destination(folder, name, size, digest)
        rename_exclusive(temporary, destination)
        return {'path': destination.relative_to(ROOT).as_posix()}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def find_duplicates(root):
    """Find identical EPUB files and suggest matches from embedded metadata."""
    by_digest = {}
    by_metadata = {}

    def normalize(value):
        return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())

    for size, paths in backup_book_index(root).items():
        for path in paths:
            info = path.stat()
            details = CACHE.get((str(path), info.st_size, info.st_mtime_ns))
            if details is None:
                details = metadata(path, load_cover=False)
            digest = sync_digest(path, info) if len(paths) > 1 else None
            book = {'path': path.relative_to(root).as_posix(), 'title': details['title'],
                    'author': details['author'], 'size': size}
            if digest is not None:
                by_digest.setdefault(digest, []).append(book)
            if details.get('authors') and not details.get('warning'):
                key = (normalize(details['title']), tuple(sorted(map(normalize, details['authors']))))
                fingerprint = (size, digest)
                by_metadata.setdefault(key, {}).setdefault(fingerprint, []).append(book)

    exact = [books for books in by_digest.values() if len(books) > 1]
    possible = [[book for copies in versions.values() for book in copies]
                for versions in by_metadata.values() if len(versions) > 1]
    for groups in (exact, possible):
        for books in groups:
            books.sort(key=lambda book: book['path'].casefold())
        groups.sort(key=lambda books: (books[0]['title'].casefold(), books[0]['path'].casefold()))
    return {'exact': exact, 'possible': possible}


def duplicate_archive():
    return mac_library().with_name('KoShelf Duplicate Archive')


def archived_duplicates():
    archive = duplicate_archive()
    if archive.is_symlink():
        raise ValueError('The duplicate archive cannot be a symbolic link.')
    if not archive.exists():
        return []
    entries = []
    for folder in sorted(archive.iterdir(), reverse=True):
        if folder.is_symlink() or not folder.is_dir() or folder.name.startswith('.'):
            continue
        manifest = folder / 'entry.json'
        if manifest.is_symlink() or not manifest.is_file():
            continue
        with manifest.open(encoding='utf-8') as stream:
            entry = json.load(stream)
        book = folder / Path(entry['path']).name
        if book.is_file() and not book.is_symlink():
            entries.append({**entry, 'archivePath': str(book)})
    return entries


def archive_duplicate(keep, remove):
    root = library_path('', allow_root=True)
    archive = duplicate_archive()
    if archive.is_symlink() or paths_overlap(archive, root):
        raise ValueError('The archive must be separate from the current shelf.')
    keeper = library_path(keep)
    source = library_path(remove)
    if keeper == source or not all(book.is_file() and book.suffix.lower() == '.epub' for book in (keeper, source)):
        raise ValueError('Choose two different EPUBs on this shelf.')
    # Recheck the current groups before removing a copy from the shelf.
    groups = find_duplicates(root)
    if not any({keep, remove} <= {book['path'] for book in group}
               for group in groups['exact'] + groups['possible']):
        raise ValueError('These books no longer match. Run Duplicate finder again.')

    return archive_epub(root, keeper, source, file_digest(source))


def archive_epub(root, keeper, source, digest, keeper_digest=None):
    archive = duplicate_archive()
    if archive.is_symlink() or paths_overlap(archive, root):
        raise ValueError('The archive must be separate from the current shelf.')
    original = source.stat()
    remove = source.relative_to(root).as_posix()
    keep = keeper.relative_to(root).as_posix()
    archive.mkdir(exist_ok=True)
    name = f'{time.time_ns()}-{uuid.uuid4().hex}'
    with tempfile.TemporaryDirectory(prefix='.pending-', dir=archive) as staging:
        folder = Path(staging)
        saved = folder / source.name
        with open_regular(source) as stream, saved.open('xb') as output:
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        if file_digest(saved) != digest:
            raise ValueError('The archived EPUB failed verification.')
        entry = {'path': remove, 'kept': keep, 'root': str(root), 'sha256': digest,
                 'archived': time.time(), 'title': metadata(source, load_cover=False)['title']}
        with (folder / 'entry.json').open('x', encoding='utf-8') as stream:
            json.dump(entry, stream)
            stream.flush()
            os.fsync(stream.fileno())
        rename_exclusive(folder, archive / name)
    current = source.lstat()
    if (keeper.is_symlink() or not keeper.is_file()
            or (keeper_digest is not None and file_digest(keeper) != keeper_digest)
            or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
            != (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns)
            or file_digest(source) != digest):
        raise ValueError('The shelf changed during archiving. The original and archived copy are both available.')
    source.unlink()
    return {'title': entry['title'], 'path': remove, 'archivePath': str(archive / name / source.name)}


def archived_paths(mac, kindle):
    paths = {}
    for entry in archived_duplicates():
        if not entry.get('restored') and entry['root'] in {str(mac), str(kindle)}:
            paths.setdefault(entry['path'], set()).add(entry['sha256'])
    return paths


def kindle_duplicate_plan(mac, kindle, send):
    """Apply Mac archive decisions only to the same EPUB at its original path."""
    plan = {}
    for entry in archived_duplicates():
        if entry.get('restored') or entry['root'] != str(mac):
            continue
        remove, keep = entry['path'], entry['kept']
        if remove == keep or any(
            not part or part.startswith('.') or part.lower().endswith('.sdr') or '\\' in part
            for path in (remove, keep) for part in path.split('/')
        ):
            continue
        try:
            source = backup_alignment_path(kindle, remove)
            mac_keeper = backup_alignment_path(mac, keep)
        except (FileNotFoundError, ValueError):
            continue
        if (source.is_symlink() or not source.is_file()
                or mac_keeper.is_symlink() or not mac_keeper.is_file()):
            continue
        digest = sync_digest(source, source.stat())
        if digest != entry['sha256']:
            continue
        keep_digest = sync_digest(mac_keeper, mac_keeper.stat())
        kindle_keeper = kindle / keep
        if os.path.lexists(kindle_keeper):
            try:
                backup_alignment_path(kindle, keep)
            except ValueError:
                continue
            if (kindle_keeper.is_symlink() or not kindle_keeper.is_file()
                    or sync_digest(kindle_keeper, kindle_keeper.stat()) != keep_digest):
                continue
        else:
            send[keep] = keep_digest
        plan[remove] = (keep, digest, keep_digest)
    # A copy scheduled for removal cannot also be the last remaining copy.
    return {path: decision for path, decision in plan.items() if decision[0] not in plan}


def sync_needed(root, target):
    """Check for work the additive Kindle-to-Mac sync can perform."""
    if target.is_symlink():
        raise ValueError('The Mac library must not be a symbolic link.')
    device = root.stat().st_dev
    archived = archived_paths(target, root)
    edits = mac_edit_history(target)
    reverted = reverted_kindle_updates() if edits else set()

    def onerror(exc):
        raise exc

    # Plain strings and single lstat calls keep this hot path cheap: the
    # status check runs it over every file on a USB volume.
    root_text, target_text = str(root), str(target)

    def regular(path):
        try:
            return statmod.S_ISREG(os.lstat(path).st_mode)
        except OSError:
            return False

    for base, dirs, files in os.walk(root_text, followlinks=False, onerror=onerror):
        relative_base = os.path.relpath(base, root_text)
        relative_base = '' if relative_base == '.' else relative_base
        out = os.path.join(target_text, relative_base) if relative_base else target_text
        try:
            if not statmod.S_ISDIR(os.lstat(out).st_mode):
                return True
        except OSError:
            return True
        kept = []
        for name in dirs:
            if system_junk(name):
                continue
            info = os.lstat(os.path.join(base, name))
            if not statmod.S_ISLNK(info.st_mode) and info.st_dev == device:
                kept.append(name)
        dirs[:] = kept
        for name in files:
            if name.startswith('.koshelf-edit-') or system_junk(name):
                continue
            source = os.path.join(base, name)
            info = os.lstat(source)
            if not statmod.S_ISREG(info.st_mode):
                continue
            relative = f'{relative_base}/{name}' if relative_base else name
            if relative in archived and sync_digest(source, info) in archived[relative]:
                continue
            destination = os.path.join(out, name)
            if not regular(destination):
                return True
            saved = os.stat(destination)
            digest = sync_digest(source, info)
            if saved.st_size == info.st_size and sync_digest(destination, saved) == digest:
                continue
            # A saved Kindle version settles a conflict, but not a book that is
            # still waiting for the edited Mac version.
            if (saved_sync_version(relative, digest, destination) is None
                    or stale_after_mac_edit(relative, digest, edits, reverted)):
                return True
    return False


def kindle_import_plan(mac, kindle):
    """Find new Mac EPUBs without sending backup versions or moved duplicates."""
    if mac.is_symlink() or paths_overlap(mac, kindle):
        raise ValueError('The Mac library and Kindle must be separate folders.')
    kindle_books = backup_book_index(kindle)
    kindle_paths = {book.relative_to(kindle).as_posix().casefold()
                    for books in kindle_books.values() for book in books}
    transfers = {}
    archived = archived_paths(mac, kindle)
    for size, books in backup_book_index(mac).items():
        for source in books:
            relative = source.relative_to(mac).as_posix()
            if relative.casefold() in kindle_paths:
                continue
            if re.fullmatch(r'.+ \(backup [0-9a-f]{16}\)', source.stem):
                continue
            if os.path.lexists(kindle / relative):
                continue
            info = source.stat()
            checksum = sync_digest(source, info)
            if checksum in archived.get(relative, ()):
                continue
            if any(sync_digest(book, book.stat()) == checksum for book in kindle_books.get(size, ())):
                continue
            transfers[relative] = checksum
    return dict(sorted(transfers.items()))


def send_to_kindle(kindle, review=None):
    mac = mac_library()
    identity = kindle.stat()
    transfers = kindle_import_plan(mac, kindle)
    token = hashlib.sha256(json.dumps([str(kindle), identity.st_dev, identity.st_ino, transfers]).encode()).hexdigest()
    if review is None:
        return {'review': token, 'count': len(transfers), 'paths': list(transfers)[:100]}
    if review != token:
        raise ValueError('The transfer list changed. Open the review again.')
    history = SyncHistory('Send to Kindle')
    result = copy_to_kindle(mac, kindle, identity, transfers, history=history)
    history.finish(result)
    return result


def report_progress(label='', done=0, total=0):
    PROGRESS.update(label=label, done=done, total=total)


def copy_to_kindle(mac, kindle, identity, transfers, *, replacements=None, history=None):
    replacements = replacements or {}
    replaced = 0
    copied = 0
    errors = []
    for index, (relative, checksum) in enumerate(transfers.items()):
        report_progress('Copying to Kindle', index, len(transfers))
        temporary = None
        try:
            current = kindle.stat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('The Kindle changed during transfer.')
            source = backup_alignment_path(mac, relative)
            if source.is_symlink() or not source.is_file():
                raise ValueError('The Mac EPUB is unavailable.')
            destination = backup_alignment_path(kindle, relative, create_parent=True)
            previous = replacements.get(relative)
            if previous is None:
                assert_free(destination)
                assert_free(destination.with_suffix('.sdr'))
            with tempfile.NamedTemporaryFile(prefix='.koshelf-edit-', dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                with open_regular(source) as stream:
                    shutil.copyfileobj(stream, output)
                output.flush()
                os.fsync(output.fileno())
            if file_digest(temporary) != checksum:
                raise ValueError('The copied EPUB failed verification.')
            if previous is None:
                rename_exclusive(temporary, destination)
                copied += 1
                if history:
                    history.record('copied', kindle, destination, checksum)
            else:
                archive = saved_sync_version(relative, previous, source, fresh=True)
                if archive is None:
                    raise ValueError('Could not verify the original Kindle EPUB backup.')
                if destination.is_symlink() or not destination.is_file() or file_digest(destination) != previous:
                    raise ValueError('The Kindle EPUB changed during sync.')
                os.chmod(temporary, destination.stat().st_mode & 0o777)
                os.replace(temporary, destination)
                replaced += 1
                if history:
                    history.record('replaced', kindle, destination, checksum, previous=previous, backup=archive)
        except (OSError, ValueError) as exc:
            errors.append(f'{relative}: {exc}')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {'copied': copied, 'replaced': replaced, 'failed': len(errors), 'errors': errors[:5]}


def compare_epub_versions(mac_book, kindle_book):
    mac = metadata(mac_book, load_cover=False, compare_contents=True)
    kindle = metadata(kindle_book, load_cover=False, compare_contents=True)
    if mac['warning'] or kindle['warning']:
        return {'available': False, 'title': mac_book.stem}
    changes = []
    for key, label in (
        ('title', 'Title'), ('author', 'Author'), ('genres', 'Genres'),
        ('series', 'Series'), ('seriesPosition', 'Series position'), ('language', 'Language'), ('description', 'Description'),
    ):
        mac_value, kindle_value = mac[key], kindle[key]
        if mac_value != kindle_value:
            changes.append({'label': label, 'mac': mac_value, 'kindle': kindle_value})
    if mac.get('coverFingerprint') != kindle.get('coverFingerprint'):
        changes.append({'label': 'Cover art', 'mac': 'Embedded cover' if mac.get('coverFingerprint') else 'No cover',
                        'kindle': 'Different embedded cover' if kindle.get('coverFingerprint') else 'No cover'})
    return {'available': True, 'title': mac['title'], 'changes': changes,
            'contentsDiffer': mac['contents'] != kindle['contents']}


def sync_history_log(event):
    path = JOURNAL.with_name('sync-history.jsonl')
    with open_state_text(path, 'a') as stream:
        stream.write(json.dumps(event) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def icloud_drive():
    return Path.home() / 'Library' / 'Mobile Documents' / 'com~apple~CloudDocs'


def icloud_config():
    settings = JOURNAL.with_name('icloud-folder.json')
    try:
        with open_state_text(settings) as stream:
            return json.load(stream)
    except FileNotFoundError:
        return None


def icloud_folder(raw):
    if not isinstance(raw, str) or not raw.startswith('/') or '\x00' in raw:
        raise ValueError('Choose an absolute folder path inside iCloud Drive.')
    candidate = Path(raw)
    try:
        drive = icloud_drive().resolve(strict=True)
        folder = candidate.resolve(strict=True)
    except RuntimeError as exc:
        raise ValueError('Choose an iCloud folder without symbolic links.') from exc
    if (folder != candidate or not folder.is_relative_to(drive) or folder == drive
            or any(part.startswith('.') for part in folder.relative_to(drive).parts)):
        raise ValueError('Choose a dedicated folder inside iCloud Drive.')
    if not folder.is_dir():
        raise ValueError('Choose an existing folder inside iCloud Drive.')
    for protected in (mac_library().resolve(), JOURNAL.parent.resolve()):
        if paths_overlap(folder, protected):
            raise ValueError('Choose a separate iCloud folder for syncing.')
    return folder


def folder_identity(folder):
    info = folder.stat()
    return [info.st_dev, info.st_ino]


def icloud_status():
    config = icloud_config()
    drive = icloud_drive()
    result = {'path': None, 'available': False,
              'defaultPath': str(drive / 'KoShelf Library') if drive.is_dir() else None}
    if not config:
        return result
    result['path'] = config['path']
    try:
        folder = icloud_folder(config['path'])
        if folder_identity(folder) != config['identity']:
            raise ValueError('The iCloud folder changed. Choose it again to reconnect.')
        result['available'] = True
    except FileNotFoundError:
        result['reason'] = 'The selected iCloud folder is unavailable.'
    except (OSError, ValueError) as exc:
        result['reason'] = str(exc)
    return result


def require_icloud_mac_library():
    root = library_path('', allow_root=True)
    local = mac_library()
    if local.is_symlink() or root != local.resolve():
        raise ValueError('Open Mac Library to use iCloud sync.')
    return root


def configure_icloud(raw):
    require_icloud_mac_library()
    settings = JOURNAL.with_name('icloud-folder.json')
    private_state_directory(settings.parent)
    if raw is None:
        settings.unlink(missing_ok=True)
        return icloud_status()
    folder = icloud_folder(raw)
    config = {'path': str(folder), 'identity': folder_identity(folder)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=settings.parent,
                                         prefix='.icloud-folder-', delete=False) as output:
            temporary = Path(output.name)
            json.dump(config, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, settings)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return icloud_status()


def icloud_history(event=None):
    path = JOURNAL.with_name('icloud-history.jsonl')
    if event is not None:
        with open_state_text(path, 'a') as stream:
            stream.write(json.dumps(event) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return None
    runs = {}
    try:
        with open_state_text(path) as stream:
            for line in stream:
                try:
                    record = json.loads(line)
                    runs.setdefault(record['id'], {}).update(record)
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
    except FileNotFoundError:
        pass
    return list(reversed(runs.values()))


def run_icloud_sync(body):
    mac = require_icloud_mac_library()
    # Native and browser instances can have separate state directories.
    # Lock the shared Mac library itself without creating a file on either shelf.
    fd = os.open(mac, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another KoShelf window is syncing with iCloud.') from exc
        info = os.fstat(fd)
        if ROOT_ID != (info.st_dev, info.st_ino):
            raise ValueError('Mac Library changed. Reopen it before syncing.')
        return sync_icloud_library(mac, body)
    finally:
        os.close(fd)


def sync_icloud_library(mac, body):
    status = icloud_status()
    if not status['available']:
        raise ValueError(status.get('reason', 'Choose a folder in iCloud Drive first.'))
    folder = Path(status['path'])
    # Never carry a deletion baseline forward to a replacement of either root.
    key = hashlib.sha256(json.dumps([str(mac), folder_identity(mac), str(folder),
                                     folder_identity(folder)]).encode()).hexdigest()
    state = private_state_subdirectory(JOURNAL.parent, 'icloud-sync', key)
    token = body.get('token')
    if token is None:
        preview = cloud_sync.preview(mac, folder, state)
        return {**preview, 'token': key + '.' + preview['token']}
    choices = body.get('choices', {})
    if not isinstance(token, str) or not isinstance(choices, dict):
        raise ValueError('Review iCloud changes before syncing.')
    scope, _, token = token.partition('.')
    if scope != key or not token:
        raise ValueError('The iCloud library changed. Review sync again.')
    event = {'id': uuid.uuid4().hex, 'timestamp': time.time(), 'path': str(folder),
             'status': 'Interrupted', 'applied': 0, 'skipped': 0, 'actions': []}
    icloud_history(event)
    try:
        report_progress()
        result = cloud_sync.apply(mac, folder, state, token, choices, rename=rename_exclusive,
                                  progress=lambda done, total: report_progress('Syncing with iCloud Drive', done, total))
    except (ValueError, OSError) as exc:
        icloud_history({'id': event['id'], 'status': 'Failed', 'error': str(exc)})
        raise
    try:
        icloud_history({'id': event['id'], 'status': 'Completed with errors' if result['errors'] else 'Completed',
                        'applied': result['applied'], 'skipped': result['skipped'],
                        'actions': result['changes'], 'errors': result['errors']})
    except OSError:
        result['warning'] = 'Sync finished, but KoShelf could not update iCloud history.'
    return result


def sync_history():
    runs = {}
    path = JOURNAL.with_name('sync-history.jsonl')
    if path.exists():
        with open_state_text(path) as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                run = runs.setdefault(event['run'], {'id': event['run'], 'actions': []})
                if event['kind'] == 'start':
                    run.update(time=event['time'], mode=event['mode'], status='Interrupted')
                elif event['kind'] == 'finish':
                    run.update(status=event['status'], errors=event['errors'])
                elif event['kind'] == 'undo':
                    for action in run['actions']:
                        if action['id'] == event['id']:
                            action['undone'] = True
                else:
                    run['actions'].append(event)
    return [run for run in reversed(runs.values()) if 'time' in run]


class SyncHistory:
    def __init__(self, mode):
        self.run = uuid.uuid4().hex
        self.write('start', mode=mode, time=time.time())

    def write(self, kind, **fields):
        sync_history_log({'run': self.run, 'kind': kind, **fields})

    def record(self, kind, root, path, digest, *, previous=None, backup=None, origin=None):
        path = path.relative_to(root).as_posix()
        parts = PurePosixPath(path).parts
        if not path.lower().endswith('.epub') or any(p.startswith('.') or p.lower().endswith('.sdr') for p in parts):
            return
        info = root.stat()
        self.write(kind, id=uuid.uuid4().hex, root=str(root),
                   side='Mac' if root == mac_library() else 'Kindle',
                   identity=[info.st_dev, info.st_ino], path=path, digest=digest,
                   previous=previous, backup=str(backup) if backup else None,
                   origin=origin.relative_to(root).as_posix() if origin else None)

    def finish(self, *results):
        failed = sum(result['failed'] for result in results)
        errors = [error for result in results for error in result['errors']]
        self.write('finish', status='Completed with errors' if failed else 'Completed', errors=errors)


def sync_restore_copy(source, destination, digest, *, replace_digest=None):
    temporary = None
    try:
        with open_regular(source) as stream, tempfile.NamedTemporaryFile(
                prefix='.koshelf-edit-', dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        if file_digest(temporary) != digest:
            raise ValueError('The recovery copy could not be verified.')
        if replace_digest is None:
            assert_free(destination)
            rename_exclusive(temporary, destination)
        else:
            if destination.is_symlink() or file_digest(destination) != replace_digest:
                raise ValueError('This book has changed since sync.')
            os.chmod(temporary, destination.stat().st_mode & 0o777)
            os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def preserve_sync_copy(path, digest, action_id):
    if not re.fullmatch('[a-f0-9]{32}', action_id):
        raise ValueError('Invalid sync action ID.')
    recovery = private_state_subdirectory(JOURNAL.parent, 'sync-recovery', action_id)
    saved = recovery / path.name
    if not saved.exists():
        sync_restore_copy(path, saved, digest)
    if saved.is_symlink() or file_digest(saved) != digest or file_digest(path) != digest:
        raise ValueError('The recovery copy or shelf changed.')


def undo_sync_action(run_id, action_id):
    history = sync_history()
    for run in history:
        if run['id'] == run_id:
            action = next((entry for entry in run['actions'] if entry['id'] == action_id), None)
            break
    else:
        action = None
    if not action or action.get('undone'):
        raise ValueError('This sync action is no longer available to undo.')
    if action['kind'] == 'kept version':
        raise ValueError('This recovery copy is stored privately, and nothing on your shelves changed.')
    root = mac_library() if action['side'] == 'Mac' else kindle_library()
    if root is None or str(root) != action['root'] or root.is_symlink():
        raise ValueError('Reconnect the library used for this sync.')
    info = root.stat()
    if [info.st_dev, info.st_ino] != action['identity']:
        raise ValueError('The library mount has changed since this sync.')
    path = backup_alignment_path(root, action['path'])
    kind = action['kind']
    digest = action['digest']
    if kind != 'archived' and (path.is_symlink() or not path.is_file() or file_digest(path) != digest):
        raise ValueError('This book has moved or changed since sync.')
    if kind == 'moved':
        origin = backup_alignment_path(root, action['origin'], create_parent=True)
        move_epub(path, origin, root)
    elif kind in ('archived', 'replaced'):
        backup = Path(action['backup'])
        if kind == 'replaced' and not backup.exists() and backup.is_relative_to(mac_library()):
            backup = next((Path(entry['archivePath']) for entry in archived_duplicates()
                           if entry['path'] == backup.relative_to(mac_library()).as_posix()
                           and entry['sha256'] == action['previous']), backup)
        if backup.is_symlink() or not backup.is_file():
            raise ValueError('The original version is no longer available at its recorded location.')
        if kind == 'archived':
            sync_restore_copy(backup, path, digest)
            # Restoring a Kindle duplicate also cancels the linked Mac archive rule.
            for entry in archived_duplicates():
                if (entry['path'] == action['path'] and entry['sha256'] == digest
                        and entry['root'] in {str(mac_library()), str(root)}):
                    manifest = Path(entry['archivePath']).parent / 'entry.json'
                    record = json.loads(manifest.read_text(encoding='utf-8'))
                    record['restored'] = True
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=manifest.parent, delete=False) as output:
                        json.dump(record, output)
                        pending = Path(output.name)
                    try:
                        os.replace(pending, manifest)
                    finally:
                        pending.unlink(missing_ok=True)
        else:
            preserve_sync_copy(path, digest, action_id)
            sync_restore_copy(backup, path, action['previous'], replace_digest=digest)
    elif kind in ('copied', 'saved version'):
        if any(entry['kind'] == 'replaced' and not entry.get('undone')
               and entry['backup'] == str(path) for run in history for entry in run['actions']):
            raise ValueError('Undo the Kindle replacement before removing its recovery copy.')
        if path.with_suffix('.sdr').exists() or path.with_suffix('.sdr').is_symlink():
            raise ValueError('This copy is associated with KOReader data. Move it manually to keep that data together.')
        # Keep this copy recoverable even if the other library has since changed.
        preserve_sync_copy(path, digest, action_id)
        path.unlink()
    else:
        raise ValueError('Unknown sync action.')
    sync_history_log({'run': run_id, 'kind': 'undo', 'id': action_id})
    return {'path': action['path']}


def empty_folder_removal_plan(mac, kindle):
    """Find explicit Mac removals whose Kindle counterparts are still empty."""
    removed = set()
    try:
        with open_state_text(JOURNAL) as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get('library') != str(mac) or event.get('status') != 'completed':
                    continue
                if event.get('operation') == 'remove-folder' and event.get('entries') == []:
                    removed.add(event['source'])
                elif event.get('operation') == 'sync-empty-folder-removal' and event.get('kindle') == str(kindle):
                    removed.discard(event['source'])
    except FileNotFoundError:
        return {}
    plan = {}
    for relative in sorted(removed):
        parts = relative.split('/')
        if any(not part or part.startswith('.') or part.lower().endswith('.sdr') for part in parts):
            continue
        try:
            source = backup_alignment_path(mac, relative)
            destination = backup_alignment_path(kindle, relative)
            if os.path.lexists(source) or destination.is_symlink() or not destination.is_dir():
                continue
            info = destination.stat()
            if info.st_dev == kindle.stat().st_dev and not any(not system_junk(child.name) for child in destination.iterdir()):
                plan[relative] = (info.st_dev, info.st_ino, info.st_mtime_ns)
        except (FileNotFoundError, ValueError):
            continue
    return plan


def sync_libraries(kindle, choice=None, review=None, use_mac=None):
    mac = mac_library()
    identity = kindle.stat()
    send = kindle_import_plan(mac, kindle)
    duplicate_plan = kindle_duplicate_plan(mac, kindle, send)
    archived = archived_paths(mac, kindle)
    backup = backup_library(source=kindle, preview=True, two_way=True, archived=archived)
    incoming = []
    incoming_folders = []
    conflicts = []
    epub_conflicts = {}
    signatures = []
    reviewed_files = {}
    mac_device = mac.stat().st_dev
    for base, dirs, files in os.walk(kindle, followlinks=False, onerror=raise_walk_error):
        base = Path(base)
        dirs[:] = sorted(name for name in dirs if not system_junk(name) and not (base / name).is_symlink()
                         and (base / name).stat().st_dev == identity.st_dev)
        relative_folder = base.relative_to(kindle)
        out = mac / relative_folder
        try:
            out_info = out.lstat()
        except FileNotFoundError:
            if relative_folder.parts and not dirs and not any(not system_junk(name) for name in files):
                incoming_folders.append(relative_folder.as_posix())
        else:
            if not statmod.S_ISDIR(out_info.st_mode) or out_info.st_dev != mac_device:
                dirs[:] = []
                continue
        for name in sorted(files):
            source = base / name
            info = source.lstat()
            if (not statmod.S_ISREG(info.st_mode) or info.st_dev != identity.st_dev
                    or name.startswith('.koshelf-edit-') or system_junk(name)):
                continue
            path = source.relative_to(kindle).as_posix()
            destination = out / name
            try:
                destination_info = destination.lstat()
            except FileNotFoundError:
                destination_info = None
            if destination_info is not None and (
                not statmod.S_ISREG(destination_info.st_mode) or destination_info.st_dev != mac_device
            ):
                continue
            checksum = sync_digest(source, info)
            if checksum in archived.get(path, ()):
                continue
            reviewed_files[path] = checksum
            if destination_info is not None:
                saved = sync_digest(destination, destination_info)
                if saved == checksum:
                    continue
                if (source.suffix.lower() == '.epub' and not source.name.startswith('.')
                        and not any(part.startswith('.') or part.lower().endswith('.sdr')
                                    for part in PurePosixPath(path).parts[:-1])
                        and destination.resolve().is_relative_to(mac.resolve())):
                    epub_conflicts[path] = (checksum, saved)
                if saved_sync_version(path, checksum, destination) is not None:
                    continue
                conflicts.append(path)
                signatures.append((path, checksum, saved))
            else:
                incoming.append(path)
                signatures.append((path, checksum, None))

    status_plan = status_sync_plan(mac, kindle, send)
    remove_folders = empty_folder_removal_plan(mac, kindle)
    incoming_folders = [path for path in incoming_folders if path not in remove_folders]
    # A Kindle copy that matches an earlier Mac version predates an edit made
    # in KoShelf, so the Mac version is simply newer: update the Kindle. An
    # undone update is a choice to keep the Kindle version, so offer it instead.
    edits = mac_edit_history(mac)
    reverted = reverted_kindle_updates() if edits else set()
    mac_newer = sorted(path for path, (kindle_digest, _) in epub_conflicts.items()
                       if stale_after_mac_edit(path, kindle_digest, edits, reverted))
    choosable = [path for path in epub_conflicts if path not in mac_newer]
    token = hashlib.sha256(json.dumps([backup['review'], send, signatures, epub_conflicts, duplicate_plan, status_plan, remove_folders, incoming_folders, mac_newer]).encode()).hexdigest()
    if choice is None:
        conflicts = [path for path in conflicts if path not in mac_newer]
        review_conflicts = sorted((set(conflicts) | epub_conflicts.keys()) - set(mac_newer))
        return {**backup, 'review': token, 'readingStatuses': list(status_plan.values()), 'removeEmptyFolders': list(remove_folders), 'incomingEmptyFolders': incoming_folders, 'toKindle': len(send), 'toKindlePaths': list(send)[:100],
                'archiveOnKindle': [{'remove': path, 'keep': decision[0]}
                                    for path, decision in duplicate_plan.items()],
                'toMac': len(incoming), 'toMacPaths': incoming[:100],
                'conflicts': len(conflicts), 'conflictPaths': conflicts[:100],
                'epubConflictPaths': choosable, 'macNewerPaths': mac_newer,
                'epubComparisons': {path: compare_epub_versions(mac / path, kindle / path)
                                    for path in choosable},
                'versionDifferenceCount': len(review_conflicts), 'versionDifferencePaths': review_conflicts[:100]}
    if review != token:
        raise ValueError('The libraries changed during review. Open Sync libraries again.')

    if use_mac is None:
        use_mac = []
    if not isinstance(use_mac, list) or any(not isinstance(path, str) or path not in epub_conflicts for path in use_mac):
        raise ValueError('Select EPUB versions from the current sync review.')
    use_mac = sorted(set(use_mac) | set(mac_newer))

    if choice == 'merge':
        for group in backup_conflicts(kindle, mac, two_way=True):
            for path in group['backup']:
                if group['digest'] not in archived.get(path, ()) and not os.path.lexists(kindle / path):
                    send[path] = group['digest']
    history = SyncHistory('Sync libraries')
    result = backup_library(source=kindle, choice=choice, review=backup['review'],
                            two_way=True, archived=archived, history=history, skip_folders=remove_folders,
                            reviewed_files=reviewed_files)
    # Folder alignment may make a reviewed destination obsolete. Only send
    # files from the original plan, and recheck their contents during copying.
    replacements = {}
    for path in use_mac:
        kindle_digest, mac_digest = epub_conflicts[path]
        replacements[path] = kindle_digest
        send[path] = mac_digest
    sent = copy_to_kindle(mac, kindle, identity, send, replacements=replacements, history=history)
    sent['removedEmptyFolders'] = 0
    for relative, signature in remove_folders.items():
        try:
            current = kindle.stat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('The Kindle changed during sync.')
            path = backup_alignment_path(kindle, relative)
            info = path.lstat()
            if (not statmod.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino, info.st_mtime_ns) != tuple(signature)
                    or os.path.lexists(backup_alignment_path(mac, relative))):
                raise ValueError('The folder changed during sync.')
            # Only macOS metadata files may remain; remove them so the folder can go.
            for child in path.iterdir():
                child_info = child.lstat()
                if not system_junk(child.name) or not statmod.S_ISREG(child_info.st_mode):
                    raise ValueError('The folder is no longer empty.')
                child.unlink()
            path.rmdir()
            sent['removedEmptyFolders'] += 1
            journal({'operation': 'sync-empty-folder-removal', 'library': str(mac),
                     'kindle': str(kindle), 'source': relative, 'status': 'completed'})
        except (OSError, ValueError) as exc:
            sent['failed'] += 1
            if len(sent['errors']) < 5:
                sent['errors'].append(f'{relative}: {exc}')
    sent['archived'] = 0
    for path, (keep, digest, keep_digest) in duplicate_plan.items():
        try:
            current = kindle.stat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('The Kindle changed during sync.')
            source = backup_alignment_path(kindle, path)
            keeper = backup_alignment_path(kindle, keep)
            if (source.is_symlink() or keeper.is_symlink() or not keeper.is_file()
                    or file_digest(keeper) != keep_digest):
                raise ValueError('The chosen copy could not be verified on the Kindle.')
            entry = archive_epub(kindle, keeper, source, digest, keep_digest)
            sent['archived'] += 1
            history.record('archived', kindle, source, digest, backup=entry['archivePath'])
        except (OSError, ValueError) as exc:
            sent['failed'] += 1
            if len(sent['errors']) < 5:
                sent['errors'].append(f'{path}: {exc}')
    sent['readingStatuses'] = 0
    queue = reading_status_queue()
    for key, edit in status_plan.items():
        try:
            current = kindle.stat()
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise ValueError('The Kindle changed during sync.')
            book = backup_alignment_path(kindle, edit['path'])
            if book.is_symlink() or file_digest(book) != edit['digest']:
                raise ValueError('Kindle book moved or changed. Review sync again.')
            write_reading_status(book, edit['status'])
            local = backup_alignment_path(mac, edit['path'])
            if not local.is_symlink() and local.is_file() and file_digest(local) == edit['digest']:
                write_reading_status(local, edit['status'])
            del queue[key]
            save_reading_status_queue(queue)
            sent['readingStatuses'] += 1
        except (OSError, ValueError) as exc:
            sent['failed'] += 1
            if len(sent['errors']) < 5:
                sent['errors'].append(f"{edit['path']}: {exc}")
    history.finish(result, sent)
    try:
        prune_sync_versions(kindle)
    except Exception as exc:  # The sync already finished; pruning can wait for the next one.
        print(f'KoShelf could not prune old recovery copies: {exc}', file=sys.stderr)
    return {'mac': result, 'kindle': sent}


def raise_walk_error(error):
    raise error


def kindle_status(root):
    if root is None:
        return {'kindle': None, 'syncNeeded': False}
    result = {'kindle': str(root), 'syncNeeded': False}
    try:
        mac = mac_library()
        result['syncNeeded'] = (sync_needed(root, mac)
                                or (mac.is_dir() and bool(kindle_import_plan(mac, root)
                                                         or kindle_duplicate_plan(mac, root, {})
                                                         or status_sync_plan(mac, root))))
    except digest_index.Cancelled:
        result['cancelled'] = True
    except (OSError, ValueError) as exc:
        result['syncError'] = str(exc)
    finally:
        DIGESTS.save()
    return result


def backup_library(destination=None, choice=None, review=None, *, source=None, preview=False, two_way=False,
                   archived=None, history=None, skip_folders=(), reviewed_files=None):
    report_progress()
    """Copy a library snapshot without overwriting its existing backup files."""
    root = library_path('', allow_root=True) if source is None else source
    root_stat = root.lstat()
    if not statmod.S_ISDIR(root_stat.st_mode):
        raise ValueError('Choose a regular library folder')

    def validate_source():
        current = root.lstat()
        if (not statmod.S_ISDIR(current.st_mode)
                or not os.path.samestat(current, root_stat)):
            raise ValueError('The source library changed. Reconnect and try again.')

    target = destination if destination is not None else mac_library()
    target = Path(target)
    if paths_overlap(target, root):
        raise ValueError('The backup folder and the library must be separate.')
    if target.is_symlink(): raise ValueError('The backup folder must not be a symbolic link.')
    if choice not in {None, 'kindle', 'backup', 'merge', 'copy'}:
        raise ValueError('Choose a backup option.')
    archived = archived or {}
    conflicts = [
        group for group in backup_conflicts(root, target, two_way=two_way)
        if not any(group['digest'] in archived.get(path, ()) for path in group['kindle'] + group['backup'])
    ]
    digest = hashlib.sha256()
    for base in (root, target):
        if base.exists():
            info = base.stat()
            digest.update(os.fsencode(base))
            digest.update(f'{info.st_dev}:{info.st_ino}'.encode())
    digest.update(json.dumps([conflicts, [(path, sorted(hashes)) for path, hashes in sorted(archived.items())]]).encode())
    token = digest.hexdigest()
    if choice is not None and review != token:
        raise ValueError('The library or backup changed during review. Start the backup again.')
    if conflicts and choice == 'copy':
        raise ValueError('Choose how to handle the matching EPUBs.')
    if preview or (conflicts and choice is None):
        return {'review': token, 'count': len(conflicts), 'examples': [
            {side: group[side][:3] for side in ('kindle', 'backup')} for group in conflicts[:5]
        ]}
    target.mkdir(parents=True, exist_ok=True)
    target_stat = target.lstat()
    device = root_stat.st_dev
    result = {'destination': str(target), 'copied': 0, 'skipped': 0, 'versions': 0, 'moved': 0, 'ignored': 0, 'failed': 0, 'errors': []}

    def problem(path, exc):
        result['failed'] += 1
        if len(result['errors']) < 5: result['errors'].append(f'{path}: {exc}')

    excluded = set()
    if choice in {'kindle', 'backup'}:
        for group in conflicts:
            try:
                if len(group['kindle']) != 1 or len(group['backup']) != 1:
                    raise ValueError('Multiple copies already exist. Left all copies in place.')
                validate_source()
                base, source, destination = align_backup_book(root, target, group, choice)
                result['moved'] += 1
                if base == root and reviewed_files is not None:
                    # The user's location choice can also move a companion folder.
                    before = source.relative_to(root).as_posix()
                    after = destination.relative_to(root).as_posix()
                    if before in reviewed_files:
                        reviewed_files[after] = reviewed_files.pop(before)
                    old_sidecar = source.with_suffix('.sdr').relative_to(root).as_posix() + '/'
                    new_sidecar = destination.with_suffix('.sdr').relative_to(root).as_posix() + '/'
                    for path in tuple(reviewed_files):
                        if path.startswith(old_sidecar):
                            reviewed_files[new_sidecar + path[len(old_sidecar):]] = reviewed_files.pop(path)
                if history:
                    history.record('moved', base, destination, group['digest'], origin=source)
            except (OSError, ValueError) as exc:
                problem(group['kindle'][0], exc)
                for rel in group['kindle']:
                    path = PurePosixPath(rel)
                    excluded.update([path, path.with_suffix('.sdr')])

    def matching(path, size, digest, *, same_device=True):
        info = path.lstat()
        return (statmod.S_ISREG(info.st_mode) and (not same_device or info.st_dev == target_stat.st_dev)
                and info.st_size == size and file_digest(path) == digest)

    def copy_file(source, destination):
        report_progress('Copying to Mac' if two_way else 'Backing up', PROGRESS['done'] + 1,
                        len(reviewed_files) if reviewed_files is not None else 0)
        info = source.lstat()
        if not statmod.S_ISREG(info.st_mode) or info.st_dev != device:
            result['ignored'] += 1
            return
        digest = None
        if reviewed_files is not None:
            digest = reviewed_files.get(source.relative_to(root).as_posix())
            if digest is None or sync_digest(source, info) != digest:
                raise ValueError('The source file changed after review.')
        versioned = os.path.lexists(destination)
        if versioned:
            destination_info = destination.lstat()
            if not statmod.S_ISREG(destination_info.st_mode) or destination_info.st_dev != target_stat.st_dev:
                raise ValueError('a link or non-file occupies the backup path')
            digest = digest or file_digest(source)
            if matching(destination, info.st_size, digest):
                result['skipped'] += 1
                return
            # A stable content suffix makes changed versions idempotent as well.
            book = destination
            if two_way:
                # Library sync keeps the differing Kindle version privately. Copies
                # that earlier releases saved in the library still count.
                legacy = backup_version(destination, digest)
                if os.path.lexists(legacy) and matching(legacy, info.st_size, digest):
                    result['skipped'] += 1
                    return
                destination = sync_version_path(source.relative_to(root).as_posix(), digest, create=True)
            else:
                destination = backup_version(destination, digest)
            if os.path.lexists(destination):
                if matching(destination, info.st_size, digest, same_device=not two_way):
                    result['skipped'] += 1
                    return
                raise ValueError('a conflicting file occupies the backup version path')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.koshelf-copy-', dir=destination.parent, delete=False) as output:
                temporary = Path(output.name)
                checksum = hashlib.sha256()
                with open_regular(source) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        output.write(chunk)
                        checksum.update(chunk)
                output.flush(); os.fsync(output.fileno())
            after = source.stat()
            if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('source changed during backup')
            copied_digest = checksum.hexdigest()
            if (digest and copied_digest != digest) or temporary.stat().st_size != info.st_size or file_digest(temporary) != copied_digest:
                raise ValueError('backup verification failed')
            os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
            rename_exclusive(temporary, destination)
            temporary = None
            result['copied'] += 1
            if versioned: result['versions'] += 1
            if history:
                if versioned and two_way:
                    history.record('kept version', target, book, copied_digest, backup=destination)
                else:
                    history.record('saved version' if versioned else 'copied', target, destination, copied_digest)
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    for base, dirs, files in os.walk(root, followlinks=False, onerror=lambda exc: problem(exc.filename or root, exc)):
        base = Path(base)
        relative = base.relative_to(root)
        out = target / relative
        try:
            validate_source()
            current_target = target.lstat()
            if (not statmod.S_ISDIR(current_target.st_mode)
                    or not os.path.samestat(current_target, target_stat)):
                raise ValueError('The backup folder changed. Reconnect and try again.')
            parent = target
            for part in relative.parts:
                parent = parent / part
                try:
                    info = parent.lstat()
                except FileNotFoundError:
                    continue
                if not statmod.S_ISDIR(info.st_mode) or info.st_dev != target_stat.st_dev:
                    raise ValueError('The backup path must stay on the same filesystem without symbolic links.')
            out.mkdir(exist_ok=True)
        except (OSError, ValueError) as exc:
            problem(relative, exc); dirs[:] = []; continue
        children = []
        for name in dirs:
            child = base / name
            if system_junk(name) or relative / name in excluded or (relative / name).as_posix() in skip_folders: continue
            try:
                if child.is_symlink() or child.stat().st_dev != device: result['ignored'] += 1
                else: children.append(name)
            except OSError as exc: problem(child.relative_to(root), exc)
        dirs[:] = children
        for name in sorted(files):
            source = base / name
            if relative / name in excluded: continue
            if system_junk(name):
                continue
            if source.is_symlink() or name.startswith('.koshelf-edit-'):
                result['ignored'] += 1
                continue
            try:
                hashes = archived.get(source.relative_to(root).as_posix())
                if hashes and sync_digest(source, source.stat()) in hashes:
                    continue
                copy_file(source, out / name)
            except (OSError, ValueError) as exc: problem(source.relative_to(root), exc)
    return result


def edit_metadata(rel, title, authors, expected_size, expected_modified, genres=None, *, series=None, series_position=None, cover=None,
                  cleanup=None, expected_revision=None):
    path = library_path(rel)
    if not path.is_file() or path.suffix.lower() != '.epub': raise ValueError('Choose an existing EPUB')
    if all(field is None for field in (title, authors, genres, series, series_position, cover)):
        raise ValueError('Choose at least one metadata field to edit')
    if title is not None:
        if not isinstance(title, str) or not title.strip() or len(title) > 1000:
            raise ValueError('Enter a title (up to 1,000 characters)')
        title = title.strip()
    if authors is not None:
        if not isinstance(authors, list) or not 1 <= len(authors) <= 50 or any(
            not isinstance(author, str) or not author.strip() or len(author) > 500 for author in authors
        ):
            raise ValueError('Enter at least one author, one per line (up to 50 authors)')
        authors = [author.strip() for author in authors]
    if any(ord(c) < 32 and c not in '\t\n\r' for c in (title or '') + ''.join(authors or [])):
        raise ValueError('Names contain unsupported control characters')
    if genres is not None:
        if not isinstance(genres, list) or any(
            not isinstance(genre, str) or any(ord(char) < 32 and char not in '\t\r\n' for char in genre)
            for genre in genres
        ):
            raise ValueError('Enter genres as text, one per line')
        genres = [value for genre in genres if (value := ' '.join(genre.split()))]
    if series is not None:
        if not isinstance(series, str) or len(series) > 500 or any(ord(c) < 32 for c in series):
            raise ValueError('Enter a series name of up to 500 characters')
        series = series.strip()
    if series_position is not None:
        if not isinstance(series_position, str):
            raise ValueError('Enter a position such as 1 or 2.5')
        series_position = series_position.strip()
        if series_position and (len(series_position) > 32 or not SERIES_POSITION.fullmatch(series_position)):
            raise ValueError('Enter a position such as 1 or 2.5')
        if series == '' and series_position:
            raise ValueError('Enter a series name before setting its position')
    image = cover_upload(cover) if cover is not None else None
    original_stat = path.stat()
    if expected_revision is not None and cleanup_revision(original_stat) != expected_revision:
        raise ValueError('This book changed since preview. Scan it again.')
    if original_stat.st_size != expected_size or abs(original_stat.st_mtime - expected_modified) > 0.001:
        raise ValueError('This book changed since it was opened. Refresh and try again.')
    backups = private_state_subdirectory(JOURNAL.parent, 'metadata-backups')
    identifier = uuid.uuid4().hex
    backup = backups / (identifier + '.epub')
    # Back up to this Mac before creating a replacement on the library filesystem.
    with open_regular(path) as source, backup.open('xb') as destination:
        shutil.copyfileobj(source, destination)
        destination.flush(); os.fsync(destination.fileno())
    digest = file_digest(backup)
    if file_digest(path) != digest: raise ValueError('Book changed during backup. Refresh and try again.')
    event = {'id': identifier, 'operation': 'edit-metadata', 'library': str(ROOT), 'source': rel, 'backup': str(backup), 'sha256': digest}
    if cleanup:
        event['cleanup'] = cleanup
    journal({**event, 'status': 'started'})
    temporary = None
    try:
        with open_book(backup) as source:
            entries = source.infolist()
            check_expanded_size(source, 512 * 1024 * 1024, 512 * 1024 * 1024)
            if any(i.flag_bits & 1 for i in entries) or any(n in source.namelist() for n in ['META-INF/encryption.xml','META-INF/signatures.xml']):
                raise ValueError('Encrypted or digitally signed EPUBs are not supported by this editor')
            container = parse_xml(bounded_read(source, 'META-INF/container.xml', MAX_XML))
            package = next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('}rootfile'))
            revised = rewrite_opf(bounded_read(source, package, MAX_XML), title, authors, genres,
                                  series=series, series_position=series_position)
            cover_path = None
            if image:
                revised, cover_path = update_cover_package(revised, package, source, image[0])
            fd, filename = tempfile.mkstemp(prefix='.koshelf-edit-', suffix='.tmp', dir=path.parent)
            temporary = Path(filename)
            os.close(fd)
            with zipfile.ZipFile(temporary, 'w') as destination:
                destination.comment = source.comment
                # EPUB mimetype must be the first entry and uncompressed.
                if 'mimetype' not in source.namelist() or bounded_read(source, 'mimetype', len(EPUB_MIMETYPE)) != EPUB_MIMETYPE:
                    raise ValueError('This file is missing a valid EPUB mimetype')
                destination.writestr('mimetype', EPUB_MIMETYPE, compress_type=zipfile.ZIP_STORED)
                for info in entries:
                    if info.filename == 'mimetype': continue
                    if info.filename == package: destination.writestr(info, revised)
                    elif info.filename == cover_path: destination.writestr(info, image[1])
                    else:
                        with source.open(info) as old, destination.open(info, 'w') as new: shutil.copyfileobj(old, new)
                if cover_path and cover_path not in source.namelist():
                    destination.writestr(cover_path, image[1])
        with temporary.open('rb') as f: os.fsync(f.fileno())
        with zipfile.ZipFile(temporary) as check:
            if check.testzip() is not None: raise ValueError('Replacement EPUB failed its integrity check')
        updated = metadata(temporary, fallback_title=path.stem)
        if ((title is not None and updated['title'] != title)
                or (authors is not None and updated.get('authors') != authors)
                or (genres is not None and updated['genres'] != genres)
                or (series is not None and updated['series'] != series)
                or (series != '' and series_position is not None and updated['seriesPosition'] != series_position)):
            raise ValueError('Replacement metadata could not be verified')
        if image and updated['cover'] != image:
            raise ValueError('The replacement cover could not be verified.')
        library_path(rel)
        now = path.stat()
        if cleanup_revision(now) != cleanup_revision(original_stat) or file_digest(path) != digest:
            raise ValueError('Book changed during editing. Nothing was replaced; refresh and try again.')
        os.chmod(temporary, original_stat.st_mode & 0o777)
        os.replace(temporary, path)  # Same-filesystem replacement only after validation.
        temporary = None
    except Exception as exc:
        try: journal({**event, 'status': 'failed', 'error': str(exc)})
        except OSError: pass
        raise ValueError('Metadata was not saved. The original book was not replaced. ' + str(exc)) from exc
    finally:
        if temporary is not None:
            try: temporary.unlink()
            except OSError: pass
    stat = path.stat()
    for key in list(CACHE):
        if key[0] == str(path): del CACHE[key]
    CACHE[(str(path), stat.st_size, stat.st_mtime_ns)] = {**updated, 'cover': None}
    warning = None
    try: journal({**event, 'status': 'completed'})
    except OSError: warning = 'Metadata saved; the operation log could not be updated.'
    return {**{field: updated[field] for field in ('title', 'author', 'authors', 'genres', 'series', 'seriesPosition')},
            'hasCover': updated.get('hasCover', False),
            'size': stat.st_size,
            'modified': stat.st_mtime, 'backup': str(backup), 'warning': warning}


def remove_folder(folder_rel):
    """Promote every direct child, including hidden files and .sdr folders.

    Nested directories move intact. Only the emptied container is removed.
    """
    folder = library_path(folder_rel)
    if not folder.is_dir(): raise ValueError('That folder no longer exists')
    parent = folder.parent
    entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
    names = set()
    for child in entries:
        if child.is_symlink(): raise ValueError('This folder contains symbolic links. Organize it in Finder instead.')
        if child.name.casefold() in names: raise ValueError('This folder contains names differing only by case. Rename them first.')
        names.add(child.name.casefold())
        assert_free(parent / child.name)
        # Refuse to accidentally attach an unrelated KOReader companion folder.
        if child.suffix.lower() == '.epub': assert_free(parent / child.with_suffix('.sdr').name)
        if child.suffix.lower() == '.sdr': assert_free(parent / child.with_suffix('.epub').name)
        if child.stat().st_dev != parent.stat().st_dev: raise ValueError('Contents must be on the same filesystem')
    pairs = [(child, parent / child.name) for child in entries]
    event = {'id': uuid.uuid4().hex, 'operation': 'remove-folder', 'library': str(ROOT),
             'source': folder_rel, 'destination': parent.relative_to(ROOT).as_posix(),
             'entries': [p.name for p in entries]}
    journal({**event, 'status': 'started'})
    completed = []
    try:
        for source, destination in pairs:
            rename_exclusive(source, destination)
            completed.append((source, destination))
        folder.rmdir()  # Never recursively delete; unexpected new contents cause rollback.
    except Exception as exc:
        failures = []
        for source, destination in reversed(completed):
            try: rename_exclusive(destination, source)
            except Exception: failures.append(f'{destination} → {source}')
        try: journal({**event, 'status': 'recovery-needed' if failures else 'rolled-back', 'recovery': failures})
        except OSError: pass
        if failures: raise ValueError('Folder removal interrupted. Reconnect the device; these paths need recovery: ' + '; '.join(failures)) from exc
        raise ValueError('Folder could not be removed; completed moves were rolled back. ' + str(exc)) from exc
    # Preserve metadata cached for books inside the promoted directory tree.
    for key in list(CACHE):
        try: relative = Path(key[0]).relative_to(folder)
        except ValueError: continue
        CACHE[(str(parent / relative), key[1], key[2])] = CACHE.pop(key)
    warning = None
    try: journal({**event, 'status': 'completed'})
    except OSError: warning = 'Folder removed and contents preserved, but the local operation log could not be updated.'
    return {'removed': folder_rel, 'parent': '' if parent == ROOT else parent.relative_to(ROOT).as_posix(), 'moved': len(entries), 'warning': warning}


def move_book(source_rel, folder_rel):
    source = library_path(source_rel)
    folder = library_path(folder_rel, allow_root=True)
    return move_epub(source, folder / source.name, ROOT)


def move_epub(source, destination, base):
    folder = destination.parent
    if not source.is_file() or source.suffix.lower() != '.epub': raise ValueError('The EPUB no longer exists')
    if not folder.is_dir(): raise ValueError('Destination folder no longer exists')
    if source == destination: raise ValueError('This book is already in that folder')
    source_stat = source.stat()
    if source_stat.st_dev != folder.stat().st_dev: raise ValueError('Books can only move within the same filesystem')
    assert_free(destination)
    sidecar = source.with_suffix('.sdr')
    target_sidecar = destination.with_suffix('.sdr')
    # An unrelated sidecar at the destination must never become associated with this book.
    assert_free(target_sidecar)
    if sidecar.exists() and any(p.stem.casefold() == destination.stem.casefold() and p.is_file() for p in folder.iterdir()):
        raise ValueError('Another format would share the companion folder at the destination.')
    if sidecar.is_symlink(): raise ValueError('The companion folder is a symbolic link; move this book in KOReader')
    pairs = []
    if sidecar.exists():
        if not sidecar.is_dir(): raise ValueError('The companion .sdr path is not a folder')
        for sibling in source.parent.iterdir():
            if sibling != source and sibling.is_file() and sibling.stem.casefold() == source.stem.casefold():
                raise ValueError('Another format shares this book’s companion folder. Move this book in KOReader.')
        pairs.append((sidecar, target_sidecar))
    pairs.append((source, destination))
    source_rel = source.relative_to(base).as_posix()
    folder_rel = '' if folder == base else folder.relative_to(base).as_posix()
    event = {'id': uuid.uuid4().hex, 'library': str(base), 'source': source_rel,
             'destination': destination.relative_to(base).as_posix(), 'sidecar': bool(sidecar.exists())}
    journal({**event, 'status': 'started'})
    completed = []
    try:
        for old, new in pairs:
            rename_exclusive(old, new)
            completed.append((old, new))
    except Exception as exc:
        failures = []
        for old, new in reversed(completed):
            try: rename_exclusive(new, old)
            except Exception: failures.append(f'{new} → {old}')
        status = 'recovery-needed' if failures else 'rolled-back'
        try: journal({**event, 'status': status, 'recovery': failures})
        except OSError: pass
        if failures:
            raise ValueError('Move interrupted. Reconnect the device; these paths need recovery: ' + '; '.join(failures)) from exc
        raise ValueError('Move could not finish; completed steps were rolled back. ' + str(exc)) from exc
    # Moving on one filesystem preserves bytes and timestamps. Reuse the cached metadata.
    old_key = (str(source), source_stat.st_size, source_stat.st_mtime_ns)
    if old_key in CACHE:
        CACHE[(str(destination), source_stat.st_size, source_stat.st_mtime_ns)] = CACHE.pop(old_key)
    warning = None
    try: journal({**event, 'status': 'completed'})
    except OSError: warning = 'Book moved, but the local operation log could not be updated.'
    return {'source': source_rel, 'path': event['destination'], 'folder': folder_rel, 'withSidecar': event['sidecar'], 'warning': warning}


APPLEDOUBLE_MAGIC = b'\x00\x05\x16\x07'
LEFTOVER_BACKUP = re.compile(r'^(?P<stem>.+) \(backup (?P<hash>[0-9a-f]{16})\)(?P<suffix>\.[^./]+)$')
DATALESS = getattr(statmod, 'SF_DATALESS', 0x40000000)


def cleanup_libraries():
    """The Mac library and, when configured, the iCloud Drive folder."""
    roots = {'mac': cleanup_root()}
    status = icloud_status()
    if status['available']:
        roots['icloud'] = icloud_folder(status['path'])
    return roots


def sync_leftovers(roots):
    """Find files that earlier releases left behind during Kindle sync.

    - AppleDouble files (``._Name``) copied from the Kindle's FAT drive. They
      only hold Finder metadata and are recognized by their file signature.
    - ``Name (backup <hash>)`` copies whose contents match the hash in the name
      and whose original sits beside them. A copy without its original is left
      alone, because it may be the only copy of that book.
    """
    found, kept = [], []
    for key, root in roots.items():
        device = root.lstat().st_dev
        for base, dirs, files in os.walk(root, followlinks=False):
            base = Path(base)
            dirs[:] = [name for name in dirs if not (base / name).is_symlink()
                       and (base / name).lstat().st_dev == device and not system_junk(name)]
            for name in files:
                path = base / name
                try:
                    info = path.lstat()
                except OSError:
                    continue
                if not statmod.S_ISREG(info.st_mode) or info.st_dev != device or info.st_flags & DATALESS:
                    continue
                relative = path.relative_to(root).as_posix()
                if name.startswith('._'):
                    try:
                        with open_regular(path) as stream:
                            if stream.read(4) != APPLEDOUBLE_MAGIC:
                                continue
                    except (OSError, ValueError):
                        continue
                    found.append({'library': key, 'path': relative, 'kind': 'appledouble',
                                  'size': info.st_size, 'identity': [info.st_ino, info.st_mtime_ns]})
                    continue
                match = LEFTOVER_BACKUP.match(name)
                if not match:
                    continue
                original = path.with_name(match['stem'] + match['suffix'])
                digest = file_digest(path)
                if not digest.startswith(match['hash']):
                    continue
                if original.is_symlink() or not original.is_file():
                    kept.append({'library': key, 'path': relative})
                    continue
                found.append({'library': key, 'path': relative, 'kind': 'backup', 'size': info.st_size,
                              'digest': digest, 'original': original.relative_to(root).as_posix(),
                              'identity': [info.st_ino, info.st_mtime_ns]})
    found.sort(key=lambda item: (item['library'], item['kind'], item['path']))
    return found, kept


def move_verified(source, destination, digest):
    """Move a file into private state, verifying the copy before removing the source."""
    if os.path.lexists(destination):
        if destination.is_symlink() or not destination.is_file() or file_digest(destination) != digest:
            raise ValueError('A different file already occupies the recovery location.')
        source.unlink()
        return
    with tempfile.NamedTemporaryFile(prefix='.koshelf-move-', dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            with open_regular(source) as stream:
                shutil.copyfileobj(stream, output)
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if file_digest(temporary) != digest:
            raise ValueError('The recovery copy failed verification.')
        rename_exclusive(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    source.unlink()


def clean_sync_leftovers(review=None):
    """Preview, then move sync leftovers out of the libraries into private state."""
    roots = cleanup_libraries()
    found, kept = sync_leftovers(roots)
    identities = {key: [root.lstat().st_dev, root.lstat().st_ino] for key, root in roots.items()}
    token = hashlib.sha256(json.dumps([identities, found]).encode()).hexdigest()
    if review is None:
        return {'review': token, 'items': found, 'kept': kept,
                'libraries': {key: str(root) for key, root in roots.items()}}
    if review != token:
        raise ValueError('The libraries changed during review. Open the cleanup again.')
    run = uuid.uuid4().hex
    result = {'moved': 0, 'failed': 0, 'errors': [], 'recovery': None}
    for item in found:
        try:
            root = roots[item['library']]
            path = backup_alignment_path(root, item['path'])
            info = path.lstat()
            if not statmod.S_ISREG(info.st_mode) or [info.st_ino, info.st_mtime_ns] != item['identity']:
                raise ValueError('The file changed during review.')
            digest = file_digest(path)
            if item['kind'] == 'backup' and item['library'] == 'mac':
                # Keep Mac-side Kindle versions where sync looks for them.
                destination = sync_version_path(item['original'], digest, create=True)
            else:
                parts = PurePosixPath(item['path']).parts
                folder = private_state_subdirectory(JOURNAL.parent, 'library-cleanup', run, item['library'], *parts[:-1])
                destination = folder / parts[-1]
                result['recovery'] = str(JOURNAL.parent / 'library-cleanup' / run)
            if item['kind'] == 'backup' and digest != item['digest']:
                raise ValueError('The file changed during review.')
            move_verified(path, destination, digest)
            result['moved'] += 1
        except (OSError, ValueError, KeyError) as exc:
            result['failed'] += 1
            if len(result['errors']) < 5:
                result['errors'].append(f"{item['path']}: {exc}")
    journal({'operation': 'clean-sync-leftovers', 'library': str(roots['mac']), 'run': run,
             'moved': result['moved'], 'failed': result['failed'], 'status': 'completed'})
    return result


class LocalServer(ThreadingHTTPServer):
    # Wait for active file requests on shutdown instead of abandoning a move.
    daemon_threads = False
    request_slots = 32
    request_idle_timeout = 10
    request_deadline = 45

    def __init__(self, address, handler):
        self.session_secret = secrets.token_hex(32)
        self.slots = threading.BoundedSemaphore(self.request_slots)
        super().__init__(address, handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.request_idle_timeout)
        return request, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def font_library(token=None):
    kindle = kindle_documents()
    if token is not None and (kindle is None or kindle_fonts.connection(kindle) != token):
        raise ValueError('The Kindle changed or disconnected. Reconnect and refresh the font list.')
    return kindle


def usb_kindle_status(documents):
    if documents is None:
        return None
    volume = documents.parent
    return {'name': volume.name, 'device': kindle_delivery.device_token(documents),
            'mode': 'koreader' if has_koreader(volume) else 'kindle'}


def reviewed_kindle_documents(token):
    documents = kindle_documents()
    if documents is None or kindle_delivery.device_token(documents) != token:
        raise ValueError('The Kindle changed or disconnected. Reconnect and review the action again.')
    return documents


def deliver_to_kindle(body):
    def connected_documents():
        return reviewed_kindle_documents(body.get('device'))

    if not DELIVERY_LOCK.acquire(blocking=False):
        raise ValueError('Another Kindle conversion is already running.')
    try:
        with LOCK:
            if str(mac_library().resolve()) != body.get('library') or ROOT != mac_library().resolve() or mac_library().is_symlink():
                raise ValueError('Open Mac Library before sending a book.')
            source = library_path(body.get('path'))
            if source.suffix.lower() != '.epub' or not source.is_file():
                raise ValueError('Choose an EPUB from Mac Library.')
            revision = kindle_delivery.source_revision(source)
            documents = connected_documents()
            target = kindle_delivery.destination(documents, source)
            if target.exists() or target.is_symlink():
                return {'result': 'skipped', 'name': target.name}
            executable = kindle_delivery.converter()
            if not executable:
                raise ValueError('The KoShelf converter is missing. Reinstall KoShelf or build the converter from source.')

        with tempfile.TemporaryDirectory(prefix='koshelf-kindle-') as temporary:
            converted = Path(temporary) / 'book.azw3'
            kindle_delivery.convert(source, converted, executable)
            with LOCK:
                if library_path(body.get('path')) != source or kindle_delivery.source_revision(source) != revision:
                    raise ValueError('The source EPUB changed during conversion. Try again.')
                connected_documents()
                if target.exists() or target.is_symlink():
                    return {'result': 'skipped', 'name': target.name}
                kindle_delivery.copy_to_device(converted, target, rename_exclusive, connected_documents)
        return {'result': 'sent', 'name': target.name}
    finally:
        DELIVERY_LOCK.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def setup(self):
        super().setup()
        self.ingress_timer = threading.Timer(self.server.request_deadline, self.stop_reading)
        self.ingress_timer.daemon = True
        self.ingress_timer.start()

    def stop_reading(self):
        try:
            self.connection.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def finish(self):
        self.ingress_timer.cancel()
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send(self, data, mime='application/json', status=200, cache='no-store'):
        if not isinstance(data, bytes): data = json.dumps(data, separators=(',', ':')).encode()
        compress = (mime == 'application/json' and len(data) > 16384
                    and 'gzip' in self.headers.get('Accept-Encoding', ''))
        if compress:
            data = gzip.compress(data, compresslevel=5)
        self.send_response(status)
        self.send_header('Content-Type', mime)
        if compress:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; connect-src 'self'; img-src 'self' data: blob:; font-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
        # Keep referrers local while preserving WebKit's Origin on local POSTs.
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(data)

    def request_origin(self):
        hosts = self.headers.get_all('Host', [])
        if len(hosts) == 1 and hosts[0] in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            return 'http://' + hosts[0]

    def is_authorized(self, origin):
        origins = self.headers.get_all('Origin', [])
        if not origin or ((origins or self.command == 'POST') and origins != [origin]):
            return False
        fetch_sites = self.headers.get_all('Sec-Fetch-Site', [])
        if fetch_sites and fetch_sites != ['same-origin']:
            return False
        keys = self.headers.get_all('Authorization', [])
        if len(keys) != 1 or not keys[0].startswith('Bearer '):
            return False
        secret = keys[0][7:]
        return local_session.valid_secret(secret) and secrets.compare_digest(secret, self.server.session_secret)

    def read_body(self, maximum, destination=None):
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,10}', lengths[0]) or self.headers.get_all('Transfer-Encoding'):
            raise ValueError('Invalid request size.')
        length = int(lengths[0])
        if not 0 < length <= maximum:
            raise ValueError('Invalid request size.')
        data = bytearray() if destination is None else None
        remaining = length
        while remaining:
            chunk = self.rfile.read1(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError('The upload was interrupted.')
            if data is None:
                destination.write(chunk)
            else:
                data.extend(chunk)
            remaining -= len(chunk)
        self.ingress_timer.cancel()
        return length if data is None else data

    def do_GET(self):
        self.ingress_timer.cancel()
        origin = self.request_origin()
        if not origin:
            return self.send({'error': 'Invalid host'}, status=403)
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/api/identity':
            identity = {'app': 'KoShelf', 'version': 3}
            challenge = urllib.parse.parse_qs(url.query).get('challenge', [])
            if len(challenge) == 1 and local_session.valid_secret(challenge[0]):
                identity['proof'] = local_session.proof(self.server.session_secret, self.server.server_port, challenge[0])
            return self.send(identity)
        if url.path.startswith('/api/') and not self.is_authorized(origin):
            return self.send({'error': 'Open KoShelf to start a new local session.'}, status=403)
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/koshelf-icon.png': ('koshelf-icon.png', 'image/png')}
        if url.path in assets:
            name, mime = assets[url.path]
            return self.send((STATIC / name).read_bytes(), mime)
        if url.path == '/api/kindle-delivery':
            try:
                documents = kindle_documents()
                return self.send({'device': kindle_delivery.device_token(documents) if documents else None,
                                  'name': documents.parent.name if documents else None,
                                  'converter': bool(kindle_delivery.converter())})
            except (OSError, ValueError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path in {'/api/fonts', '/api/font-preview'}:
            try:
                with LOCK:
                    if url.path == '/api/font-preview':
                        query = urllib.parse.parse_qs(url.query)
                        kindle = font_library(query['device'][0])
                        path = kindle_fonts.font_path(kindle, query['reader'][0], query['path'][0])
                        if kindle_fonts.revision(path) != query['version'][0]:
                            raise ValueError('This font changed. Refresh the list.')
                        data = kindle_fonts.read_font(path)
                        kindle_fonts.font_details(data)
                        return self.send(data, 'font/otf' if path.suffix.lower() == '.otf' else 'font/ttf')
                    kindle = font_library()
                    if not kindle:
                        return self.send({'device': None, 'readers': {}})
                    koreader = kindle.parent / 'koreader'
                    readers = ['kindle', 'koreader'] if koreader.is_dir() and not koreader.is_symlink() else ['kindle']
                    return self.send({'device': kindle_fonts.connection(kindle),
                                      'readers': {reader: kindle_fonts.entries(kindle, reader) for reader in readers}})
            except (OSError, ValueError, KeyError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path == '/api/status':
            # The status poll should never queue behind a long sync or import.
            if not LOCK.acquire(timeout=0.1):
                return self.send({'busy': True})
            try:
                try:
                    state = sync_connection()
                    if LOCAL_FIRST:
                        kindle = kindle_library()
                        state['kindle'] = str(kindle) if kindle else None
                        documents = kindle.parent / 'documents' if kindle else kindle_documents()
                        state['usbKindle'] = usb_kindle_status(documents)
                finally:
                    LOCK.release()
                return self.send(state)
            except (OSError, ValueError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path == '/api/warm-kindle':
            try:
                kindle = kindle_library() if LOCAL_FIRST else None
                if kindle:
                    warm_kindle(kindle)
                return self.send({})
            except (OSError, ValueError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path == '/api/sync-status':
            try:
                with COMPARE_LOCK:
                    state = kindle_status(kindle_library() if LOCAL_FIRST else None)
                return self.send(state)
            except (OSError, ValueError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path == '/api/progress':
            return self.send(dict(PROGRESS))
        if url.path == '/api/description':
            # The shelf list omits descriptions; the detail view reads one on demand.
            params = urllib.parse.parse_qs(url.query)
            try:
                if params.get('root') != [str(ROOT)]:
                    raise ValueError('The shelf changed.')
                path = library_path(params.get('path', [''])[0])
                info = path.lstat()
                if path.suffix.lower() != '.epub' or not statmod.S_ISREG(info.st_mode):
                    raise ValueError('Choose an EPUB.')
                meta = CACHE.get((str(path), info.st_size, info.st_mtime_ns)) or metadata(path, load_cover=False)
                return self.send({'description': meta.get('description', '')})
            except (OSError, ValueError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path == '/api/cover':
            # Covers are read-only, so they do not wait for library operations to finish.
            root = ROOT
            if root is None or not root.is_dir(): return self.send({}, status=404)
            params = urllib.parse.parse_qs(url.query)
            if 'root' in params and params['root'][0] != str(root):
                return self.send({}, status=404)
            rel = params.get('path', [''])[0]
            try:
                p = library_path(rel)
                s = p.stat()
                if p.suffix.lower() != '.epub' or not statmod.S_ISREG(s.st_mode):
                    raise ValueError()
                key = (str(p), s.st_size, s.st_mtime_ns)
                m = CACHE.get(key)
                if m and m.get('hasCover') and (image := cached_cover(p, s)):
                    # The URL names the file's size and modification time, so it never goes stale.
                    return self.send(image[1], image[0], cache='private, max-age=31536000, immutable')
            except (OSError, ValueError): pass
            return self.send({}, status=404)
        with LOCK:
            if url.path == '/api/storage': return self.send(storage_usage())
            if url.path == '/api/library':
                known = urllib.parse.parse_qs(url.query).get('known', [None])[0]
                try: return self.send(library_snapshot(known))
                except (OSError, ValueError) as exc: return self.send({'error': str(exc)}, status=400)
            if url.path == '/api/sync-history':
                try:
                    return self.send(sync_history())
                except OSError as exc:
                    return self.send({'error': str(exc)}, status=400)
            if url.path in {'/api/icloud-status', '/api/icloud-history'}:
                try:
                    data = icloud_status() if url.path == '/api/icloud-status' else icloud_history()
                    return self.send(data)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    return self.send({'error': str(exc)}, status=400)
            if url.path == '/api/duplicate-archive':
                try:
                    return self.send(archived_duplicates())
                except (ValueError, OSError, KeyError) as exc:
                    return self.send({'error': str(exc)}, status=400)
            if url.path == '/api/duplicates':
                try:
                    root = library_path('', allow_root=True)
                    if urllib.parse.parse_qs(url.query).get('root') != [str(root)]:
                        raise ValueError('The shelf changed. Open Duplicate finder again.')
                    return self.send(find_duplicates(root))
                except (OSError, ValueError) as exc:
                    return self.send({'error': str(exc)}, status=400)
        self.send({}, status=404)

    def do_POST(self):
        global ROOT
        if not self.is_authorized(self.request_origin()):
            return self.send({'error': 'Open KoShelf to start a new local session.'}, status=403)
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/api/kindle-delivery' and self.headers.get('Content-Type') == 'application/json':
            try:
                body = json.loads(self.read_body(8191))
                if not isinstance(body, dict) or any(not isinstance(body.get(key), str)
                                                     for key in ('device', 'library', 'path')):
                    raise ValueError('Invalid Kindle delivery request.')
                return self.send(deliver_to_kindle(body))
            except (OSError, ValueError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path in {'/api/font-install', '/api/font-remove'}:
            try:
                kind = self.headers.get('Content-Type')
                if url.path == '/api/font-install' and kind == 'application/octet-stream':
                    query = urllib.parse.parse_qs(url.query)
                    data = bytes(self.read_body(kindle_fonts.MAX_FONT))
                    with LOCK:
                        kindle = font_library(query['device'][0])
                        result = kindle_fonts.install(kindle, query['reader'][0], query['name'][0], data, rename_exclusive)
                        return self.send({'result': result})
                if url.path == '/api/font-remove' and kind == 'application/json':
                    body = json.loads(self.read_body(8191))
                    if not isinstance(body, dict) or any(not isinstance(body.get(key), str)
                                                         for key in ('device', 'reader', 'path', 'version')):
                        raise ValueError('Invalid font removal request.')
                    with LOCK:
                        kindle = font_library(body['device'])
                        backup = kindle_fonts.remove(kindle, body['reader'], body['path'], body['version'],
                                                     JOURNAL.parent / 'removed-fonts')
                        return self.send({'backup': str(backup)})
                raise ValueError('Choose a TTF, OTF, or TTC file up to 32 MB.')
            except (OSError, ValueError, KeyError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if url.path == '/api/import-epub' and self.headers.get('Content-Type') == 'application/octet-stream':
            try:
                with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as upload:
                    size = self.read_body(MAX_IMPORT, upload)
                    upload.seek(0)
                    with LOCK:
                        return self.send(import_epub(urllib.parse.parse_qs(url.query, keep_blank_values=True), upload, size))
            except (OSError, ValueError, KeyError) as exc:
                return self.send({'error': str(exc)}, status=400)
        if self.path not in {'/api/library', '/api/folders', '/api/move', '/api/eject', '/api/remove-folder', '/api/metadata', '/api/backup', '/api/sync', '/api/send-to-kindle', '/api/duplicate-archive', '/api/import-preview', '/api/sync-undo', '/api/sync-leftovers', '/api/genre-cleanup-preview', '/api/metadata-cleanup-apply', '/api/icloud-setup', '/api/icloud-sync', '/api/epub-split', '/api/reading-status', '/api/kindle-setup'} or self.headers.get('Content-Type') != 'application/json':
            return self.send({'error': 'Unsupported request'}, status=400)
        try:
            limit = 512 * 1024 if self.path in {'/api/sync', '/api/genre-cleanup-preview', '/api/metadata-cleanup-apply', '/api/icloud-sync'} else 8192
            if self.path == '/api/metadata':
                limit += 4 * ((MAX_COVER + 2) // 3)
            body = json.loads(self.read_body(limit - 1))
            if not isinstance(body, dict): raise ValueError('Invalid request')
            with LOCK:
                report_progress()  # Each operation starts with a clean transfer count.
                if self.path == '/api/library':
                    select_library(body['path'])
                    return self.send(library_snapshot())
                if str(ROOT) != body.get('library'):
                    raise ValueError('Library changed. Refresh before making changes.')
                if self.path == '/api/kindle-setup':
                    documents = reviewed_kindle_documents(body.get('device'))
                    if not has_koreader(documents.parent):
                        raise ValueError('KoShelf could not find KOReader on this Kindle.')
                    folder = documents.parent / 'books'
                    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
                        raise ValueError('A file already uses the name books on this Kindle.')
                    folder.mkdir(exist_ok=True)
                    return self.send({'kindle': str(folder)})
                if self.path == '/api/eject' and body.get('usbDevice') and not body.get('kindle'):
                    documents = reviewed_kindle_documents(body['usbDevice'])
                    return self.send(eject_library(documents))
                if self.path == '/api/icloud-setup':
                    return self.send(configure_icloud(body['path']))
                if self.path == '/api/icloud-sync':
                    return self.send(run_icloud_sync(body))
                if self.path == '/api/reading-status':
                    return self.send(set_reading_status(body['path'], body['status']))
                if self.path == '/api/epub-split':
                    if library_path('', allow_root=True) != mac_library().resolve():
                        raise ValueError('Open Mac Library to split an EPUB.')
                    path = library_path(body['path'])
                    if path.suffix.lower() != '.epub' or not path.is_file():
                        raise ValueError('Choose an EPUB to split.')
                    result = epub_split.split(path, body, rewrite_opf, rename_exclusive)
                    if 'folder' in result:
                        result['folder'] = (path.parent / result['folder']).relative_to(ROOT).as_posix()
                    return self.send(result)
                if self.path == '/api/metadata-cleanup-apply':
                    return self.send(apply_metadata_cleanup(body))
                if self.path == '/api/genre-cleanup-preview':
                    return self.send(genre_cleanup_preview(body))
                if self.path == '/api/sync-leftovers':
                    return self.send(clean_sync_leftovers(body.get('review')))
                if self.path == '/api/sync-undo':
                    return self.send(undo_sync_action(body['run'], body['action']))
                if self.path == '/api/import-preview':
                    path = import_destination(body['folder'], body['name'], body['size'], body['digest'])
                    return self.send({'path': path.relative_to(ROOT).as_posix()})
                if self.path in {'/api/sync', '/api/send-to-kindle'} or (self.path == '/api/eject' and body.get('kindle')):
                    kindle = kindle_library() if LOCAL_FIRST else None
                    if kindle is None or str(kindle) != body.get('kindle'):
                        raise ValueError('The Kindle changed or disconnected. Reconnect and try again.')
                    if self.path == '/api/eject':
                        # Stop background reads first so eject never waits on a full comparison.
                        WARM_STOP.set()
                        DIGESTS.cancel.set()
                        try:
                            with COMPARE_LOCK, WARM_READ_LOCK:
                                return self.send(eject_library(kindle))
                        finally:
                            DIGESTS.cancel.clear()
                            WARM_STOP.clear()
                    if self.path == '/api/send-to-kindle':
                        return self.send(send_to_kindle(kindle, body.get('review')))
                    choice = body.get('choice')
                    try:
                        return self.send(sync_libraries(kindle, choice, body.get('review'), body.get('useMac')))
                    finally:
                        DIGESTS.save()
                if self.path == '/api/eject':
                    return self.send(eject_library())
                if self.path == '/api/backup':
                    return self.send(backup_library(choice=body.get('choice'), review=body.get('review')))
                if self.path == '/api/duplicate-archive':
                    return self.send(archive_duplicate(body['keep'], body['remove']))
                if self.path == '/api/metadata':
                    return self.send(edit_metadata(body['path'], body.get('title'), body.get('authors'), body['size'], body['modified'], body.get('genres'),
                                                   series=body.get('series'), series_position=body.get('seriesPosition'),
                                                   cover=body.get('cover')))
                if self.path == '/api/remove-folder':
                    return self.send(remove_folder(body['folder']))
                if self.path == '/api/folders':
                    folder = create_folder(body['parent'], body['name'])
                    return self.send({'folder': folder})
                return self.send(move_book(body['source'], body['folder']))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.send({'error': str(exc)}, status=400)


def main():
    global ROOT, LOCAL_FIRST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, help='Books folder, on the Kindle or your computer')
    parser.add_argument('--port', type=int, default=0, help='Local port for the interface (default: any free port)')
    parser.add_argument('--open', action='store_true', help='Open your default browser')
    parser.add_argument('--session-pipe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    LOCAL_FIRST = args.library is None
    if args.library:
        if args.library.is_dir(): select_library(args.library)
        else: ROOT = args.library.expanduser().absolute()
    server = LocalServer(('127.0.0.1', args.port), Handler)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    ticket = None
    try:
        if not args.session_pipe:
            ticket = local_session.publish(server.server_port, server.session_secret)
        if args.open:
            local_session.forget_redirect_later(local_session.open_browser(server.server_port, server.session_secret))
        print(f'KoShelf: http://127.0.0.1:{server.server_port}', flush=True)
        if args.session_pipe:
            print('KoShelf session: ' + local_session.session_url(server.server_port, server.session_secret), flush=True)
        else:
            print('Local library organizer. Press Control-C to stop.', flush=True)
        server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        try:
            if ticket is not None:
                local_session.forget(server.server_port, ticket)
        finally:
            server.server_close()

if __name__ == '__main__': main()
