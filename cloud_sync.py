"""Review and reconcile two local replicas of a KoShelf library."""

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import unicodedata
from pathlib import Path
from library_fs import open_regular, open_state_text, paths_overlap, private_state_directory, private_state_subdirectory


DATALESS = 0x40000000
SIDES = ("mac", "icloud")


def _available(info):
    if getattr(info, "st_flags", 0) & DATALESS:
        raise ValueError("Download the iCloud library in Finder before syncing.")


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _digest(path, info):
    with open_regular(path) as stream:
        if _identity(os.fstat(stream.fileno())) != _identity(info):
            raise ValueError("A library file changed while scanning. Review sync again.")
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        if _identity(os.fstat(stream.fileno())) != _identity(info):
            raise ValueError("A library file changed while scanning. Review sync again.")
    return digest.hexdigest()


def _name_key(name):
    return unicodedata.normalize("NFC", name.casefold())


def _scan(path, *, root=False, device=None):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if root:
            raise ValueError("The library folder is unavailable. Check its location before syncing.") from None
        return None
    _available(info)
    if device is not None and info.st_dev != device:
        raise ValueError("Cloud sync cannot cross filesystems.")
    if stat.S_ISDIR(info.st_mode):
        children = {}
        folded = set()
        for child in sorted(path.iterdir()):
            if child.name.endswith(".icloud") and child.name.startswith("."):
                raise ValueError("Download the iCloud library in Finder before syncing.")
            if child.name.startswith(".") or child.name.lower().endswith(".sdr"):
                continue
            entry = _scan(child, device=info.st_dev)
            if entry is None:
                continue
            key = _name_key(child.name)
            if key in folded:
                raise ValueError(f"Conflicting filename capitalization in {path.name}.")
            folded.add(key)
            children[child.name] = entry
        return {"folder": children}
    if root:
        raise ValueError("Choose an existing library folder.")
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"Remove the symbolic link {path.name} from the library before syncing.")
    if stat.S_ISREG(info.st_mode) and path.suffix.lower() == ".epub":
        return {"file": _digest(path, info)}
    return None


def _kind(entry):
    return next(iter(entry)) if entry else None


def _plans(mac, cloud, baseline, path=""):
    if mac == cloud:
        return
    if _kind(mac) == _kind(cloud) == "folder":
        old = baseline.get("folder", {}) if baseline else {}
        mac_children, cloud_children = mac["folder"], cloud["folder"]
        keys = mac_children.keys() | cloud_children.keys()
        if len({_name_key(key) for key in keys}) != len(keys):
            raise ValueError("Both libraries contain names that differ only by capitalization.")
        for name in sorted(keys):
            relative = f"{path}/{name}" if path else name
            yield from _plans(mac_children.get(name), cloud_children.get(name), old.get(name), relative)
        return
    values = {"mac": mac, "icloud": cloud}
    if baseline == cloud:
        source = "mac"
    elif baseline == mac:
        source = "icloud"
    else:
        source = None
    target = next((side for side in SIDES if source and side != source), None)
    if not source or (_kind(mac) and _kind(cloud) and _kind(mac) != _kind(cloud)):
        kind = "conflict"
        source = target = None
    elif values[source] is None:
        kind = "delete"
    elif values[target] is None:
        kind = "copy"
    else:
        kind = "replace"
    yield {"path": path, "kind": kind, "source": source, "target": target,
           "needsReview": kind in {"delete", "conflict"},
           "mac": _kind(mac), "icloud": _kind(cloud)}


def _baseline(state):
    try:
        with open_state_text(state / "baseline.json") as stream:
            return json.load(stream)["tree"]
    except FileNotFoundError:
        return {"folder": {}}


def _joined(mac, cloud, baseline):
    if mac == cloud:
        return mac
    if _kind(mac) == _kind(cloud) == "folder":
        old = baseline.get("folder", {}) if baseline else {}
        left, right = mac["folder"], cloud["folder"]
        children = {}
        for key in sorted(old.keys() | left.keys() | right.keys()):
            shared = _joined(left.get(key), right.get(key), old.get(key))
            if shared is not None:
                children[key] = shared
        return {"folder": children}
    return baseline


