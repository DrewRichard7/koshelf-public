"""Convert local EPUBs for the Kindle reader on a USB-mounted device."""
import filecmp
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote_to_bytes
import zipfile

from epub_safety import bounded_read, check_expanded_size, check_raster, open_book, open_regular, parse_xml

CONVERTER_NAME = 'koshelf-kindle-converter'
MAX_CONVERSION = 256 * 1024 * 1024
MAX_CONVERSION_MEMBER = 32 * 1024 * 1024
MAX_CONVERSION_CSS = 16 * 1024 * 1024
MAX_CONVERSION_SPINE = 48 * 1024 * 1024
MAX_CONVERSION_DOCUMENT = 8 * 1024 * 1024
MAX_CONVERSION_MARKUP = 500_000


def converter():
    resources = Path(__file__).resolve().parent
    candidates = [resources.parent / 'MacOS' / CONVERTER_NAME] if resources.name == 'Resources' else [
        resources / 'build' / 'KoShelf.app' / 'Contents' / 'MacOS' / CONVERTER_NAME,
        resources / 'build' / CONVERTER_NAME,
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def check_epub(source):
    """Allow unencrypted books, including the standard font-only obfuscation."""
    try:
        with open_book(source) as epub:
            check_expanded_size(epub, MAX_CONVERSION, MAX_CONVERSION_MEMBER)
            if any(entry.flag_bits & 1 for entry in epub.infolist()) or 'META-INF/rights.xml' in epub.namelist():
                raise ValueError('This EPUB is protected. Choose a DRM-free EPUB.')
            if 'META-INF/encryption.xml' in epub.namelist():
                check_encryption(parse_xml(bounded_read(epub, 'META-INF/encryption.xml', 1024 * 1024)))
            check_publication(epub)
            # The converter decodes covers and some other images into memory. A
            # tiny compressed raster can advertise a very large pixel buffer.
            for entry in epub.infolist():
                if not entry.is_dir():
                    with epub.open(entry) as stream:
                        check_raster(stream)
    except zipfile.BadZipFile as exc:
        raise ValueError('This EPUB could not be read.') from exc


def local_attributes(node):
    # The converter's encoding/xml struct tags match attributes by local name.
    return {key.rsplit('}', 1)[-1]: value for key, value in node.attrib.items()}


def manifest_path(package, href):
    href = href.split('#', 1)[0].encode()
    # Match Go's url.PathUnescape: a malformed escape leaves the full href unchanged.
    if re.search(rb'%(?![0-9a-fA-F]{2})', href) is None:
        href = unquote_to_bytes(href)
    base = posixpath.normpath(posixpath.dirname(package))
    joined = posixpath.normpath(base.rstrip(b'/') + b'/' + href if base != b'.' else href)
    return b'/' + joined.lstrip(b'/') if joined.startswith(b'//') else joined


def check_publication(archive):
    # Go keeps the raw archive name bytes. Round-trip the standard ZIP name
    # encodings so both parsers account for the same destination resources.
    files = {entry.orig_filename.encode('utf-8' if entry.flag_bits & 0x800 else 'cp437'): entry
             for entry in archive.infolist()}
    container_entry = files.get(b'META-INF/container.xml')
    if container_entry is None:
        raise ValueError('This EPUB does not contain a package directory.')
    container = parse_xml(bounded_read(archive, container_entry, 2 * 1024 * 1024),
                          protected_attributes=('full-path',))
    rootfile = container.find('./{*}rootfiles/{*}rootfile')
    if rootfile is None:
        raise ValueError('This EPUB does not identify a package document.')
    package = local_attributes(rootfile).get('full-path', '').encode()
    if not package or package not in files:
        raise ValueError('This EPUB package document is missing.')
    document = parse_xml(bounded_read(archive, files[package], 2 * 1024 * 1024),
                         protected_attributes=('href', 'id', 'media-type', 'idref'))
    manifest = {}
    css_bytes = 0
    for item in document.findall('./{*}manifest/{*}item'):
        attributes = local_attributes(item)
        member = files.get(manifest_path(package, attributes.get('href', '')))
        media_type = attributes.get('media-type')
        manifest[attributes.get('id', '')] = (member, media_type)
        if member is not None and media_type == 'text/css':
            css_bytes += member.file_size
            if css_bytes > MAX_CONVERSION_CSS:
                raise ValueError('This EPUB contains too much stylesheet data for conversion.')

    ncx_bytes = sum(member.file_size for member, kind in manifest.values()
                    if member is not None and kind == 'application/x-dtbncx+xml')
    if ncx_bytes > MAX_CONVERSION_DOCUMENT:
        raise ValueError('This EPUB contains too much navigation data for conversion.')

    spine_bytes = markup = 0
    markup_counts = {}
    for item in document.findall('./{*}spine/{*}itemref'):
        member, _ = manifest.get(local_attributes(item).get('idref', ''), (None, None))
        if member is None:
            continue
        spine_bytes += member.file_size
        if member.file_size > MAX_CONVERSION_DOCUMENT or spine_bytes > MAX_CONVERSION_SPINE:
            raise ValueError('This EPUB contains too much reading-order data for conversion.')
        if member not in markup_counts:
            markup_counts[member] = bounded_read(archive, member, MAX_CONVERSION_DOCUMENT).count(b'<')
        markup += markup_counts[member]
        if markup > MAX_CONVERSION_MARKUP:
            raise ValueError('This EPUB contains too much complex chapter markup for conversion.')


def check_encryption(encryption):
    namespace = '{http://www.w3.org/2001/04/xmlenc#}'
    entries = encryption.findall('.//' + namespace + 'EncryptedData')
    for entry in entries:
        method = entry.find(namespace + 'EncryptionMethod')
        reference = entry.find('.//' + namespace + 'CipherReference')
        if (method is None or method.get('Algorithm') not in {
                'http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC'}
                or reference is None or Path(reference.get('URI', '')).suffix.lower() not in {'.ttf', '.otf', '.woff', '.woff2'}):
            raise ValueError('This EPUB is protected. Choose a DRM-free EPUB.')
    if not entries:
        raise ValueError('This EPUB uses unrecognized encryption metadata.')


def identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def source_revision(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def device_token(documents):
    return f'{documents}:{identity(documents.parent)}:{identity(documents)}'


def destination(documents, source):
    name = source.with_suffix('.azw3').name
    # FAT filenames reject a subset of the characters a Mac EPUB can contain.
    cleaned = ''.join('_' if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in name)
    return documents / cleaned


def convert(source, output, executable):
    # The converter must read the exact bytes we checked even if the source
    # is replaced by another application or an iCloud download in the meantime.
    with tempfile.TemporaryDirectory(prefix='input-', dir=output.parent) as directory:
        staged = Path(directory) / source.name
        with open_regular(source) as book, staged.open('xb') as target:
            remaining = MAX_CONVERSION
            while chunk := book.read(min(remaining + 1, 1024 * 1024)):
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValueError('This EPUB is too large for Kindle conversion.')
                target.write(chunk)
        check_epub(staged)
        with tempfile.TemporaryFile(dir=directory) as errors:
            try:
                result = subprocess.run([executable, str(staged), str(output)], check=False,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=errors, cwd=directory, timeout=300,
                                        env={'HOME': directory, 'TMPDIR': directory, 'PATH': '/usr/bin:/bin',
                                             'GOMEMLIMIT': '384MiB', 'GOTRACEBACK': 'none'})
            except subprocess.TimeoutExpired as exc:
                raise ValueError('Conversion took more than five minutes.') from exc
            if result.returncode:
                errors.seek(0)
                lines = errors.read(4096).decode(errors='replace').strip().splitlines()
                reason = lines[0][:240] if lines else 'Check that the EPUB opens and is DRM-free.'
                raise ValueError('Conversion failed. ' + reason)
    with open_regular(output) as stream:
        header = stream.read(78)
    if len(header) != 78 or header[60:68] != b'BOOKMOBI':
        raise ValueError('The converter did not produce a readable Kindle file.')


def copy_to_device(converted, target, rename, validate):
    validate()
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.koshelf-send-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with converted.open('rb') as source:
                shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
            if not filecmp.cmp(temporary, converted, shallow=False):
                raise ValueError('The Kindle copy did not match the converted book.')
            validate()
            rename(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
