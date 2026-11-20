"""Read Apple Books' library on macOS, and add EPUBs to it.

KoShelf never writes Books' database. It reads a private snapshot of the
database files, so a running Books app is never affected. Adding a book copies
the EPUB into Books' iCloud Drive folder, which Books imports on its own.
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata

CONTAINER = Path.home() / 'Library' / 'Containers' / 'com.apple.iBooksX' / 'Data' / 'Documents'
ICLOUD_BOOKS = Path.home() / 'Library' / 'Mobile Documents' / 'iCloud~com~apple~iBooks' / 'Documents'
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
EPUB_CONTENT = 1


class AccessDenied(Exception):
    """macOS has not given this app access to Books' data."""


def available():
    return sys.platform == 'darwin' and CONTAINER.parent.parent.exists()


def library_database(container=None):
    folder = (container or CONTAINER) / 'BKLibrary'
    try:
        databases = sorted(folder.glob('BKLibrary-*.sqlite'), key=lambda path: path.stat().st_mtime)
    except PermissionError as exc:
        raise AccessDenied(str(exc)) from exc
    if not databases:
        try:
            next(folder.iterdir(), None)
        except PermissionError as exc:
            raise AccessDenied(str(exc)) from exc
        return None
    return databases[-1]


def database_revision(container=None):
    """Changes whenever Books writes its library, for caching snapshots."""
    database = library_database(container)
    if database is None:
        return None
    revision = []
    for suffix in ('', '-wal'):
        try:
            info = Path(str(database) + suffix).stat()
            revision.append((info.st_size, info.st_mtime_ns))
        except FileNotFoundError:
            revision.append(None)
        except PermissionError as exc:
            raise AccessDenied(str(exc)) from exc
    return str(database), tuple(revision)


def read_library(container=None, scratch=None):
    """Books' EPUBs, from a snapshot of its database. Raises AccessDenied without permission."""
    database = library_database(container)
    if database is None:
        return []
    with tempfile.TemporaryDirectory(prefix='apple-books-', dir=scratch) as folder:
        copy = Path(folder) / 'library.sqlite'
        try:
            for suffix in ('', '-wal', '-shm'):
                source = Path(str(database) + suffix)
                if source.exists():
                    shutil.copyfile(source, str(copy) + suffix)
        except PermissionError as exc:
            raise AccessDenied(str(exc)) from exc
        connection = sqlite3.connect(copy)
        try:
            rows = connection.execute(
                'SELECT ZASSETID, ZTITLE, ZAUTHOR, ZREADINGPROGRESS, ZISFINISHED, ZLASTOPENDATE, ZPATH, ZSTOREID '
                'FROM ZBKLIBRARYASSET WHERE ZCONTENTTYPE = ?', (EPUB_CONTENT,)).fetchall()
        finally:
            connection.close()
    books = []
    for asset, title, author, progress, finished, opened, path, store in rows:
        progress = progress if isinstance(progress, (int, float)) and 0 <= progress <= 1 else 0.0
        books.append({
            'asset': asset, 'title': title or '', 'author': author or '',
            'progress': 1.0 if finished == 1 else float(progress), 'finished': finished == 1,
            'opened': (CORE_DATA_EPOCH + timedelta(seconds=opened)).timestamp() if isinstance(opened, (int, float)) else None,
            'added': bool(path), 'store': bool(store),
        })
    return books


def ascii_words(text):
    """Words without case or accents. Letters in any script are kept, so non-Latin titles still match."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(char for char in text if not unicodedata.combining(char)).casefold()
    return ' '.join(re.sub(r'[\W_]+', ' ', text).split())


def title_keys(title):
    """The title without case, accents, punctuation, or leading article, and also without its subtitle."""
    text = re.sub(r'\(.*?\)|\[.*?\]', ' ', title or '')
    full = re.sub(r'^(the|a|an) ', '', ascii_words(text))
    short = re.sub(r'^(the|a|an) ', '', ascii_words(text.split(':')[0]))
    return full, short


def title_key(title):
    return title_keys(title)[1]


def author_keys(author):
    """Every listed author's surname, from "First Last", "Last, First", or a list of either."""
    surnames = set()
    for name in re.split(r'\s*(?:&|;|\band\b)\s*', author or ''):
        parts = [part.strip() for part in name.split(',') if part.strip()]
        if len(parts) == 2 and len(parts[0].split()) == 1:
            names = [parts[0]]  # "Last, First"
        else:
            names = [part.split()[-1] for part in parts if part.split()]
        for surname in names:
            key = ascii_words(surname)
            if key and key not in {'jr', 'sr', 'ii', 'iii'}:
                surnames.add(key)
    return surnames


class Matcher:
    """Finds the Books entries for a KoShelf book by title and author."""

    def __init__(self, books):
        self.by_full, self.by_short = {}, {}
        for book in books:
            full, short = title_keys(book['title'])
            if full:
                self.by_full.setdefault(full, []).append(book)
            if short:
                self.by_short.setdefault(short, []).append(book)

    def match(self, title, author):
        """Returns (entries with the same title and an author in common, entries with only the same title).

        A title that matches only without its subtitle counts when an author also matches.
        """
        full, short = title_keys(title)
        exact = self.by_full.get(full, []) if full else []
        loose = [book for book in self.by_short.get(short, []) if book not in exact] if short else []
        surnames = author_keys(author)
        same = [book for book in exact + loose if surnames & author_keys(book['author'])]
        return same, [book for book in exact if book not in same]

    def progress(self, title, author):
        """Books' progress for a KoShelf book, from its most recently opened entry, or None."""
        same, _ = self.match(title, author)
        if not same:
            return None
        finished = any(book['finished'] for book in same)
        latest = max(same, key=lambda book: book['opened'] or 0)
        if not finished and not latest['progress']:
            return None
        return {'progress': 1.0 if finished else latest['progress'], 'finished': finished}


def add_to_books(source, rename, opener, folder=None):
    """Copy an EPUB into Books' iCloud folder without replacing anything. Returns the new path.

    `rename` must refuse to replace an existing path, like app.rename_exclusive, and
    `opener` must refuse symbolic links and special files, like epub_safety.open_regular.
    """
    folder = folder or ICLOUD_BOOKS
    if not folder.is_dir():
        raise ValueError('Apple Books’ iCloud Drive folder is not available. Turn on iCloud Drive for Books.')
    stem = source.stem
    for attempt in range(1, 100):
        name = f'{stem}.epub' if attempt == 1 else f'{stem} ({attempt}).epub'
        destination = folder / name
        if os.path.lexists(destination):
            continue
        # iCloud Drive never uploads names ending in .nosync, so a partial copy stays local.
        with tempfile.NamedTemporaryFile(dir=folder, prefix='.koshelf-', suffix='.nosync', delete=False) as output:
            temporary = Path(output.name)
            try:
                with opener(source) as stream:
                    shutil.copyfileobj(stream, output)
                output.flush()
                os.fsync(output.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.chmod(temporary, 0o644)
            rename(temporary, destination)
        except FileExistsError:
            continue
        finally:
            temporary.unlink(missing_ok=True)
        return destination
    raise ValueError('Too many books with this name in Apple Books’ folder.')