def _save(state, baseline):
    private_state_directory(state)
    with tempfile.NamedTemporaryFile(mode="w", dir=state, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump({"tree": baseline}, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, state / "baseline.json")
        finally:
            temporary.unlink(missing_ok=True)


def _snapshot(mac, cloud, state):
    roots = {side: Path(root).expanduser().absolute() for side, root in zip(SIDES, (mac, cloud))}
    if paths_overlap(*roots.values()):
        raise ValueError("Choose separate folders for the Mac and iCloud libraries.")
    if any(root.resolve() != root for root in roots.values()):
        raise ValueError("Choose library folders that are not symbolic links.")
    if any(paths_overlap(state, root) for root in roots.values()):
        raise ValueError("Sync state must be outside both libraries.")
    identities = {side: _root_identity(root) for side, root in roots.items()}
    trees = {side: _scan(root, root=True) for side, root in roots.items()}
    if any(_root_identity(root) != identities[side] for side, root in roots.items()):
        raise ValueError("A library folder changed while scanning. Review sync again.")
    baseline = _baseline(state)
    payload = ([str(root) for root in roots.values()], identities, trees, baseline)
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return roots, trees, baseline, token, identities


def preview(mac, cloud, state):
    state = Path(state)
    _, trees, baseline, token, _ = _snapshot(mac, cloud, state)
    actions = _review_actions(trees, baseline)
    if not actions and baseline != trees["mac"]:
        _save(state, trees["mac"])
        _, _, _, token, _ = _snapshot(mac, cloud, state)
    return {"token": token, "status": "ready" if actions else "in-sync", "actions": actions}


def _lookup(tree, relative):
    for part in relative.split("/"):
        tree = tree.get("folder", {}).get(part) if tree else None
    return tree


def _review_actions(trees, baseline):
    actions = list(_plans(trees["mac"], trees["icloud"], baseline))
    copies, deletes = {}, {}
    for action in actions:
        kind = action["kind"]
        if kind not in {"copy", "delete"}:
            continue
        side = action["source"] if kind == "copy" else action["target"]
        entry = _lookup(trees[side], action["path"])
        if _kind(entry) != "file":
            continue
        key = (action["source"], entry["file"])
        (copies if kind == "copy" else deletes).setdefault(key, []).append(action)
    for key in copies.keys() & deletes.keys():
        if len(copies[key]) == len(deletes[key]) == 1:
            copy, = copies[key]
            delete, = deletes[key]
            copy["moveFrom"] = delete["path"]
            delete["moveTo"] = copy["path"]
    return actions


def _copy_file(source, target, digest, rename, previous=None):
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".koshelf-cloud-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with open_regular(source) as original:
                shutil.copyfileobj(original, stream)
            stream.flush()
            os.fsync(stream.fileno())
            if _digest(temporary, temporary.lstat()) != digest:
                raise ValueError("A library file changed while copying. Review sync again.")
            if previous is None:
                if any(_name_key(child.name) == _name_key(target.name) for child in target.parent.iterdir()):
                    raise ValueError("The destination name already exists. Review sync again.")
                rename(temporary, target)
            else:
                if _scan(target) != previous:
                    raise ValueError("The destination file changed. Review sync again.")
                os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def _copy(source, target, entry, rename):
    if _kind(entry) == "file":
        _copy_file(source, target, entry["file"], rename)
        return
    target.mkdir()
    for name, child in entry["folder"].items():
        _copy(source / name, target / name, child, rename)


def _check_removal(path, entry):
    if _kind(entry) == "folder":
        if {child.name for child in path.iterdir()} != entry["folder"].keys():
            raise ValueError(f"{path.name} contains items outside cloud sync. Move those items before replacing or removing the folder.")
        for name, child in entry["folder"].items():
            _check_removal(path / name, child)


def _root_identity(root):
    try:
        info = root.lstat()
    except FileNotFoundError:
        raise ValueError("The library folder is unavailable. Check its location before syncing.") from None
    _available(info)
    if root.resolve() != root or not stat.S_ISDIR(info.st_mode):
        raise ValueError("A library folder changed. Review sync again.")
    return info.st_dev, info.st_ino


def _checked_path(root, identity, relative):
    if _root_identity(root) != identity:
        raise ValueError("A library folder changed. Review sync again.")
    path = root
    for part in relative.split("/")[:-1]:
        path /= part
        info = path.lstat()
        _available(info)
        if not stat.S_ISDIR(info.st_mode) or info.st_dev != identity[0]:
            raise ValueError("A library folder changed. Review sync again.")
    path = root / relative
    try:
        info = path.lstat()
    except FileNotFoundError:
        return path
    if info.st_dev != identity[0]:
        raise ValueError("Cloud sync cannot cross filesystems.")
    return path


def _apply_change(source, target, desired, previous, backup, rename):
    if _scan(source) != desired or _scan(target) != previous:
        raise ValueError("A library file changed. Review sync again.")
    if previous:
        _check_removal(target, previous)
        backup.parent.mkdir(parents=True, exist_ok=True)
        _copy(target, backup, previous, rename)
        if _scan(backup) != previous:
            raise ValueError("The recovery copy could not be verified.")
    if _kind(previous) == _kind(desired) == "file":
        _copy_file(source, target, desired["file"], rename, previous)
    elif not previous and _kind(desired) == "file":
        _copy_file(source, target, desired["file"], rename)
    else:
        staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=".koshelf-cloud-"))
        removed, replacement = staging / target.name, staging / ("replacement-" + target.name)
        try:
            if desired:
                _copy(source, replacement, desired, rename)
                if _scan(replacement) != desired:
                    raise ValueError("The staged copy could not be verified.")
            if previous:
                if _scan(target) != previous:
                    raise ValueError("The destination changed. Review sync again.")
                _check_removal(target, previous)
                rename(target, removed)
            try:
                if previous:
                    if _scan(removed) != previous:
                        raise ValueError("The destination changed. Review sync again.")
                    _check_removal(removed, previous)
                if desired:
                    rename(replacement, target)
            except (OSError, ValueError):
                if previous:
                    rename(removed, target)
                raise
            shutil.rmtree(staging, ignore_errors=True)
        finally:
            # Keep the staged original if rollback was interrupted.
            if not removed.exists():
                shutil.rmtree(staging, ignore_errors=True)


