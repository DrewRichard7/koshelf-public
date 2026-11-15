"""Export independent reading ranges from EPUB omnibuses."""
from copy import copy
import os
import posixpath
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from datetime import datetime, timezone

from epub_safety import EPUB_MIMETYPE, bounded_read, check_expanded_size, open_book, parse_xml

OPF = 'http://www.idpf.org/2007/opf'
DC = 'http://purl.org/dc/elements/1.1/'
NCX = 'http://www.daisy.org/z3986/2005/ncx/'
XHTML = 'http://www.w3.org/1999/xhtml'
OPS = 'http://www.idpf.org/2007/ops'


def target(base, href):
    url = urlsplit(href)
    if url.scheme or url.netloc:
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(url.path))) if url.path else base


def xml(archive, name):
    return parse_xml(bounded_read(archive, name, 8 * 1024 * 1024))


class Omnibus:
    def __init__(self, archive):
        self.archive = archive
        names = archive.namelist()
        check_expanded_size(archive, 512 * 1024 * 1024, 512 * 1024 * 1024)
        if any(entry.flag_bits & 1 for entry in archive.infolist()) or {
            'META-INF/encryption.xml', 'META-INF/signatures.xml',
        }.intersection(names):
            raise ValueError('This EPUB is encrypted or signed.')
        if bounded_read(archive, 'mimetype', len(EPUB_MIMETYPE)) != EPUB_MIMETYPE:
            raise ValueError('This file is missing a valid EPUB mimetype.')
        container = xml(archive, 'META-INF/container.xml')
        roots = container.findall('./{*}rootfiles/{*}rootfile')
        if len(roots) != 1:
            raise ValueError('Splitting requires a single EPUB package.')
        self.package = roots[0].attrib['full-path']
        doc = xml(archive, self.package)
        spine = doc.find(f'{{{OPF}}}spine')
        self.modern = doc.get('version', '').startswith('3.')
        if spine is None or (not self.modern and doc.get('version') != '2.0'):
            raise ValueError('This splitter supports EPUB 2 and EPUB 3 books with a table of contents.')
        manifest = doc.find(f'{{{OPF}}}manifest')
        self.items = {item.attrib['id']: target(self.package, item.attrib['href']) for item in manifest}
        self.order = [self.items[row.attrib['idref']] for row in spine]
        if None in self.order or len(set(self.order)) != len(self.order):
            raise ValueError('This EPUB has an unsupported reading order.')
        self.positions = {page: index for index, page in enumerate(self.order)}
        self.toc = self.items.get(spine.get('toc'))
        self.navigation = next((self.items[item.attrib['id']] for item in manifest
                                if 'nav' in item.get('properties', '').split()), None) if self.modern else None
        self.sections = {index: posixpath.basename(page) for index, page in enumerate(self.order)}
        self.suggested = {}
        if self.navigation:
            nav = self.html_toc(xml(archive, self.navigation))
            if nav is None:
                raise ValueError('This EPUB has no readable navigation table of contents.')
            top = set(nav.findall(f'{{{XHTML}}}ol/{{{XHTML}}}li'))
            for point in nav.iter(f'{{{XHTML}}}li'):
                link = point.find(f'{{{XHTML}}}a')
                if link is not None:
                    label = ' '.join(''.join(link.itertext()).split())
                    self.add_section(self.navigation, link.get('href', ''), label,
                                     point in top and point.find(f'{{{XHTML}}}ol') is not None)
        elif self.toc:
            nav = xml(archive, self.toc).find(f'{{{NCX}}}navMap')
            if nav is None:
                raise ValueError('This EPUB has no readable table of contents.')
            top = set(nav)
            for point in nav.iter(f'{{{NCX}}}navPoint'):
                content = point.find(f'{{{NCX}}}content')
                label = point.findtext(f'{{{NCX}}}navLabel/{{{NCX}}}text')
                if content is not None:
                    self.add_section(self.toc, content.attrib['src'], label,
                                     point in top and point.find(f'{{{NCX}}}navPoint') is not None)
        else:
            raise ValueError('This EPUB has no readable table of contents.')

    @staticmethod
    def html_toc(doc):
        return next((nav for nav in doc.iter(f'{{{XHTML}}}nav')
                     if 'toc' in nav.get(f'{{{OPS}}}type', '').split()), None)

    def add_section(self, base, href, label, volume):
        page = target(base, href)
        if label and href and page in self.positions and not urlsplit(href).fragment:
            index = self.positions[page]
            self.sections[index] = label
            if volume:
                self.suggested.setdefault(index, label)

    def write_html_navigation(self, pages, first, title):
        doc = xml(self.archive, self.navigation)
        toc = self.html_toc(doc)
        for section in doc.iter(f'{{{XHTML}}}nav'):
            for listing in section.findall(f'{{{XHTML}}}ol'):
                self.trim_html_list(listing, pages)
                if not len(listing):
                    section.remove(listing)
        if not toc.findall(f'{{{XHTML}}}ol'):
            listing = ET.SubElement(toc, f'{{{XHTML}}}ol')
            point = ET.SubElement(listing, f'{{{XHTML}}}li')
            href = quote(posixpath.relpath(first, posixpath.dirname(self.navigation) or '.'))
            ET.SubElement(point, f'{{{XHTML}}}a', {'href': href}).text = title
        for parent in doc.iter():
            for section in parent.findall(f'{{{XHTML}}}nav'):
                if section is not toc and section.find(f'{{{XHTML}}}ol') is None:
                    parent.remove(section)
        return ET.tostring(doc, encoding='utf-8', xml_declaration=True)

    def trim_html_list(self, listing, pages):
        keep = []
        for point in listing:
            children = []
            for child in point:
                if child.tag == f'{{{XHTML}}}ol':
                    self.trim_html_list(child, pages)
                    if not len(child):
                        continue
                children.append(child)
            point[:] = children
            link = point.find(f'{{{XHTML}}}a')
            if link is not None and target(self.navigation, link.get('href', '')) in pages:
                keep.append(point)
                continue
            if point.find(f'{{{XHTML}}}ol') is None:
                continue
            if link is not None:
                link.tag = f'{{{XHTML}}}span'
                link.attrib.pop('href', None)
            keep.append(point)
        listing[:] = keep

    def preview(self):
        return {
            'sections': [{'start': index, 'label': label} for index, label in self.sections.items()],
            'suggested': [{'start': index, 'title': self.suggested[index]} for index in sorted(self.suggested)],
        }

    def write(self, destination, start, end, title, series, position, rewrite_opf):
        archive = self.archive
        pages = set(self.order[start:end])
        doc = ET.fromstring(rewrite_opf(archive.read(self.package), title, None,
                                       series=series, series_position=str(position) if series else ''))
        spine = doc.find(f'{{{OPF}}}spine')
        for index, row in enumerate(list(spine)):
            if not start <= index < end:
                spine.remove(row)
        metadata = doc.find(f'{{{OPF}}}metadata')
        identifier = 'urn:uuid:' + str(uuid.uuid4())
        for node in list(metadata):
            if node.tag in {f'{{{DC}}}identifier', f'{{{DC}}}description'}:
                metadata.remove(node)
        ET.SubElement(metadata, f'{{{DC}}}identifier', {'id': doc.attrib['unique-identifier']}).text = identifier
        manifest = doc.find(f'{{{OPF}}}manifest')
        omitted = set(self.order) - pages - {self.navigation}
        for item in list(manifest):
            if self.items[item.attrib['id']] in omitted:
                manifest.remove(item)
        guide = doc.find(f'{{{OPF}}}guide')
        if guide is not None:
            doc.remove(guide)
        first = self.order[start]
        first_doc = xml(archive, first)
        images = {target(first, node.get('src', '')) for node in first_doc.findall('.//{*}img')}
        if len(images) == 1:
            cover = next((item for item in manifest if self.items[item.attrib['id']] in images
                          and item.get('media-type', '').startswith('image/')), None)
            if cover is not None:
                for node in list(metadata):
                    if node.get('name') == 'cover':
                        metadata.remove(node)
                ET.SubElement(metadata, f'{{{OPF}}}meta', {'name': 'cover', 'content': cover.attrib['id']})
        if self.modern:
            for node in list(metadata):
                if node.get('property') == 'dcterms:modified':
                    metadata.remove(node)
            ET.SubElement(metadata, f'{{{OPF}}}meta', {'property': 'dcterms:modified'}).text = (
                datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            )
        updated = {self.package: ET.tostring(doc, encoding='utf-8', xml_declaration=True)}
        if self.navigation:
            updated[self.navigation] = self.write_html_navigation(pages, first, title)
        if self.toc:
            toc = xml(archive, self.toc)
            for node in toc.findall(f'{{{NCX}}}head/{{{NCX}}}meta'):
                if node.get('name') == 'dtb:uid':
                    node.set('content', identifier)
            toc.find(f'{{{NCX}}}docTitle/{{{NCX}}}text').text = title
            nav = toc.find(f'{{{NCX}}}navMap')
            self.trim_navigation(nav, pages)
            for element in list(toc):
                if element.tag in {f'{{{NCX}}}pageList', f'{{{NCX}}}navList'}:
                    toc.remove(element)
            if not len(nav):
                point = ET.SubElement(nav, f'{{{NCX}}}navPoint', {'id': 'start', 'playOrder': '1'})
                ET.SubElement(ET.SubElement(point, f'{{{NCX}}}navLabel'), f'{{{NCX}}}text').text = title
                href = posixpath.relpath(first, posixpath.dirname(self.toc) or '.')
                ET.SubElement(point, f'{{{NCX}}}content', {'src': href})
            for number, point in enumerate(nav.iter(f'{{{NCX}}}navPoint'), 1):
                point.set('playOrder', str(number))
            updated[self.toc] = ET.tostring(toc, encoding='utf-8', xml_declaration=True)
        for page in pages - {self.navigation}:
            document = xml(archive, page)
            changed = False
            for link in document.findall('.//{*}a'):
                if target(page, link.get('href', '')) in omitted:
                    link.attrib.pop('href', None)
                    changed = True
            if changed:
                updated[page] = ET.tostring(document, encoding='utf-8', xml_declaration=True)
        with zipfile.ZipFile(destination, 'w') as output:
            output.writestr('mimetype', EPUB_MIMETYPE, compress_type=zipfile.ZIP_STORED)
            for entry in archive.infolist():
                if entry.filename == 'mimetype' or entry.filename in omitted:
                    continue
                if entry.filename in updated:
                    output.writestr(entry.filename, updated[entry.filename], compress_type=zipfile.ZIP_DEFLATED)
                else:
                    with archive.open(entry) as source, output.open(copy(entry), 'w') as stream:
                        shutil.copyfileobj(source, stream)
        with zipfile.ZipFile(destination) as check:
            if check.testzip() is not None:
                raise ValueError('An exported EPUB failed its integrity check.')

    def trim_navigation(self, parent, pages):
        keep = []
        for point in parent:
            if point.tag != f'{{{NCX}}}navPoint':
                keep.append(point)
                continue
            self.trim_navigation(point, pages)
            content = point.find(f'{{{NCX}}}content')
            if content is not None and target(self.toc, content.attrib['src']) in pages:
                keep.append(point)
            else:
                keep.extend(point.findall(f'{{{NCX}}}navPoint'))
        parent[:] = keep


