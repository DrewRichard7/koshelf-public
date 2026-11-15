"""Read and manage user fonts on a mounted Kindle."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import tempfile
import uuid
from library_fs import open_regular, private_state_subdirectory

MAX_FONT = 32 * 1024 * 1024
SUFFIXES = {'.ttf', '.otf', '.ttc'}
# KOReader ships these resources. Keep their removal outside the font manager.
BUNDLED = {'droid', 'freefont', 'host', 'hyperreadable', 'libron', 'nerdfonts',
           'noto', 'readerly', 'source', 'urw'}


def connection(library):
    if library is None:
        return None
    volume = library.parent
    info = volume.stat()
    return f'{volume}:{info.st_dev}:{info.st_ino}'


def directory(library, reader):
    if reader not in {'kindle', 'koreader'}:
        raise ValueError('Choose a reader.')
    volume = library.parent
    parent = volume if reader == 'kindle' else volume / 'koreader'
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError('KOReader is not installed on this Kindle.')
    root = parent / 'fonts'
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError('The font folder could not be opened safely.')
    return root


def font_details(data):
    """Read the first face's OpenType name table with bounded offsets."""
    magic = data[:4]
    try:
        start = struct.unpack_from('>I', data, 12)[0] if magic == b'ttcf' else 0
        if data[start:start + 4] not in {b'\x00\x01\x00\x00', b'OTTO', b'true'}:
            raise ValueError
        count = struct.unpack_from('>H', data, start + 4)[0]
        tables = {}
        for index in range(count):
            tag, _, offset, size = struct.unpack_from('>4sIII', data, start + 12 + index * 16)
            if offset + size > len(data):
                raise ValueError
            tables[tag] = (offset, size)
        if not {b'head', b'cmap', b'name'} <= tables.keys():
            raise ValueError
        offset, size = tables[b'name']
        names = memoryview(data)[offset:offset + size]
        _, records, strings = struct.unpack_from('>HHH', names)
        if not 6 + records * 12 <= strings <= len(names):
            raise ValueError
        values = {}
        for index in range(records):
            platform, _, lang, key, length, position = struct.unpack_from('>6H', names, 6 + index * 12)
            if platform not in (0, 1, 3) or key not in (1, 2, 16, 17):
                continue
            position += strings
            if position + length > len(names):
                continue
            rank = 2 if platform == 3 and lang == 0x409 else int(platform != 1)
            if rank <= values.get(key, (-1, ''))[0]:
                continue
            # A hostile font can reuse one giant string in thousands of name
            # records. Only decode what can be displayed by the font manager.
            encoding = 'mac_roman' if platform == 1 else 'utf-16-be'
            label = names[position:position + min(length, 480)].tobytes().decode(encoding, errors='replace').strip()
            if label:
                values[key] = (rank, label[:120])
        family = values.get(16) or values.get(1)
        style = values.get(17) or values.get(2)
        if family:
            return family[1], style[1] if style else 'Regular', b'fvar' in tables
    except (struct.error, ValueError):
        pass
    raise ValueError('This file does not contain a readable OpenType font.')


def revision(path):
    info = path.stat()
    return f'{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}'


def font_files(root):
    for dirpath, dirs, names in os.walk(root):
        folder = Path(dirpath)
        dirs[:] = [name for name in dirs if not name.startswith('.') and not (folder / name).is_symlink()]
        for name in names:
            path = folder / name
            if not name.startswith('.') and path.suffix.lower() in SUFFIXES and not path.is_symlink() and path.is_file():
                yield path


def entries(library, reader):
    root = directory(library, reader)
    result = []
    for path in font_files(root):
        rel = path.relative_to(root)
        version = revision(path)
        try:
            family, style, variable = font_details(read_font(path))
            readable = True
        except (ValueError, OSError):
            family, style, variable, readable = path.stem, 'Unreadable file', False, False
        result.append({'path': rel.as_posix(), 'family': family, 'style': style, 'variable': variable,
                       'version': version, 'preview': readable and path.suffix.lower() != '.ttc',
                       'removable': reader == 'kindle' or rel.parts[0].lower() not in BUNDLED})
    return sorted(result, key=lambda item: (item['family'].casefold(), item['style'], item['path']))


def read_font(path):
    with open_regular(path) as stream:
        data = stream.read(MAX_FONT + 1)
    if len(data) > MAX_FONT:
        raise ValueError('Font files must be 32 MB or smaller.')
    return data


def font_path(library, reader, name):
    root = directory(library, reader)
    rel = PurePosixPath(name)
    if rel.is_absolute() or not rel.parts or any(part.startswith('.') for part in rel.parts) or rel.suffix.lower() not in SUFFIXES:
        raise ValueError('Choose a font file.')
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Symbolic links cannot be managed.')
    if not path.is_file():
        raise ValueError('The font file is no longer on the Kindle.')
    return path


def install(library, reader, name, data, rename):
    if '/' in name or '\\' in name or ':' in name or name.startswith('.') or any(ord(c) < 32 for c in name):
        raise ValueError('Choose a font with a valid filename.')
    root = directory(library, reader)
    path = root / name
    if path.suffix.lower() not in SUFFIXES:
        raise ValueError('Choose a TTF, OTF, or TTC file.')
    font_details(data)
    digest = hashlib.sha256(data).digest()
    for candidate in font_files(root):
        if candidate.stat().st_size == len(data) and hashlib.sha256(read_font(candidate)).digest() == digest:
            return 'Already installed'
    if path.exists() or path.is_symlink():
        raise ValueError('A different font already uses this filename. Rename the file before importing.')
    root.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, prefix='.koshelf-font-', delete=False) as stream:
        temporary = root / os.path.basename(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            rename(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return 'Installed'


def remove(library, reader, name, version, archive):
    path = font_path(library, reader, name)
    if reader == 'koreader' and PurePosixPath(name).parts[0].lower() in BUNDLED:
        raise ValueError('This font is bundled with KOReader.')
    if revision(path) != version:
        raise ValueError('This font changed. Refresh the list and try again.')
    folder = private_state_subdirectory(archive.parent, archive.name, reader, uuid.uuid4().hex)
    backup = folder / path.name
    with open_regular(path) as source, backup.open('xb') as target:
        shutil.copyfileobj(source, target)
        target.flush()
        os.fsync(target.fileno())
    if revision(path) != version or read_font(path) != read_font(backup):
        raise ValueError('The font changed while being backed up.')
    path.unlink()
    return backup
