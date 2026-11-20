"""Read-only genre suggestions from EPUB subject tags."""

import unicodedata
from collections import Counter


GENRE_ALIASES = {
    "Fiction": ("general fiction", "fiction / general"),
    "Literary Fiction": ("literary", "literature & fiction", "fiction / literary"),
    "Historical Fiction": ("fiction / historical", "historical novels"),
    "Fantasy": ("fiction / fantasy", "fantasy fiction", "epic fantasy", "urban fantasy"),
    "Science Fiction": ("sci-fi", "sci fi", "sf", "fiction / science fiction", "space opera"),
    "Mystery": ("detective fiction", "fiction / mystery & detective"),
    "Thriller": ("thrillers", "suspense fiction", "fiction / thrillers"),
    "Romance": ("romance fiction", "fiction / romance"),
    "Horror": ("horror fiction", "fiction / horror"),
    "Short Stories": ("fiction / short stories", "short story collections"),
    "Nonfiction": ("non-fiction", "non fiction"),
    "Biography & Memoir": ("biography", "biographies", "memoir", "memoirs", "autobiography",
                           "biography & autobiography"),
    "History": (),
    "Science": (),
    "Philosophy": (),
    "Poetry": (),
    "Essays": (),
}

FICTION_GENRES = frozenset((
    "Literary Fiction", "Historical Fiction", "Fantasy", "Science Fiction",
    "Mystery", "Thriller", "Romance", "Horror", "Short Stories",
))
NONFICTION_GENRES = frozenset(("Biography & Memoir", "History", "Science", "Philosophy", "Essays"))
BROAD_GENRES = {"Fiction": FICTION_GENRES, "Nonfiction": NONFICTION_GENRES}


def tag_key(tag):
    return unicodedata.normalize("NFC", " ".join(tag.split()).casefold())


KNOWN_TAGS = {
    tag_key(alias): genre
    for genre, aliases in GENRE_ALIASES.items()
    for alias in (genre, *aliases)
}


def validate_mappings(mappings):
    """Validate persisted mappings and canonicalize their lookup keys."""
    if not isinstance(mappings, dict) or len(mappings) > 1000:
        raise ValueError("Supply up to 1,000 genre mappings")
    clean = {}
    for tag, target in mappings.items():
        if (not isinstance(tag, str) or not tag.strip() or len(tag) > 1000
                or not isinstance(target, str) or len(target) > 1000
                or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in tag + target)):
            raise ValueError("Genre mappings require a source tag and a text replacement")
        key = tag_key(tag)
        value = " ".join(target.split())
        if key in clean and value != clean[key]:
            raise ValueError("A tag can have only one replacement")
        clean[key] = value
    return clean


def _replacement(key, mappings, custom_genres):
    if key in mappings:
        return mappings[key]
    if key in custom_genres:
        return custom_genres[key]
    if key in KNOWN_TAGS:
        return KNOWN_TAGS[key]
    # Published classifications commonly use a slash-delimited path. Only
    # use a recognized prefix, and leave unknown branches for review.
    parts = [part.strip() for part in key.split("/")]
    if len(parts) >= 3 and parts[0] == "fiction" and all(parts):
        return KNOWN_TAGS.get(" / ".join(parts[:2]))
    if len(parts) >= 2 and all(parts) and parts[0] in {
        "biography & autobiography", "history", "science", "philosophy", "poetry",
    }:
        return KNOWN_TAGS[parts[0]]
    return None


def _book_suggestion(book, current, keys, replacements, mappings):
    proposed = []
    changes = []
    unmapped = []
    seen = set()
    specific = {replacements[key] for key in keys}
    for tag, key in zip(current, keys):
        replacement = replacements[key]
        if replacement is None:
            unmapped.append(tag)
            replacement = tag
        if key in mappings:
            reason = "Uses your saved tag mapping."
        else:
            reason = f"Uses the standard genre {replacement}."
        if replacement in BROAD_GENRES and key not in mappings and specific & BROAD_GENRES[replacement]:
            reason = f"A more specific {replacement.lower()} genre is already listed."
            replacement = ""
        if replacement:
            replacement_key = tag_key(replacement)
            if replacement_key in seen:
                replacement = ""
                reason = "This tag is already represented."
            else:
                proposed.append(replacement)
                seen.add(replacement_key)
        if replacement != tag:
            changes.append({"tag": tag, "replacement": replacement, "reason": reason})
    if changes:
        return {
            "path": book["path"], "title": book["title"], "author": book["author"],
            "current": current, "proposed": proposed, "changes": changes, "unmapped": unmapped,
        }
    return None


def preview_genres(books, mappings=None):
    """Summarize tag usage and suggest per-book edits without changing any files."""
    mappings = validate_mappings({} if mappings is None else mappings)
    # Apply exactly one mapping per source tag. Treat saved destinations as
    # recognized genres, even when they are absent from the starter list.
    custom_genres = {tag_key(target): target for target in sorted(mappings.values()) if target}
    counts = Counter()
    labels = {}
    replacements = {}
    suggestions = []
    unmapped_books = 0
    for book in books:
        current = book.get("genres", [])
        keys = [tag_key(tag) for tag in current]
        counts.update(set(keys))
        for tag, key in zip(current, keys):
            if key not in labels:
                labels[key] = tag
                replacements[key] = _replacement(key, mappings, custom_genres)
        if any(replacements[key] is None for key in keys):
            unmapped_books += 1
        suggestion = _book_suggestion(book, current, keys, replacements, mappings)
        if suggestion:
            suggestions.append(suggestion)
    tags = [
        {"tag": labels[key], "key": key, "count": counts[key], "replacement": replacements[key]}
        for key in sorted(counts, key=lambda key: (-counts[key], key))
    ]
    genres = sorted({**{tag_key(genre): genre for genre in GENRE_ALIASES},
                     **custom_genres}.values(), key=str.casefold)
    return {"books": suggestions, "tags": tags, "genres": genres, "unmappedBooks": unmapped_books}


def choose_changes(current, changes, accepted):
    """The genres that result from applying only the accepted tag changes.

    Tags without an accepted change stay exactly as they are.
    """
    replacements = {change["tag"]: change["replacement"] for change in changes}
    accepted = set(accepted)
    if not accepted <= replacements.keys():
        raise ValueError("Choose changes from the current preview")
    result = []
    for tag in current:
        if tag not in accepted:
            result.append(tag)
            continue
        replacement = replacements[tag]
        if replacement and tag_key(replacement) not in {tag_key(genre) for genre in result}:
            result.append(replacement)
    return result