def apply(mac, cloud, state, token, choices, *, rename):
    state = Path(state)
    roots, trees, baseline, current_token, identities = _snapshot(mac, cloud, state)
    if token != current_token:
        raise ValueError("The libraries changed. Review sync again.")
    actions = _review_actions(trees, baseline)
    review = {action["path"] for action in actions if action["needsReview"]}
    if not isinstance(choices, dict) or choices.keys() - review or any(
        choice not in (*SIDES, "skip") for choice in choices.values()
    ):
        raise ValueError("Choose which versions to keep from this sync review.")
    changes = []
    errors = []
    skipped = 0
    queued = []
    recovery_id = uuid.uuid4().hex
    for action in actions:
        relative = action["path"]
        source_side = choices.get(relative, "skip") if action["needsReview"] else action["source"]
        if "moveFrom" in action and choices.get(action["moveFrom"]) != source_side:
            skipped += 1
            continue
        if source_side == "skip":
            skipped += 1
            continue
        target_side = next(side for side in SIDES if side != source_side)
        desired, previous = (_lookup(trees[side], relative) for side in (source_side, target_side))
        change = {**action, "source": source_side, "target": target_side,
                  "kind": "delete" if desired is None else "replace" if previous else "copy"}
        queued.append((change, desired, previous))
    queued.sort(key=lambda entry: entry[0]["kind"] == "delete")
    for change, desired, previous in queued:
        relative, source_side, target_side = (change[key] for key in ("path", "source", "target"))
        try:
            if change["kind"] == "delete" and errors:
                skipped += 1
                continue
            source = _checked_path(roots[source_side], identities[source_side], relative)
            target = _checked_path(roots[target_side], identities[target_side], relative)
            if previous:
                recovery = private_state_subdirectory(state, "recovery", recovery_id)
            else:
                recovery = state / "recovery" / recovery_id
            backup = recovery / target_side / relative
            _apply_change(source, target, desired, previous, backup, rename)
        except (OSError, ValueError) as exc:
            errors.append({"path": relative, "error": str(exc)})
            continue
        if previous:
            change["recovery"] = str(backup)
        changes.append(change)
    try:
        if any(_root_identity(root) != identities[side] for side, root in roots.items()):
            raise ValueError("A library folder changed. Review sync again.")
        updated = {side: _scan(root, root=True) for side, root in roots.items()}
        _save(state, _joined(updated["mac"], updated["icloud"], baseline))
    except (OSError, ValueError) as exc:
        errors.append({"path": "", "error": str(exc)})
    return {"applied": len(changes), "skipped": skipped, "changes": changes, "errors": errors}