def split(path, body, rewrite_opf, rename):
    try:
        with open_book(path) as archive:
            omnibus = Omnibus(archive)
            stat = os.fstat(archive.fp.fileno())
            revision = f'{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}'
            if 'parts' not in body:
                return {**omnibus.preview(), 'revision': revision}
            if revision != body.get('revision'):
                raise ValueError('The omnibus changed. Open the split preview again.')
            parts = body['parts']
            series = body.get('series', '').strip()
            if not isinstance(parts, list) or not 2 <= len(parts) <= 16 or len(series) > 500:
                raise ValueError('Choose between two and sixteen reading ranges.')
            starts = [part['start'] for part in parts]
            if any(type(start) is not int for start in starts) or starts != sorted(set(starts)) or not 0 <= starts[0] <= starts[-1] < len(omnibus.order):
                raise ValueError('Choose distinct start pages in reading order.')
            folder = path.parent / (path.stem[:60] + ' — split')
            occupied = {entry.name.casefold() for entry in path.parent.iterdir()}
            suffix = 2
            while folder.name.casefold() in occupied:
                folder = path.parent / (path.stem[:60] + f' — split {suffix}')
                suffix += 1
            with tempfile.TemporaryDirectory(prefix='.koshelf-split-', dir=path.parent) as temporary:
                staged = Path(temporary) / 'books'
                staged.mkdir()
                filenames = []
                for index, (part, end) in enumerate(zip(parts, starts[1:] + [len(omnibus.order)]), 1):
                    if part.get('skip'):
                        continue
                    title = part['title'].strip()
                    if not title or len(title) > 500 or any(ord(char) < 32 for char in title):
                        raise ValueError('Enter a title for each volume.')
                    filename = re.sub(r'[/:\\\\]', ' ', title).strip(' .')
                    filename = filename.encode('utf-8')[:180].decode('utf-8', errors='ignore').strip(' .')
                    if not filename or filename.casefold() in {name[:-5].casefold() for name in filenames}:
                        raise ValueError('Each export needs a distinct filename.')
                    filename += '.epub'
                    omnibus.write(staged / filename, part['start'], end, title, series, index, rewrite_opf)
                    filenames.append(filename)
                if not filenames:
                    raise ValueError('Select at least one volume to export.')
                current = path.stat()
                if (current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                    stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                ):
                    raise ValueError('The omnibus changed during export.')
                rename(staged, folder)
            return {'folder': folder.name, 'files': filenames}
    except (zipfile.BadZipFile, ET.ParseError, KeyError, AttributeError, TypeError) as exc:
        raise ValueError('KoShelf could not read the EPUB structure or split settings.') from exc
