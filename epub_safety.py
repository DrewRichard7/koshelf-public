"""Bound reads of untrusted EPUB archives and their embedded assets."""
from contextlib import contextmanager
from io import BufferedReader, BytesIO
import os
import xml.etree.ElementTree as ET
from xml.parsers import expat
import zipfile
import zlib
from library_fs import open_regular


MAX_ENTRIES = 20_000
MAX_DIRECTORY = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
EPUB_MIMETYPE = b'application/epub+zip'


class ArchiveStream(BufferedReader):
    """Prevent zipfile from allocating an oversized central directory."""
    opening = True

    def read(self, size=-1):
        if self.opening:
            if size < 0:
                # Older zipfile versions use read() while searching for the
                # end-of-directory record in the final 64 KiB of the file.
                size = max(0, os.fstat(self.fileno()).st_size - self.tell())
            if size > MAX_DIRECTORY:
                raise ValueError('The EPUB archive directory is too large.')
        return super().read(size)


@contextmanager
def open_book(path):
    with open_regular(path) as raw, ArchiveStream(raw) as stream:
        with zipfile.ZipFile(stream) as archive:
            stream.opening = False
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES or len({entry.filename for entry in entries}) != len(entries):
                raise ValueError('The EPUB archive has too many entries or contains duplicate names.')
            try:
                yield archive
            except zlib.error as exc:
                raise ValueError('The EPUB contains damaged compressed data.') from exc


def check_entry(info):
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ValueError('The EPUB uses an unsupported compression method.')


def check_expanded_size(archive, maximum, member_maximum):
    expanded = 0
    for info in archive.infolist():
        check_entry(info)
        expanded += info.file_size
        if info.file_size > member_maximum or expanded > maximum:
            raise ValueError('The EPUB contains too much uncompressed data.')


def bounded_read(archive, name, limit):
    info = name if isinstance(name, zipfile.ZipInfo) else archive.getinfo(name)
    check_entry(info)
    if info.file_size > limit:
        raise ValueError('An embedded EPUB asset exceeds its size limit.')
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('An embedded EPUB asset exceeds its size limit.')
    return data


def check_xml(data, *, allow_external_doctype=True, protected_attributes=()):
    """Keep ordinary external DOCTYPE references inert and reject entity definitions."""
    parser = expat.ParserCreate()
    depth = nodes = 0

    def start(name, attributes):
        nonlocal depth, nodes
        depth += 1
        nodes += 1
        if depth > 128 or nodes > 150_000:
            raise ValueError('An EPUB XML document is too complex.')
        if any('xmlns:' + attribute in attributes for attribute in protected_attributes):
            raise ValueError('This EPUB declares an ambiguous XML namespace.')

    def end(name):
        nonlocal depth
        depth -= 1

    def doctype(name, system_id, public_id, has_subset):
        if has_subset or not allow_external_doctype:
            raise ValueError('The EPUB uses unsupported XML declarations.')

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = doctype
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise ValueError('An EPUB XML document is malformed.') from exc


def parse_xml(data, *, protected_attributes=()):
    check_xml(data, protected_attributes=protected_attributes)
    return ET.fromstring(data)


def raster_type(stream):
    """Identify supported raster headers without allocating a decoded image."""
    header = stream.read(10)
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        header += stream.read(14)
        if len(header) == 24 and header[8:16] == b'\x00\x00\x00\rIHDR':
            return 'image/png', int.from_bytes(header[16:20], 'big'), int.from_bytes(header[20:24], 'big')
    if header.startswith((b'GIF87a', b'GIF89a')) and len(header) == 10:
        return 'image/gif', int.from_bytes(header[6:8], 'little'), int.from_bytes(header[8:10], 'little')
    if header.startswith(b'\xff\xd8\xff'):
        stream.seek(2)
        # Bound the work of skipping metadata segments before the first frame.
        while stream.tell() < 1024 * 1024 and stream.read(1) == b'\xff':
            marker = stream.read(1)
            while marker == b'\xff' and stream.tell() < 1024 * 1024:
                marker = stream.read(1)
            if not marker or marker in (b'\xda', b'\xd9'):
                break
            length = int.from_bytes(stream.read(2), 'big')
            if length < 2:
                break
            if 0xC0 <= marker[0] <= 0xCF and marker[0] not in {0xC4, 0xC8, 0xCC}:
                frame = stream.read(5)
                if length >= 8 and len(frame) == 5:
                    return 'image/jpeg', int.from_bytes(frame[3:5], 'big'), int.from_bytes(frame[1:3], 'big')
                break
            stream.seek(length - 2, os.SEEK_CUR)
    if header.startswith((b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a', b'\xff\xd8\xff')):
        raise ValueError('An EPUB image has an unreadable header.')
    return None


def check_raster(stream):
    details = raster_type(stream)
    if details:
        mime, width, height = details
        if not width or not height or width * height > MAX_IMAGE_PIXELS:
            raise ValueError('An EPUB image exceeds the supported dimensions.')
        return mime
    return None


def image_mime(data):
    return check_raster(BytesIO(data))
