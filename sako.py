#!/usr/bin/env python3
"""SAKO, Simple Agentic Kit for Operations: a task ledger and finish gate for coding agents.

Run from anywhere in the repository or one of its worktrees; --help lists the commands.

Standard library only, in one file. Python 3.10 or newer. Runs on Linux, WSL and Windows; macOS is allowed, untested.
Git finds the repository from the current directory. Everything SAKO keeps lives
in the main checkout's .sako/ folder, shared by its worktrees and listed in
.git/info/exclude, so nothing tracked changes.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.5.0"

TASK_ID = re.compile(r"\bT-\d+\b")
MARKER = re.compile(r"sk-\d{4}-[0-9a-f]{12}")
DONE = "done"
EMPTY_CELL = ("", "\u2014", "-", "\u2013")
PRIORITIES = ("P1", "P2", "P3")
COLUMNS = {"tasks": ("ID", "Pri", "Task", "Done when", "Status", "Scope", "After", "Source"),
           "done": ("ID", "Task", "Done when", "Receipt", "Scope", "Source")}

KIT = ".sako"
PYTHON = "python" if sys.platform == "win32" else "python3"  # the command SAKO prints and its hooks run
CONFIG = ".sako/config.json"
DEFAULTS: dict[str, object] = {
    "tasks": "work/TASKS.md",
    "done": "work/DONE.md",
    "verify_command": None,
    "verify_paths": [],
}


class SakoError(Exception):
    """An actionable refusal. Never turns into passing evidence."""


def retried(action):
    """Windows refuses a file for a moment while another process reads or replaces it."""
    for wait in (0.01, 0.05, 0.2, 0.5):
        with suppress(PermissionError):
            return action()
        time.sleep(wait)
    return action()


def read_text(path: Path, label: str) -> str:
    try:
        return retried(lambda: path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise SakoError(f"{label} {path}: not UTF-8 text") from None
    except OSError as error:
        raise SakoError(f"{label} {path}: {error.strerror or error}") from None


# ----------------------------------------------------------------------------
# configuration and roots


def load_config(kit: Path) -> dict:
    """Read .sako/config.json over the defaults. Record paths stay inside the kit folder, check paths
    inside the checkout; unknown keys, wrong types and escaping paths are errors."""
    data: dict = dict(DEFAULTS)
    path = kit / "config.json"
    if path.exists():
        try:
            loaded = json.loads(read_text(path, CONFIG))
        except ValueError as error:
            raise SakoError(f"{CONFIG}: invalid JSON ({error})") from None
        if not isinstance(loaded, dict):
            raise SakoError(f"{CONFIG}: expected a JSON object")
        unknown = sorted(set(loaded) - set(DEFAULTS))
        if unknown:
            raise SakoError(f"{CONFIG}: unknown keys {unknown}")
        data.update(loaded)
    command, paths = data["verify_command"], data["verify_paths"]
    if command is not None and not (isinstance(command, list) and command and all(isinstance(x, str) and x for x in command)):
        raise SakoError(f"{CONFIG}: verify_command must be null or a non-empty list of strings")
    if not (isinstance(paths, list) and all(isinstance(x, str) and x and not Path(x).is_absolute()
                                            and ".." not in Path(x).parts for x in paths)):
        raise SakoError(f"{CONFIG}: verify_paths must list paths that stay inside the repository")
    home, records = kit.resolve(), []
    for key in ("tasks", "done"):
        path = (home / data[key]).resolve() if isinstance(data[key], str) and data[key].strip() else home
        if home not in path.parents or path.relative_to(home).parts[0].lower() in ("state", ".git") or path.exists() \
                and not path.is_file():
            raise SakoError(f"{CONFIG}: {key} must name a file and stay inside {KIT}/, outside state/")
        records.append(path.relative_to(home).as_posix())
    first, second = (r.lower() for r in records)  # case apart: macOS and Windows disks ignore it
    if first == second or first.startswith(second + "/") or second.startswith(first + "/") \
            or {first, second} & {"sako.py", "sako.md", "install.json", "config.json"}:
        raise SakoError(f"{CONFIG}: tasks and done must be two distinct files, apart from the kit files")
    data["tasks"], data["done"] = records
    return data


@dataclass
class Roots:
    work: Path      # this checkout's top level: code, checks and local changes
    main: Path      # the main checkout, home of the kit folder
    worktree: str   # a linked worktree's name under .git/worktrees/; "" in the main checkout
    home: Path      # the checkout whose .sako/ this one uses: main, or itself in the shared footprint

    @property
    def kit(self) -> Path:
        return self.home / KIT

    @property
    def state(self) -> Path:
        """Disposable state shared by every worktree; never under .git/."""
        return self.kit / "state"

    @property
    def local(self) -> Path:
        """This checkout's own state: its check stamp, verify lock and hook sightings."""
        return self.state / "worktrees" / self.worktree if self.home != self.work else self.state


def git(root: Path, *args: str, check: bool = True) -> str:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")  # paths come back as UTF-8, unquoted, whatever the locale
    result = subprocess.run(["git", "-c", "core.quotePath=false", "-C", str(root), *args], capture_output=True,
                            encoding="utf-8", errors="surrogateescape", env=env)
    if check and result.returncode:
        raise SakoError(f"git {' '.join(args)} in {root}: {result.stderr.strip() or 'failed'}")
    return result.stdout.strip()


def resolve_roots(start: Path) -> Roots:
    """This checkout and the main checkout, found through Git from any subfolder or
    worktree. The kit folder is the main checkout's .sako/."""
    if not sys.platform.startswith(("linux", "darwin", "win32")):
        raise SakoError("SAKO runs on Linux, WSL, macOS and Windows; other platforms are not supported")
    if not Path(start).is_dir():
        raise SakoError(f"{start} does not exist")
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    result = subprocess.run(["git", "-C", str(start), "rev-parse", "--path-format=absolute",
                             "--show-toplevel", "--git-dir", "--git-common-dir"],
                            capture_output=True, encoding="utf-8", errors="surrogateescape", env=env)
    if result.returncode:
        if "path-format" in result.stderr:
            raise SakoError("SAKO needs Git 2.31 or newer (git rev-parse --path-format)")
        raise SakoError(f"{start} is not inside a Git worktree")
    top, gitdir, common = (Path(line).resolve() for line in result.stdout.strip().splitlines()[:3])
    if common.name != ".git" or gitdir != common and git(start, f"--git-dir={common}", "rev-parse",
                                                          "--is-bare-repository", check=False) == "true":
        raise SakoError("this repository keeps its Git folder apart from a checkout (a bare repository, "
                        "a separate Git folder or a submodule); SAKO does not support that layout yet")
    home = top if gitdir != common and tracked(top, MANIFEST) and read_json(top / MANIFEST).get("footprint") == "shared" \
        else common.parent
    if gitdir != common and home != top and read_json(home / MANIFEST).get("footprint") == "shared":
        raise SakoError(f"the shared footprint keeps a copy of {KIT}/ on each branch, and this branch has none; merge one that does")
    return Roots(top, common.parent, "" if gitdir == common else gitdir.name, home)


def inside(root: Path, relative: str) -> Path:
    """A root-relative path that must stay inside the root and out of Git internals."""
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise SakoError(f"{relative} escapes the repository")
    if ".git" in [part.lower() for part in path.relative_to(root).parts]:  # lower: macOS and Windows ignore case
        raise SakoError(f"{relative} resolves into Git internals")
    return path


def atomic_write(path: Path, text: str | bytes) -> None:
    """Replace a complete file; readers never observe half a record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=".sako-", delete=False) as stream:
            name = stream.name
            stream.write(text.encode("utf-8") if isinstance(text, str) else text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, (path.stat().st_mode & 0o777) if path.exists() else 0o644)
        retried(lambda: os.replace(name, path))
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


@contextmanager
def ledger_lock(roots: Roots, write: bool = False, name: str = "ledger"):
    """Serialize cooperating commands on the records, never code edits or Git. A read that cannot
    create its lock (a read-only sandbox) reads without it: record writes replace whole files.
    Windows locks are exclusive only, so reads there take none."""
    lib = __import__("msvcrt" if sys.platform == "win32" else "fcntl")
    if name != "install" and not roots.kit.is_dir():
        raise SakoError(f"no SAKO kit at {roots.kit.as_posix()}; install it with: uvx sako init")
    path = (roots.local if name == "verify" else roots.state) / (name + ".lock")
    try:
        if sys.platform == "win32" and not write:
            raise OSError("reads take no lock on Windows")
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a")
    except OSError:
        if write:
            raise
        yield
        return
    with stream:
        deadline = time.monotonic() + 2
        while True:
            try:
                (lib.locking(stream.fileno(), lib.LK_NBLCK, 1) if sys.platform == "win32"  # byte 0 of the empty file, freed on close
                 else lib.flock(stream, (lib.LOCK_EX if write else lib.LOCK_SH) | lib.LOCK_NB))
                break
            except (BlockingIOError, PermissionError):  # Windows refuses a held lock with PermissionError
                if time.monotonic() >= deadline:
                    raise SakoError(f"{name} is busy in another SAKO command; retry when it finishes")
                time.sleep(0.02)
        yield


# ----------------------------------------------------------------------------
# ledger


@dataclass
class Row:
    id: str
    doc: str                    # "tasks" or "done"
    line: int
    titles: list[str]           # its table's header cells, as written
    cells: dict[str, str]       # every cell by column name, as written
    pri: str = "P2"
    task: str = ""
    done_when: str = ""
    status: str = "open"        # open, parked, claimed, done, or what a hand edit left
    claim: str = ""             # the holder's marker, then any takeover note
    touches: list[str] = field(default_factory=list)
    source: str = ""
    after: list[str] = field(default_factory=list)
    evidence: str = ""          # a done row's receipt


def column(title: str) -> str:
    """A header cell as a column name: case, spacing and emphasis do not matter."""
    return " ".join(title.strip("*` ").split()).lower()


def record_lines(text: str):
    """Numbered Markdown lines outside fenced examples."""
    fence = ""
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        boundary = re.match(r"(`{3,}|~{3,})", line)
        if boundary:
            token = boundary.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
        elif not fence:
            yield number, line
    if fence:
        raise SakoError("unclosed fenced example in task record; close the fence before editing work")


def parse_rows(documents: dict[str, str]) -> list[Row]:
    """Task rows, read by column name from every table whose header has an ID column.
    Headings, prose and other tables between them mean nothing."""
    out: list[Row] = []
    for label, text in documents.items():
        titles, last = None, None
        for number, line in record_lines(text):
            if not line.startswith("|"):
                titles = last = None
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if last and all(re.fullmatch(r":?-+:?", c) for c in cells):
                names = [column(c) for c in last[1]]
                titles = last[1] if "id" in names else None
                missing = [c for c in COLUMNS[label] if c.lower() not in names]
                if titles and missing:
                    raise SakoError(f"{label}:{last[0]}: the table misses {', '.join(missing)}; "
                                    f"its header needs | {' | '.join(COLUMNS[label])} |")
                last = None
                continue
            last = (number, cells)
            names = [column(c) for c in titles] if titles else []
            first = cells[names.index("id")] if titles and len(cells) == len(titles) else cells[0]
            if not re.match(r"[Tt]-", first):
                continue
            if not titles or len(cells) != len(titles):
                raise SakoError(f"{label}:{number}: a task row needs a table header and as many cells as it has")
            if not TASK_ID.fullmatch(first):
                raise SakoError(f"{label}:{number}: malformed task ID {first!r}; use T- and a number, such as T-7")
            cell = dict(zip(names, cells))
            value = {name: "" if text in EMPTY_CELL else text for name, text in cell.items()}
            touches = [t.strip().strip("`").strip() for t in value.get("scope", "").split(",")]
            row = Row(first, label, number, titles, cell, task=value.get("task", ""),
                      done_when=value.get("done when", ""), source=value.get("source", ""),
                      touches=[t for t in touches if t and t not in EMPTY_CELL],
                      after=[a.strip() for a in value.get("after", "").split(",") if a.strip()])
            if label == "tasks":
                state = value.get("status", "").strip("*` ")
                word = state.split(" ")[0].rstrip(":") if state else "open"
                row.pri = value.get("pri", "").strip("*` ").upper() or "P2"
                row.status = "claimed" if MARKER.fullmatch(word) else word.lower()
                row.claim = state if row.status == "claimed" else ""
            else:
                row.status, row.pri, row.evidence = DONE, "", value.get("receipt", "")
                bracket = re.search(r"\[([^\[\]]*)\]\s*$", row.evidence)
                row.claim = bracket.group(1).split(";")[0].strip() if bracket else ""
            out.append(row)
    return out


def scope(value: str) -> tuple[str, ...]:
    """Normalize a repository-relative scope entry to path components."""
    text = value.strip().strip("`").strip()
    parts = tuple(p for p in text.replace("\\", "/").split("/") if p not in ("", "."))
    if ".." in parts or Path(text).is_absolute():
        raise SakoError(f"scope {text!r} escapes its root")
    return parts


def overlaps(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    shorter = min(len(a), len(b))
    return a[:shorter] == b[:shorter]


def scopes_overlap(first: Row, second: Row) -> bool:
    try:
        return any(overlaps(scope(a), scope(b)) for a in first.touches for b in second.touches)
    except SakoError:
        return False


def covers(row: Row, path: str) -> bool:
    try:
        return any(overlaps(tuple(Path(path).parts), scope(t)) for t in row.touches)
    except SakoError:
        return False


def row_faults(row: Row, known: set[str]) -> list[str]:
    """What is wrong with one row's values, each with its fix; such a row waits."""
    out = []
    if not row.task or not row.done_when:
        out.append(f"{row.id} needs a Task and a Done when; write both in its row")
    if row.doc == "tasks":
        if row.pri not in PRIORITIES:
            out.append(f'{row.id} has Pri "{row.pri}"; use P1, P2 or P3')
        if row.status not in ("open", "parked", "claimed"):
            out.append(f'{row.id} has status "{row.cells.get("status")}"; use open, parked, or claim it')
        for dependency in row.after:
            if dependency == row.id or dependency not in known:
                out.append(f"{row.id} names {dependency} in After, which is no other recorded task; fix the After cell")
        if len(set(row.after)) != len(row.after):
            out.append(f"{row.id} repeats a task in After; list each once")
    for touch in row.touches:
        try:
            scope(touch)
        except SakoError as error:
            out.append(f"{row.id}: {error}; fix its Scope")
    return out


def ledger_findings(rows: list[Row], live_markers: set[str] | None = None) -> list[str]:
    """Consistency findings over the records, each with its fix. Never edits rows."""
    out, seen, sources = [], {}, {}
    for row in rows:
        seen.setdefault(row.id, []).append(f"{row.doc}:{row.line}")
    for task_id, where in seen.items():
        if len(where) > 1:
            out.append(f"{task_id} is defined by {len(where)} rows ({', '.join(where)}); "
                       "IDs are unique and never reused: renumber or merge them")
    done = {r.id for r in rows if r.doc == DONE}
    live = {key_of(m) for m in live_markers} if live_markers is not None else None
    claimed = [r for r in rows if r.status == "claimed"]
    for row in rows:
        if row.source and sources.setdefault(row.source, row.id) != row.id:
            out.append(f"source {row.source!r} is mapped more than once: {sources[row.source]}, {row.id}; "
                       "keep one row per source")
        out += row_faults(row, set(seen))
        if row.status == "claimed":
            waiting = [d for d in row.after if d not in done]
            if waiting:
                out.append(f"{row.id} is claimed before {', '.join(waiting)} is done; finish those first")
            if live is not None and holder_key(row.claim) not in live:
                out.append(f"{row.id} is claimed by {row.claim.split()[0]}, which is not a live session; "
                           f"resume it, or take it over: claim {row.id} --takeover REASON")
    for index, row in enumerate(claimed):
        for other in claimed[index + 1:]:
            if holder_key(row.claim) != holder_key(other.claim) and scopes_overlap(row, other):
                out.append(f"{row.id} and {other.id} are both claimed and their scopes overlap; "
                           "finish one first or narrow the scopes")
    return out


def next_lines(rows: list[Row], live_markers: set[str] | None = None, brief: bool = False) -> list[str]:
    """What to start now: open, prerequisites done, no overlap with a claim; ranked by
    priority, then row order. The rest waits, with its reason."""
    done, known = {r.id for r in rows if r.doc == DONE}, {r.id for r in rows}
    live = {key_of(m) for m in live_markers} if live_markers is not None else None
    active = [r for r in rows if r.doc == "tasks"]
    ready, waiting = [], []
    for row in active:
        faults = row_faults(row, known)
        missing = [d for d in row.after if d not in done]
        holder = next((o for o in active if o is not row and o.status == "claimed" and scopes_overlap(row, o)), None)
        if faults:
            why = faults[0]
        elif row.status == "claimed":
            why = f"claimed by {row.claim}" + (" (not live)" if live is not None and holder_key(row.claim) not in live else "")
        elif row.status == "parked":
            why = row.cells.get("status", "parked")
        elif missing:
            why = "after " + ", ".join(missing)
        elif holder:
            why = f"scope overlaps {holder.id}, claimed by {holder.claim.split()[0]}"
        else:
            ready.append(row)
            continue
        waiting.append(f"  {row.id}  {why}")
    ready.sort(key=lambda r: PRIORITIES.index(r.pri))
    if ready:
        pick, others = ready[0], ready[1:]
        reason = pick.pri + (", first in row order" if others and others[0].pri == pick.pri else
                             f", the only ready {pick.pri} task" if others else ", the only ready task")
        out = [f"next: {pick.id} ({reason}): {pick.task}", f"  done when: {pick.done_when}",
               f"  scope: {', '.join(pick.touches)}"] + ([f"  source: {pick.source}"] if pick.source else [])
        if others:
            out.append("also ready: " + ", ".join(f"{r.id} ({r.pri})" for r in others))
        if waiting:
            out += [f"waiting: {len(waiting)}; run next for the reasons"] if brief else ["waiting:"] + waiting
        return out
    state = ("no recorded work: establish direction and one useful action with the selected planner or work source"
             if not rows else "work is held: inspect owners, prerequisites and findings before adding tasks"
             if any(r.status != "parked" for r in active) else "completed work is recorded: check whether the goal is "
             "satisfied" if done else "only parked work: review intent before adding tasks")
    return ["next: nothing is ready"] + ([] if brief else waiting) + [f"{state}.", "Follow the planner or work source "
            f"selected in project instructions. Without one, read No planner? Start here in {METHOD}."]


def read_ledger(roots: Roots, config: dict) -> list[Row]:
    """Task rows from the kit's records."""
    documents: dict[str, str] = {}
    for key in ("tasks", "done"):
        path = roots.kit / str(config[key])
        if path.exists():
            documents[key] = read_text(path, key)
        elif key == "tasks":
            raise SakoError(f"{KIT}/{config[key]} not found; install the kit with: uvx sako init")
    return parse_rows(documents)


# ----------------------------------------------------------------------------
# presence


def session_key(session_id: str) -> str:
    """Twelve hex characters derived from the session id; stable across resumes."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]


def marker_for(session_id: str, day: str | None = None) -> str:
    """The claim marker: sk-<month day of first start>-<session key>."""
    return f"sk-{day or time.strftime('%m%d')}-{session_key(session_id)}"


def key_of(marker: str | None) -> str | None:
    return marker.rsplit("-", 1)[-1] if marker else None


def holder_key(claim: str) -> str | None:
    """The session key of the marker that holds a row: the first token of the claim cell."""
    first = claim.split()[0] if claim.split() else ""
    return key_of(first) if first.startswith("sk-") else None


PID_NS = ""  # the PID namespace a PID means something in; its number is reused, so its first process's start is added
with suppress(OSError, IndexError):
    PID_NS = os.readlink("/proc/self/ns/pid")
    PID_NS += "@" + Path("/proc/1/stat").read_text().rsplit(")", 1)[1].split()[19]
LEASE = 24 * 3600  # seconds an entry no visible process vouches for counts as live after its last start


def presence_dir(roots: Roots) -> Path:
    return roots.state / "live"


def parent_of(pid: int) -> tuple[str, int]:
    """Read a process name and parent, including names containing spaces or parentheses."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.index("(") + 1:stat.rindex(")")], int(stat[stat.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        result = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                                capture_output=True, text=True)
        fields = result.stdout.split(None, 1)
        if result.returncode or len(fields) != 2:
            raise SakoError(f"process {pid} not found")
        return Path(fields[1].strip()).name.lstrip("-"), int(fields[0])  # macOS shows a login shell as -zsh


def agent_pid() -> int:
    """The long-lived agent process: climb past transient shells. SAKO_AGENT_PID overrides it. 0 when the
    climb ends at PID 1: inside a sandbox (Codex's) no host is visible, and init is never an agent. 0 on
    Windows, where SAKO reads no process: presence there is the 24-hour lease."""
    if sys.platform == "win32":
        return 0
    override = os.environ.get("SAKO_AGENT_PID")
    if override and override.isdigit():
        return int(override)
    pid = os.getppid()
    for _ in range(6):
        try:
            name, parent = parent_of(pid)
            if name not in ("sh", "bash", "dash", "zsh", "fish"):
                break
            pid = parent
        except (SakoError, OSError, ValueError):
            break
    return 0 if pid <= 1 else pid


def alive(pid: int) -> bool:
    """Ask whether a POSIX process exists without sending it a signal."""
    if pid <= 0 or sys.platform == "win32":  # on Windows os.kill(pid, 0) sends Ctrl+C to a process group
        return False
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError) as error:
        return isinstance(error, PermissionError)  # it exists, owned by someone else
    return True


def read_presence(roots: Roots) -> list[dict]:
    """Every live entry. A process visible here judges its entry, and a gone or unreadable one is removed. An
    entry from another PID namespace (a client's sandbox), or with no host, counts as live for LEASE after its
    last start, marked unverified; it stays on disk either way, so a resume keeps its starting facts."""
    entries, now = [], time.time()
    for path in sorted(presence_dir(roots).glob("*.json")):
        entry = read_json(path)
        pid = entry["pid"] if entry.get("marker") and isinstance(entry.get("pid"), int) else -1
        judged = pid < 0 or pid > 1 and entry.get("ns", PID_NS) == PID_NS
        if judged and alive(pid) or not judged and isinstance(entry.get("seen"), int) and entry["seen"] + LEASE > now:
            entries.append(entry if judged else dict(entry, unverified=True))
        elif judged:
            with suppress(OSError):
                path.unlink()
    return entries


def write_presence(roots: Roots, session_id: str) -> dict:
    """Write or refresh this session's entry; a resume keeps its starting facts. Raises SakoError
    when it cannot be written; presence_entry still builds it for rendering."""
    directory = presence_dir(roots)
    mine = directory / f"{session_key(session_id)}.json"
    entry = presence_entry(roots, session_id, read_json(mine))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        read_presence(roots)  # Sweep dead hosts; distinct conversations may share a live host.
        atomic_write(mine, json.dumps(entry, indent=1) + "\n")
    except OSError as error:
        raise SakoError(f"presence not recorded at {directory}: {error.strerror or error}") from None
    return entry


def presence_entry(roots: Roots, session_id: str, previous: dict | None = None) -> dict:
    """This session's entry as it would be written; a resume keeps its starting facts."""
    previous = previous or {}
    pid = agent_pid()
    pid = pid if alive(pid) else 0  # a host hidden from here, such as SAKO_AGENT_PID inside a sandbox, is none
    return {  # with no host visible (a sandbox), keep the one the hook recorded: better evidence than none
        "marker": previous.get("marker") or marker_for(session_id),
        "session_id": session_id,
        "pid": pid or previous.get("pid", 0), "ns": PID_NS if pid else previous.get("ns", ""), "seen": int(time.time()),
        "started": previous.get("started") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "started_at": previous.get("started_at") or int(time.time()),
        "work_root": str(roots.work),
        "branch": git(roots.work, "rev-parse", "--abbrev-ref", "HEAD", check=False) or "?",
        "work_start_rev": previous.get("work_start_rev") or git(roots.work, "rev-parse", "HEAD", check=False),
        "dirty_at_start": previous["dirty_at_start"] if "dirty_at_start" in previous else
        {path: fingerprint(roots.work, path) for path in product(dirty_paths(roots.work))},
    }


def remove_presence(roots: Roots, session_id: str) -> None:
    with suppress(OSError):
        (presence_dir(roots) / f"{session_key(session_id)}.json").unlink()


def my_entry(roots: Roots, session_id: str | None) -> dict | None:
    return (read_json(presence_dir(roots) / f"{session_key(session_id)}.json") or None) if session_id else None


# ----------------------------------------------------------------------------
# verification evidence


def product(paths) -> list[str]:
    """Paths outside the kit folder: SAKO's own files are never checked content."""
    return sorted(p for p in set(paths) if p and p != KIT and not p.startswith(KIT + "/"))


def covered_files(root: Path, paths: list[str]) -> list[str]:
    """Tracked and untracked-unignored files under paths, outside the kit folder."""
    listing: set[str] = set()
    for path in paths or [""]:
        if path and not (root / path).exists():
            raise SakoError(f"verify_paths entry {path!r} does not exist in the product")
        found = product(git(root, "ls-files", "-coz", "--exclude-standard", "--", *([path] if path else [])).split("\0"))
        if path and not found:
            raise SakoError(f"verify_paths entry {path!r} matches no tracked or untracked file")
        listing.update(found)
    return sorted(listing)


def content_id(root: Path, paths: list[str]) -> str:
    """A fingerprint of the covered content: each file's path, kind and bytes, sorted by path.
    A folder (a nested repository) is no checked content."""
    digest = hashlib.sha256()
    for relative in covered_files(root, paths):
        path = root / relative
        if path.is_symlink() or path.is_file():
            kind = "link" if path.is_symlink() else str(path.stat().st_mode & 0o111)
            digest.update(f"{relative}\0{kind}\0{fingerprint(root, relative)}\0".encode("utf-8", "surrogateescape"))
    return digest.hexdigest()[:16]


def command_id(config: dict) -> str:
    payload = json.dumps([VERSION, config["verify_command"], config["verify_paths"]], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def evidence_state(roots: Roots, config: dict) -> tuple[str, str]:
    """('none' | 'fresh' | 'stale', explanation) for the current product content."""
    if not config["verify_command"]:
        return "none", "no verify_command configured; SAKO keeps no check evidence"
    stamp = read_json(roots.local / "stamp.json")
    if not stamp:
        return "none", "no passing check recorded; run: sako.py verify"
    now = content_id(roots.work, list(config["verify_paths"]))
    if stamp.get("command_id") != command_id(config):
        return "stale", "the check command or paths changed since the last pass"
    if stamp.get("content_id") != now:
        return "stale", f"checked content moved since the last pass (was {stamp.get('content_id')}, now {now})"
    return "fresh", f"last pass {stamp.get('time')} on content {now}"


def verify(roots: Roots, config: dict, out=print) -> int:
    command = config["verify_command"]
    if not command:
        raise SakoError(f"{CONFIG} has no verify_command; nothing to run")
    path = roots.local / "stamp.json"
    path.unlink(missing_ok=True)  # a failing run must never leave a passing stamp behind
    paths = list(config["verify_paths"])
    listed = covered_files(roots.work, paths)
    before = content_id(roots.work, paths)
    started = time.time()
    try:
        result = subprocess.run(list(command), cwd=roots.work)
    except OSError as error:
        raise SakoError(f"verify_command {command[0]!r} cannot run: {error.strerror or error}") from None
    after = content_id(roots.work, paths)
    if result.returncode:
        out(f"check: FAILED (exit {result.returncode}) after {time.time() - started:.0f}s; no stamp written")
        return 1
    if before != after or command_id(config) != command_id(load_config(roots.kit)):
        made = sorted(set(covered_files(roots.work, paths)) - set(listed))  # often the check's own cache
        out("check: passed, but the checked content changed during the run " f"({before} -> {after}); run it again for a stamp"
            + (f". The run created {', '.join(made[:4])}: ignore generated files in .gitignore, or keep the check from "
               f"writing them ({PYTHON} -B for Python), so they are not checked content" if made else ""))
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = {"content_id": after, "command_id": command_id(config),
             "product_rev": git(roots.work, "rev-parse", "HEAD", check=False),
             "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sako": VERSION}
    atomic_write(path, json.dumps(stamp, indent=1) + "\n")
    out(f"check: PASS in {time.time() - started:.0f}s; covered {len(listed)} file(s); stamp {after} -> {path.as_posix()}")
    return 0


def changed_since(root: Path, start_rev: str, paths: list[str]) -> list[str]:
    """Paths changed since start_rev, committed, merged or not, plus new untracked files."""
    if not start_rev or not git(root, "rev-parse", "-q", "--verify", f"{start_rev}^{{commit}}", check=False):
        return ["(unknown starting revision; assuming changes)"]
    changed = set(git(root, "diff", "--name-only", start_rev, "--", *paths).splitlines())
    new = git(root, "ls-files", "-z", "--others", "--exclude-standard", "--", *paths).split("\0")
    return product(changed | {p for p in new if not p.endswith("/")})  # a nested repository is no checked content


# ----------------------------------------------------------------------------
# views


def dirty_paths(root: Path) -> list[str]:
    """Uncommitted paths (modified, added, deleted or untracked) in a worktree. Without rename
    detection a rename shows as its old and new path: it changes both scopes."""
    result = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all"],
                            capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if result.returncode:
        raise SakoError("cannot inspect uncommitted work: " + result.stderr.decode("utf-8", "replace").strip())
    return [item[3:] for item in result.stdout.decode("utf-8", "surrogateescape").split("\0") if len(item) > 3]


def my_rows(rows: list[Row], marker: str | None) -> list[Row]:
    """Rows whose claim cell carries this marker's session key."""
    key = key_of(marker)
    return [r for r in rows if key and r.claim and holder_key(r.claim) == key]


def peer_lines(roots: Roots, rows: list[Row], me: str | None) -> list[str]:
    lines = []
    for entry in read_presence(roots):
        if entry.get("marker") == me:
            continue
        held = my_rows(rows, entry.get("marker"))
        where = "; ".join(f"{r.id} ({r.status}) touches {', '.join(r.touches) or '?'}" for r in held) + \
            "  [from the claim column]" if held else "no claimed row yet; ask before touching shared paths"
        until = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["seen"] + LEASE)) if entry.get("unverified") else ""
        lines.append(f"  peer {entry.get('marker')} on {entry.get('branch')} since {entry.get('started')}"
                     + (f", unverified from here (counts as live until {until})" if until else "") + f" -> {where}")
    return lines


def fingerprint(root: Path, relative: str) -> str:
    """What a path holds now: file bytes, a symlink's target, a submodule's commit, or absent."""
    path = root / relative
    if path.is_symlink():
        data = b"link:" + os.readlink(path).encode("utf-8", "surrogateescape")
    elif path.is_file():
        data = path.read_bytes()
    elif path.is_dir():
        data = b"commit:" + git(path, "rev-parse", "HEAD", check=False).encode()
    else:
        return "absent"
    return hashlib.sha256(data).hexdigest()[:16]


def session_changes(roots: Roots, entry: dict | None) -> tuple[dict[str, str], str]:
    """Paths this session changed, each with a label: dirty now and not as they were at
    start, or in a commit made since the session started. Without a start record every
    dirty path counts and commits go unchecked; the note says so. A linked worktree nested
    here (a harness's) is another checkout; its work counts here once merged."""
    nested = {p.relative_to(roots.work).as_posix() + "/" for p in checkouts(roots) if roots.work in p.parents}
    dirty = [p for p in product(dirty_paths(roots.work)) if p not in nested]  # untracked only: a gitlink counts
    start, since = (entry or {}).get("dirty_at_start"), (entry or {}).get("started_at")
    if not isinstance(start, dict) or not isinstance(since, int):
        return ({path: "" if (roots.work / path).exists() or (roots.work / path).is_symlink() else "deleted"
                 for path in dirty}, "no start record for this session (start did not run, or its entry was "
                "swept); commits since you started were not checked")
    changed = {}
    for path in dirty:
        if path not in start:
            changed[path] = "" if (roots.work / path).exists() or (roots.work / path).is_symlink() else "deleted"
    for path, before in start.items():
        gone = path.endswith("/") and not (roots.work / path).is_dir()  # a nested checkout removed or moved away
        if path not in nested and not gone and fingerprint(roots.work, path) != before:
            changed[path] = "edited after start; already dirty at start"
    rev = str(entry.get("work_start_rev") or "")
    known = rev and git(roots.work, "rev-parse", "-q", "--verify", f"{rev}^{{commit}}", check=False)
    log = git(roots.work, "log", "--no-renames", "--name-only", "--format=%x00%h %s", f"--since=@{since}",
              f"{rev}..HEAD" if known else "HEAD", check=False)
    for block in log.split("\0")[1:]:
        subject, *paths = block.strip("\n").split("\n")
        short, _, title = subject.partition(" ")
        for path in product(paths):
            if path not in start:
                changed.setdefault(path, f'in commit {short} "{title}"')
    return changed, ""


def recorded(roots: Roots, rows: list[Row], entry: dict | None, marker: str | None,
             live: list[dict]) -> tuple[list[str], list[str], dict[str, str], str]:
    """The Recorded rule: (unrecorded, not-counted notes, changed, start note)."""
    changed, note = session_changes(roots, entry)
    own = [r for r in my_rows(rows, marker)]
    peers = {key_of(e["marker"]): e for e in live if e.get("marker") != marker}
    unrecorded, exempt = [], {}
    for path, label in sorted(changed.items()):
        if any(covers(row, path) for row in own):
            continue
        closer = next((r for r in rows if r.doc == DONE and r.claim and holder_key(r.claim) != key_of(marker)
                       and label.startswith("in commit") and covers(r, path)), None)
        if closer:
            exempt.setdefault(f"committed under {closer.id}, closed by {closer.claim}", []).append(path)
            continue
        holder = next((r for r in rows if holder_key(r.claim) in peers and covers(r, path)), None)
        if holder and peers[holder_key(holder.claim)].get("work_root") == str(roots.work):
            kind = "unverified" if peers[holder_key(holder.claim)].get("unverified") else "live"
            exempt.setdefault(f"in {holder.id}'s scope, claimed by {kind} peer {holder.claim.split()[0]} in this checkout",
                              []).append(path)
            continue
        if holder:
            label = (label + "; " if label else "") + f"claimed by {holder.claim.split()[0]} in another worktree, " \
                                                      "which cannot have changed this checkout"
        unrecorded.append(f"{path} ({label})" if label else path)
    notes = [f"not counted: {', '.join(paths)}: {why}; a shared checkout shows what changed, not who changed it."
             for why, paths in exempt.items()]
    return unrecorded, notes, changed, note


def runtime_path(roots: Roots, path: Path | None = None) -> str:
    """A kit path as an agent types it, the runtime by default: relative in the main checkout,
    absolute elsewhere."""
    path = path or roots.kit / "sako.py"
    return (path if roots.home != roots.work else path.relative_to(roots.home)).as_posix()


def footprint_lines(roots: Roots, config: dict, manifest: dict) -> list[str]:
    """Where the kit and records live; init prints these and status repeats them."""
    kit = runtime_path(roots, roots.kit)  # relative where it lives, absolute from a linked worktree
    home = f"{kit}/" + ("" if roots.home == roots.work else ", shared with this worktree")
    footprint, records = manifest["footprint"], f"Records: {kit}/{config['tasks']} and {Path(str(config['done'])).name}"
    listed = f"/{KIT}/".encode() in (ex.read_bytes().splitlines() if (ex := roots.main / ".git/info/exclude").exists() else [])
    lines = [f"SAKO installed in {home} ({footprint}). " + (f"{KIT}/ is tracked with the code; SAKO commits nothing."
             if footprint == "shared" else "Nothing tracked changed; " + ("the folder is listed in .git/info/exclude."
             if listed else f"add the line /{KIT}/ to {ex.as_posix() if roots.work != roots.main else '.git/info/exclude'}, or git status shows the folder."))]
    if footprint == "shared":
        return lines + [records + (", committed with the code; each branch has its own copy."
                                   if tracked(roots.home, MANIFEST) else f". Commit them with: {SHARE}")]
    if footprint == "repo":
        return lines + [records + f", in their own repository ({kit}/.git). Commit them with: git -C {shlex.quote(kit)} "
                        f"add -A && git -C {shlex.quote(kit)} commit -m \"SAKO records\""]
    return lines + [records + ". They are not in Git yet; git clean -x deletes them.",
                    "  Keep their history, or share them privately:  uvx sako init --repo [url]",
                    "  Commit them with the code:                    uvx sako init --shared"]


def hooks_line(roots: Roots, manifest: dict) -> str:
    """Written hooks are not proof of running ones: say when each client's last ran."""
    if not manifest["clients"]:
        return "Hooks: off; run start, check --gate and end yourself."
    seen = read_json(roots.local / "hooks.json")
    parts = []
    for agent in manifest["clients"]:
        if roots.worktree and not (roots.work / hook_file(agent)).exists():
            parts.append(f"{CLIENTS[agent]} not wired in this worktree (run: uvx sako init)")
        elif isinstance(seen.get(agent), dict):
            parts.append(f"{CLIENTS[agent]} last ran {seen[agent].get('time')} ({seen[agent].get('event')})")
        else:
            parts.append(f"{CLIENTS[agent]} not seen yet" + (", runs only once you trust it in Codex"
                                                             if agent == "codex" else ""))
    return "Hooks: " + "; ".join(parts) + "."


def stray(roots: Roots) -> list[str]:
    """Footprint findings: a stray kit folder, and the recorded footprint against Git."""
    found = [f"stray {KIT}/ in this worktree is ignored; SAKO uses {roots.kit.as_posix()}"] if roots.home != roots.work and (roots.work / KIT).exists() else []
    footprint = str(read_json(roots.home / MANIFEST).get("footprint") or "local")
    actual = "shared" if tracked(roots.home, MANIFEST) else "repo" if (roots.kit / ".git/HEAD").is_file() else "local"
    if footprint == "shared" and not subprocess.run(["git", "-C", str(roots.home), "check-ignore", "-q", "--no-index",
                                                    MANIFEST]).returncode:
        found.append(f"footprint: Git ignores {KIT}/ here, so the shared records cannot be committed; delete the line "
                     f"that lists it (git check-ignore -v --no-index {MANIFEST} names it)")
    if actual != footprint:
        found.append(f"footprint: install.json records {footprint}, but Git shows {actual} (tracked {KIT}/ means shared, {KIT}/.git "
                     "means repo); fix: " + (SHARE.replace(" && ", ", then ") if footprint == "shared" else "uvx sako init" + (" --repo [url]" if footprint == "repo" else ", which names the way out")))
    return found


def render_start(roots: Roots, config: dict, entry: dict) -> str:
    rows = read_ledger(roots, config)
    live = {e["marker"] for e in read_presence(roots)}
    findings = stray(roots) + ledger_findings(rows, live)
    state, why = evidence_state(roots, config)
    rev, kit = entry["work_start_rev"], KIT if roots.home == roots.work else roots.kit.as_posix()
    lines = [f"sako {VERSION} · work {roots.work.as_posix()} ({entry['branch']} @ {rev[:9] if rev and rev != 'HEAD' else 'no commits yet'})",
             DOOR.format(kit),
             f"kit: {roots.kit.as_posix()}; method {kit}/SAKO.md; records {kit}/{config['tasks']} and {kit}/{config['done']}",
             f"commands: {PYTHON} {shlex.quote((roots.kit / 'sako.py').as_posix())} <verb> "
             f"--session {shlex.quote(entry['session_id'])}",
             f"your marker: {entry['marker']}  (use claim to record task ownership)",
             f"session: {json.dumps(entry['session_id'])} (use this ID with --session)"]
    peers = peer_lines(roots, rows, entry["marker"])
    lines.append("peers: none recorded" if not peers else "live peers (disjoint: share the tree; "
                 "overlap: wait or negotiate):")
    lines += peers
    lines.append(f"evidence: {state}; {why}")
    if findings:
        lines.append(f"ledger: {len(findings)} finding(s)")
        lines += [f"  {f}" for f in findings]
    else:
        lines.append("ledger: consistent")
    lines += handoffs_line(roots, config, rows) + next_lines(rows, live, brief=True)
    return "\n".join(lines)


def handoff(roots: Roots, config: dict, row: Row) -> Path:
    """A task's handoff folder beside the records, found by its ID: work/T-<n>-<slug>/."""
    home = (roots.kit / str(config["tasks"])).parent
    found = sorted(p for p in home.glob(f"{row.id}-*") if p.is_dir())
    return found[0] if found else home / "-".join([row.id] + (re.findall(r"[a-z0-9]+", row.task.lower())[:4] or ["notes"]))


def handoffs_line(roots: Roots, config: dict, rows: list[Row]) -> list[str]:
    """Open tasks' handoff folders, for the session that picks the work up."""
    folders = [(r.id, handoff(roots, config, r)) for r in rows if r.doc == "tasks"]
    listed = [f"{i} {shlex.quote(runtime_path(roots, p))}/" for i, p in folders if p.is_dir()]
    return ["handoffs: " + ", ".join(listed)] if listed else []


def overview(roots: Roots, config: dict, session_id: str | None) -> tuple[list[Row], list[dict], list[str], dict | None, str | None]:
    """Rows, live presence, findings, and this session's entry and marker, for every view."""
    rows, live = read_ledger(roots, config), read_presence(roots)
    entry = my_entry(roots, session_id)
    marker = entry["marker"] if entry else (marker_for(session_id) if session_id else None)
    mine = {marker} if marker else set()  # the asking session is alive, even after its own end
    return rows, live, stray(roots) + ledger_findings(rows, {e["marker"] for e in live} | mine), entry, marker


def changes_line(roots: Roots, rows: list[Row], entry: dict | None, marker: str | None) -> str:
    unrecorded, notes, changed, note = recorded(roots, rows, entry, marker, read_presence(roots))
    return (f"changed this session: {len(changed)}" + (f"; unrecorded: {', '.join(unrecorded[:8])}" if unrecorded else "")
            + (f" ({note})" if note else ""))


def generated(root: Path, paths: list[str]) -> str:
    new = git(root, "ls-files", "--others", "--exclude-standard", "--", *paths).splitlines() if paths else []
    return f" New files among them ({', '.join(new[:4])}): delete or ignore any that is generated, such as build output or a cache." if new else ""


def gate(roots: Roots, config: dict, session_id: str | None) -> tuple[int, str]:
    """(exit code, message). 2 means: do not stop yet. Each problem names its rule and
    ends with its fix; notes that do not block follow."""
    rows, live, findings, entry, marker = overview(roots, config, session_id)
    problems = [f"Ledger: {f}" for f in findings]
    sako, session = f"{PYTHON} {shlex.quote(runtime_path(roots))}", shlex.quote(session_id or "ID")
    unrecorded, notes, changed, note = recorded(roots, rows, entry, marker, live)
    if unrecorded:
        first = unrecorded[0].split(" (")[0]
        task = next((r for r in rows if r.doc == "tasks" and r.status == "open" and covers(r, first)), None)
        fix = f'{sako} add "WHAT CHANGED" --done-when "HOW TO CHECK IT" --scope {shlex.quote(first)}, then claim it'
        fix = (f"if this change is {task.id} ({task.task}), claim it: {sako} claim {task.id} --session {session}; "
               f"otherwise record it: {fix}") if task else fix  # a covering scope alone does not make it that task
        problems.append(("Recorded: " + (f"{note}: uncommitted and" if note else "changed this session but") +
                         " covered by no task you claimed or closed: " + ", ".join(unrecorded[:8]) +
                         (" and more" if len(unrecorded) > 8 else "") + f"\n    fix: {fix}; for a change that is not "
                         "yours or stays unrecorded, say which and why in your reply."
                         + generated(roots.work, [u.split(" (")[0] for u in unrecorded])))
    mine = my_rows(rows, marker)
    closed = [r for r in mine if r.status == DONE]
    held = [r for r in mine if r.status == "claimed"]
    moved = changed_since(roots.work, (entry or {}).get("work_start_rev", ""), list(config["verify_paths"])) \
        if closed and config["verify_command"] else []
    if moved:
        state, why = evidence_state(roots, config)
        # A claim still held explains a stale check, so a close whose receipt shows a pass stays proven.
        working = any(covers(r, path) for r in held for path in moved)
        unproven = [r.id for r in closed if not working or "; check pass " not in r.evidence]
        if state != "fresh" and unproven:
            problems.append(f"Proven: {', '.join(unproven)} closed after checked content changed since you started, "
                            f"and the check is {state}: {why}\n    fix: {sako} verify, fix what it reports, then stop "
                            "again.")
    dirty = set(product(dirty_paths(roots.work)))
    for row in closed:
        pending = [path for path in sorted(changed) if path in dirty and covers(row, path)
                   and not any(covers(r, path) for r in held)]
        if pending:
            quoted, message = " ".join(map(shlex.quote, pending)), shlex.quote(f"{row.id}: {row.task}")
            problems.append(f"Committed: {row.id} is closed but its changes are not committed: {', '.join(pending[:8])}"
                            f"\n    fix: git add -- {quoted}, then git commit -m {message} when the owner has allowed "
                            f"commits in this session; otherwise ask, or say in your reply: {row.id} closed, commit "
                            "awaiting approval." + generated(roots.work, pending))
    footprint = str(read_json(roots.home / MANIFEST).get("footprint") or "local") if closed else "local"
    at = f"-C {shlex.quote(runtime_path(roots, roots.kit))} " if footprint == "repo" and (roots.kit / ".git/HEAD").is_file() else ""
    done = config["done"] if at else f"{KIT}/{config['done']}"
    records = [p for p in dirty_paths(roots.kit if at else roots.work) if p == done] if at or footprint == "shared" else []
    if records:
        ids = ", ".join(r.id for r in closed)
        problems.append(f"Committed: {ids} closed, but the receipts are not committed: {records[0]}\n    fix: "
                        f"git {at}add -A{'' if at else ' -- ' + KIT}, then git {at}commit -m {shlex.quote(ids + ' closed')} "
                        "when the owner has allowed commits in this session; otherwise ask, or say so in your reply.")
    for row in mine:
        folder = handoff(roots, config, row)
        if row.status == "claimed" and not (folder / "handoff.md").is_file():
            notes.append(f"advisory: {row.id} is claimed and unfinished; if you stop before closing it, write "
                         f"{shlex.quote(runtime_path(roots, folder / 'handoff.md'))}: where it stands, what landed, "
                         "what is left, the next step, and traps.")
        elif row.status == DONE and folder.is_dir():
            notes.append(f"advisory: {row.id} is closed and its handoff folder remains; move lasting facts to their "
                         f"home, then delete {shlex.quote(runtime_path(roots, folder))}/.")
    tail = notes + ([f"note: {note}."] if note and not unrecorded else [])
    if not problems:
        return 0, "\n".join(tail)
    return 2, "\n".join([f"SAKO gate: {len(problems)} problem(s) before you stop:"] + [f"  {p}" for p in problems] + tail)


def render_status(roots: Roots, config: dict, session_id: str | None) -> str:
    rows, _, findings, entry, marker = overview(roots, config, session_id)
    state, why = evidence_state(roots, config)
    manifest = installation(roots.home)
    lines = footprint_lines(roots, config, manifest) + [hooks_line(roots, manifest)]
    lines += [f"sako {VERSION} · work {roots.work.as_posix()}",
              f"config: {CONFIG} {'present' if (roots.kit / 'config.json').exists() else 'absent (defaults)'}"
              f" · verify_command {'set' if config['verify_command'] else 'not set'}",
              f"evidence: {state}; {why}"]
    if entry:
        lines.append(f"you: {marker} since {entry['started']} on {entry['branch']}; "
                     f"work started at {entry['work_start_rev'][:9]}")
        landed = git(roots.work, "log", "--format=%h %s", f"{entry['work_start_rev']}..HEAD",
                     check=False).splitlines() if entry.get("work_start_rev") else []
        lines.append("landed in the repository since you started: "
                     + (f"{len(landed)} commit(s)" if landed else "nothing"))
        lines += [f"  {c}" for c in landed[:12]]
    elif session_id:
        lines.append(f"you: {marker}; no presence entry (session ended or swept); rows and Git history "
                     "below are the durable record")
    held = my_rows(rows, marker)
    lines.append("your rows: " + ("; ".join(f"{r.id} ({r.status})" for r in held) if held else "none"))
    lines.append(changes_line(roots, rows, entry, marker))
    peers = peer_lines(roots, rows, marker)
    lines += ["peers:" if peers else "peers: none"] + peers
    lines += [f"ledger: {len(findings)} finding(s)" if findings else "ledger: consistent"] + [f"  {f}" for f in findings]
    return "\n".join(lines + handoffs_line(roots, config, rows))


# ----------------------------------------------------------------------------
# init: copy the kit into a repository


MANIFEST = ".sako/install.json"
METHOD = ".sako/SAKO.md"
RUNTIME = ".sako/sako.py"
SHARED_SKILL = ".agents/skills/sako/SKILL.md"
CLAUDE_SKILL = ".claude/skills/sako/SKILL.md"
ASSETS = {RUNTIME: "sako.py", METHOD: "SAKO.md", SHARED_SKILL: "skills/sako/SKILL.md",
          CLAUDE_SKILL: "skills/sako/SKILL.md"}
CLIENTS = {"claude": "Claude Code", "codex": "Codex"}
CODEX_ROOT = "# sako: start\n[sandbox_workspace_write]\nwritable_roots = [{}]\n# sako: end\n"
SHARE = f'git add -A -- {KIT} && git commit -m "Share SAKO records"'
HOOK_OWNED = r"(?<![\w.])\.sako/sako\.py(?![\w.]).*\bhook {}(?![\w-])"  # SAKO's hook for a client, any Python or redirect
DOOR = ("This project uses SAKO for task execution: before carrying out a project task, read {}/SAKO.md and follow its "
        "daily loop. Questions and discussion need no task record; project instructions and the owner's choices come first.")
POINTER = f"{DOOR.format(KIT)} Records: {KIT}/work/TASKS.md and DONE.md. Commands: python3 {KIT}/sako.py <verb> (python on Windows)."


def kit_root() -> Path:
    """The folder holding the running file and the kit assets: a checkout, or the
    installed package, where this file is sako/__init__.py."""
    here = Path(__file__).resolve().parent
    if (here / "templates").is_dir() and (here / "skills").is_dir():
        return here
    raise SakoError("init needs the SAKO package or a checkout; the copy in a project cannot seed one. "
                    "Run: uvx sako init")


def kit_text(kit: Path, relative: str) -> str:
    """Read one canonical asset; the runtime asset is the running file itself."""
    return read_text(Path(__file__).resolve() if relative == "sako.py" else kit / relative, "kit asset")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    """A JSON object from a disposable state file; anything unreadable counts as empty."""
    try:
        data = json.loads(retried(lambda: path.read_text(encoding="utf-8")))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def installation(main: Path) -> dict:
    path = main / MANIFEST
    if not path.exists():
        return {"version": VERSION, "footprint": "local", "files": {}, "clients": None}
    data = None
    with suppress(ValueError):
        data = json.loads(read_text(path, "installation manifest"))
    if isinstance(data, dict) and "clients" not in data:
        runtime = next((f for f in data.get("files") or {} if str(f).endswith("sako.py")), RUNTIME)
        raise SakoError(f"{MANIFEST} is from an older SAKO release, which this release does not convert; "
                        f"remove that kit with its own runtime ({PYTHON} {runtime} remove), then install this "
                        "release. If an older commit you checked out brought it back, switch back and run init again")
    try:
        if not (isinstance(data, dict) and data["footprint"] in ("local", "repo", "shared") and isinstance(data["version"], str)
                and isinstance(data["files"], dict) and {RUNTIME, METHOD} <= set(data["files"]) <= set(ASSETS)
                and all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in data["files"].values())
                and (data["clients"] is None or isinstance(data["clients"], list)
                     and set(data["clients"]) <= set(CLIENTS))):
            raise ValueError("invalid record shape")
    except (ValueError, TypeError, KeyError):
        raise SakoError(f"{MANIFEST} is invalid; restore it or reinstall the kit") from None
    return data


def hook_file(agent: str) -> str:
    return ".claude/settings.local.json" if agent == "claude" else ".codex/hooks.json"


def hook_command(agent: str) -> str:
    """Find the main checkout's copy through Git from any folder. A missing kit exits 1,
    which clients show without blocking; exit 2 on Stop would trap the session."""
    return ('c="$(git rev-parse --path-format=absolute --git-common-dir)"; k="${c%/.git}/.sako/sako.py"; '
            '[ -f "$k" ] || { echo "sako: no kit at $k; run: uvx sako init" >&2; exit 1; }; '
            f'exec {PYTHON} "$k" hook {agent}')


def edit_hooks(text: str, agent: str, remove: bool = False, where: str = "") -> str:
    try:
        data = json.loads(text or "{}")
        hooks = data.setdefault("hooks", {})  # other shapes fail with AttributeError here or below
        command, owned = hook_command(agent), re.compile(HOOK_OWNED.format(agent))
        for event in ("SessionStart", "SessionEnd", "Stop"):
            entries = hooks.get(event, [])
            if not isinstance(entries, list) or not all(isinstance(e.get("hooks"), list) for e in entries):
                raise ValueError("invalid record shape")
            clean = []
            for entry in entries:
                remaining = [h for h in entry["hooks"] if not owned.search(str(h.get("command")))]
                if remaining or not entry["hooks"]:
                    clean.append(dict(entry, hooks=remaining))
            if not remove:
                quiet = " >/dev/null" if agent == "codex" and event != "SessionStart" else ""
                timeout = 3 if agent == "codex" and event == "SessionEnd" else 10 if event != "Stop" else 20
                clean.append({"hooks": [{"type": "command", "command": command + quiet, "timeout": timeout}]})
            if clean:
                hooks[event] = clean
            else:
                hooks.pop(event, None)
        if not hooks:
            data.pop("hooks", None)
        return json.dumps(data, indent=2) + "\n"
    except (ValueError, TypeError, AssertionError, AttributeError):
        raise SakoError(f"{where or hook_file(agent)} has invalid hook settings; reconcile it before installing "
                        "or removing hooks") from None


def note_hook(roots: Roots, agent: str, event: str) -> None:
    """Record that a client's hook ran here, so status can tell written hooks from running ones."""
    path = roots.local / "hooks.json"
    seen = read_json(path)
    seen[agent] = {"time": time.strftime("%Y-%m-%d %H:%M"), "event": event}
    with suppress(OSError):
        atomic_write(path, json.dumps(seen, indent=1) + "\n")


def tracked(root: Path, relative: str) -> bool:
    return bool(git(root, "ls-files", "--", relative, check=False))


def exclude(roots: Roots, patterns: list[str], drop: tuple[str, ...] = ()) -> list[str]:
    """List paths in Git's shared exclude file, so nothing SAKO writes shows as a change, and drop
    the ones a footprint tracks. Returns the lines it could not add: a sandbox can keep .git/ read-only."""
    path = roots.main / ".git" / "info" / "exclude"
    data = path.read_bytes() if path.exists() else b""
    lines = data.decode("utf-8", "surrogateescape").splitlines(True)
    if any(line.rstrip("\r\n") in [f"/{p}" for p in drop] for line in lines):
        data = "".join(line for line in lines if line.rstrip("\r\n") not in [f"/{p}" for p in drop]).encode("utf-8", "surrogateescape")
        atomic_write(path, data)
    present = {line.rstrip("\r\n") for line in lines}
    missing = [f"/{p}" for p in patterns if f"/{p}" not in present]
    if missing:
        block = "\n".join(([] if "# sako" in present else ["# sako"]) + missing) + "\n"
        try:
            atomic_write(path, data + (b"\n" if data and not data.endswith(b"\n") else b"")
                         + block.encode("utf-8", "surrogateescape"))
        except OSError:
            return missing
    return []


def checkouts(roots: Roots) -> list[Path]:
    listing = git(roots.main, "worktree", "list", "--porcelain")  # realpath, not resolve: a symlink loop never raises
    return [Path(os.path.realpath(line[9:])) for line in listing.splitlines() if line.startswith("worktree ")]


def client_path(root: Path, relative: str, notes: list[str]) -> Path | None:
    """A client file's real path, or None with a note when its folder leads outside the
    repository: SAKO writes and deletes client files only inside it."""
    try:
        return inside(root, relative)
    except SakoError:
        notes.append(f"{relative} leads outside this repository, so SAKO left it alone.")
        return None


def wiring(roots: Roots, checkout: Path, clients: list[str], remove: bool = False,
           add: bool = True) -> tuple[dict[Path, str], list[str]]:
    """The client files one checkout needs: hooks for the chosen clients, SAKO's entries gone
    for the others, and in a linked worktree Codex's writable root for the kit folder. With
    add off, only removals happen. A tracked file is never changed; a note says what to do."""
    writes: dict[Path, str] = {}
    notes = []
    for agent in CLIENTS:
        relative, want = hook_file(agent), agent in clients and not remove and (agent != "codex" or sys.platform != "win32")
        current = read_text(checkout / relative, "hook settings") if (checkout / relative).exists() else ""
        if want and not add or not want and not re.search(HOOK_OWNED.format(agent), current) \
                or not (path := client_path(checkout, relative, notes)):
            continue
        proposed = edit_hooks(current, agent, remove=not want, where=str(path))
        if json.loads(proposed) == json.loads(current or "{}"):
            continue
        if tracked(checkout, path.relative_to(checkout.resolve()).as_posix()):
            notes.append(f"{CLIENTS[agent]}: {relative} is tracked, so SAKO left it unchanged. Add or remove "
                         "its hooks there yourself, or run start, check --gate and end explicitly.")
        else:
            writes[path] = proposed
    relative, want = ".codex/config.toml", "codex" in clients and not remove
    current = read_text(checkout / relative, "Codex settings") if (checkout / relative).exists() else ""
    if checkout.resolve() != roots.home and (want and add or not want and "# sako: start" in current) \
            and (path := client_path(checkout, relative, notes)):
        rest = re.sub(r"# sako: start\n.*?# sako: end\n", "", current, flags=re.S)
        proposed = rest if not want else rest + ("\n" if rest and not rest.endswith("\n") else "") + \
            CODEX_ROOT.format(json.dumps(str(roots.kit), ensure_ascii=False))
        if proposed != current and (tracked(checkout, relative) or want and re.search(
                r"^\s*(\[\s*sandbox_workspace_write\s*\]|sandbox_workspace_write\s*[.=])", rest, re.M)):
            notes.append(f"Codex: {relative} is tracked or has its own sandbox settings, so " + (
                f"make {roots.kit.as_posix()} writable yourself with writable_roots there, or start Codex with --add-dir "
                f"{shlex.quote(roots.kit.as_posix())}" if want else "remove SAKO's block from it yourself"))
        elif proposed != current:
            writes[path] = proposed
    return writes, notes


def apply(writes: dict[Path, str]) -> list[Path]:
    """Write what differs; a settings file left empty is deleted. If a write fails, every
    file already written goes back, so no hook points at a kit that is not there."""
    changed = [p for p, text in writes.items() if not p.exists() or read_text(p, str(p)) != text]
    before = {p: p.read_bytes() if p.exists() else None for p in changed}
    done = []
    try:
        for path in changed:
            if writes[path].strip() in ("", "{}"):
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, writes[path])
            done.append(path)
    except BaseException:
        for path in done:
            if before[path] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, before[path])
        raise
    return changed


def listing(items: list[str]) -> str:
    return ", ".join(items[:-1]) + (" and " if len(items) > 1 else "") + items[-1]


def detect(root: Path) -> tuple[list[str], str]:
    """The clients with a signal here, and the signals: the Claude Code or Codex session running init,
    each client's own files, and AGENTS.md, which both clients read."""
    found = [(f"this {CLIENTS[c]} session", [c]) for c, name in (("claude", "CLAUDECODE"), ("codex", "CODEX_THREAD_ID"))
             if os.environ.get(name)]
    for name, clients in ((".claude/", ["claude"]), ("CLAUDE.md", ["claude"]), (".codex/", ["codex"]),
                          ("AGENTS.md (read by both)", ["claude", "codex"])):
        path = root / name.split()[0].rstrip("/")
        if path.is_dir() if name.endswith("/") else path.exists() or path.is_symlink():
            found.append((name, clients))
    return sorted({c for _, cs in found for c in cs}), listing([n for n, _ in found]) if found else ""


def hooks_written(roots: Roots, clients: list[str], reason: str) -> str:
    """The client choice, why it was made, and how to change it. Codex runs Windows hooks in PowerShell; SAKO's hook is POSIX."""
    clients, windows = ([c for c in clients if c != "codex"], " Codex on Windows: no hooks written; run start, check --gate and end "
                        "yourself.") if sys.platform == "win32" and "codex" in clients else (clients, "")
    change = (f" Change the client with init --client in {roots.main.as_posix()}." if roots.worktree else
              " Change the client with: uvx sako init --client claude|codex|none.")
    if not clients:
        return (f"Hooks: none, {reason}. Agents read {SHARED_SKILL}; run start, check --gate and end yourself "
                f"({PYTHON} {shlex.quote(runtime_path(roots))} start --session <id>).{change}{windows}")
    return "Hooks written: " + listing([f"{CLIENTS[a]} ({hook_file(a)}" + (
        ", which runs them only after you trust them in Codex" if a == "codex" else "") + ")" for a in clients]) + \
        f", chosen from {reason}. Start a session to begin.{change}{windows}"


def init(target: Path, clients: list[str], out=print, update: bool = False, footprint: str | None = None,
         url: str | None = None) -> None:
    kit = kit_root()
    roots = resolve_roots(target)
    if roots.worktree:
        if clients or update or footprint:
            raise SakoError(f"in a linked worktree init only wires what the main checkout chose; "
                            f"run init with options in {roots.main.as_posix()}")
        return wire_worktree(roots, out)
    fresh, url = not roots.kit.exists(), str(Path(url).resolve()) if url and Path(url).exists() else url
    try:
        if url and fresh:  # a fresh checkout takes the records, their settings and .gitignore from their repository
            git(roots.main, "clone", "-q", url, str(roots.kit))
        config = load_config(roots.kit)
        with ledger_lock(roots, True, "install"):
            install(roots, kit, config, clients, out, update, footprint, url)
    except BaseException:
        if fresh:
            shutil.rmtree(roots.kit, ignore_errors=True)
        raise


def adopt(roots: Roots, footprint: str, url: str | None) -> list[str]:
    """Git work for the chosen footprint; a refusal names the command to run by hand from the main checkout."""
    nested = (roots.kit / ".git/HEAD").is_file()
    if nested and footprint != "repo":
        raise SakoError(f"{KIT}/.git holds the records' history, which the {footprint} footprint does not keep. Stay with "
                        f"init --repo, or move it away first: mv {KIT}/.git ../{roots.main.name}-records.git, then run init again")
    if footprint != "repo":
        return []
    if url and not nested and git(roots.main, "ls-remote", "--heads", url):
        raise SakoError(f"{url} already holds records, and so does {KIT}/ here; keep one. For the remote's: mv {KIT} ../{roots.main.name}"
                        "-sako-local and run this command again. For these: init --repo without a URL, commit, add the remote, merge with Git.")
    notes = [] if nested else [f"Created the records repository {KIT}/.git."]
    nested or git(roots.main, "init", "-q", str(roots.kit))
    origin = git(roots.kit, "remote", "get-url", "origin", check=False)
    if url and not origin:
        git(roots.kit, "remote", "add", "origin", url)
    elif url and origin != url:
        notes.append(f"The records remote stays origin {origin}; to use {url}: git -C {KIT} remote set-url origin {url}")
    return notes + ([f"Records remote: origin {url}; push with: git -C {KIT} push -u origin HEAD"] if url and not origin else [])


def install(roots: Roots, kit: Path, config: dict, chosen: list[str], out, update: bool,
            footprint: str | None = None, url: str | None = None) -> None:
    if (footprint or read_json(roots.main / MANIFEST).get("footprint")) != "shared" and git(roots.main, "ls-files", "--", KIT, check=False):
        raise SakoError(f"files in {KIT}/ are tracked here, and only the shared footprint keeps them in Git. Untrack them first "
                        f"(the folder stays on disk): git rm -r --cached {KIT} && git commit, then run init again")
    old = installation(roots.main)
    footprint = footprint or old["footprint"]
    if old["files"] and old["version"] != VERSION and not update:
        raise SakoError("a different SAKO version is installed; review the change and use init --update")
    if "none" in chosen and len(set(chosen)) > 1:
        raise SakoError("--client none stands alone: name the clients to wire, or none")
    if chosen:
        clients, reason = sorted(set(chosen) - {"none"}), "your choice" if "none" in chosen else "--client"
    elif old["clients"] is not None:
        clients, reason = old["clients"], "your recorded choice"
    else:
        clients, reason = detect(roots.main)
        reason = reason or "no client found here"
    desired = [RUNTIME, METHOD, SHARED_SKILL] + ([CLAUDE_SKILL] if "claude" in clients else [])
    writes, managed, notes, deletes, kept = {}, dict(old["files"]), [], [], set()
    # Preflight the entire change before writing any file.
    for relative in dict.fromkeys(desired):
        path = inside(roots.main, relative) if relative.startswith(KIT) else client_path(roots.main, relative, notes)
        if not path:
            managed.pop(relative, None)
            continue
        kept.add(path)
        if tracked(roots.main, path.relative_to(roots.main).as_posix()) and not (footprint == "shared" and relative.startswith(KIT)):
            managed.pop(relative, None)
            notes.append(f"{relative} is tracked, so SAKO left it unchanged.")
            continue
        proposed = kit_text(kit, ASSETS[relative])
        current = read_text(path, "installed asset") if path.exists() else None
        if current is not None and digest(current) not in (managed.get(relative), digest(proposed)):
            raise SakoError(f"{relative} has local content; inspect its diff and preserve it before replacing it")
        if current is None or update:
            writes[path], managed[relative] = proposed, digest(proposed)
        else:
            managed[relative] = digest(current)
    for relative in [r for r in managed if r not in desired]:
        expected, path = managed.pop(relative), client_path(roots.main, relative, notes)
        if not path or path in kept or not path.exists() or tracked(roots.main, path.relative_to(roots.main).as_posix()):
            continue
        if digest(read_text(path, relative)) != expected:
            raise SakoError(f"{relative} is no longer part of this kit and has local content; keep what "
                            "you need, delete it, then run init again")
        deletes.append(path)
    if not (roots.kit / str(config["tasks"])).exists():
        writes[roots.kit / str(config["tasks"])] = kit_text(kit, "templates/TASKS.md")
    else:
        read_ledger(roots, config)
    if not (roots.kit / "config.json").exists():
        writes[roots.kit / "config.json"] = json.dumps(config, indent=2) + "\n"
    kept_ignore = tracked(roots.kit, ".gitignore") if footprint == "repo" and (roots.kit / ".git/HEAD").is_file() else \
        footprint == "shared" and tracked(roots.home, f"{KIT}/.gitignore")  # the records' repository owns it then
    if footprint != "local" and not kept_ignore:
        writes[roots.kit / ".gitignore"] = ("/sako.py\n/SAKO.md\n/install.json\n" if footprint == "repo" else "") + "/state/\n"
    record = clients if chosen or old["clients"] is not None or clients else None
    manifest = {"version": VERSION if update or not old["files"] else old["version"], "footprint": footprint,
                "files": managed, "clients": record}
    writes[roots.main / MANIFEST] = json.dumps(manifest, indent=2) + "\n"
    for checkout in checkouts(roots):
        wired, more = wiring(roots, checkout, clients, add=checkout.resolve() == roots.main)
        writes.update(wired)
        notes += more
    new = [p.relative_to(roots.main).as_posix() for p in writes if not p.exists() and roots.main in p.parents]
    if footprint == "shared" and old["footprint"] != "shared":
        notes.append(f"Add this line to your project instructions, so an agent on a fresh clone finds SAKO before init: {POINTER}")
    notes += (adopted := adopt(roots, footprint, url))
    unlisted = exclude(roots, ([] if footprint == "shared" else [KIT + "/"]) + [r for r in new if not r.startswith(KIT + "/")],
                       (KIT + "/",) if footprint == "shared" else ())
    changed = apply(writes)
    for path in deletes:
        path.unlink(missing_ok=True)
        with suppress(OSError):
            path.parent.rmdir()
    if not changed and not deletes and not unlisted and not adopted:
        out(f"SAKO in {KIT}/ is current; nothing changed.")
        return
    if footprint != "shared" and git(roots.main, "rev-list", "-1", "--all", "--", MANIFEST, check=False):
        notes.append(f"Commits here track {MANIFEST} (the shared footprint on another branch, or an older release); "
                     f"checking one out replaces kit files, and {KIT}/ stays ignored there. Run init again after that.")
    if unlisted:
        notes.append("Could not write .git/info/exclude (read-only here); add these lines to it, or git status "
                     "shows SAKO's files: " + " ".join(unlisted))
    for line in footprint_lines(roots, config, manifest) + [hooks_written(roots, clients, reason)] + notes:
        out(line)


def wire_worktree(roots: Roots, out) -> None:
    """A linked worktree gets wiring only: its own hook settings and, for Codex, a writable
    root for the main checkout's kit folder. The records stay in the main checkout."""
    manifest = installation(roots.home)
    if not manifest["files"]:
        raise SakoError(f"install SAKO in the main checkout first: cd {shlex.quote(roots.main.as_posix())} && uvx sako init")
    writes, notes = wiring(roots, roots.work, manifest["clients"] or [])
    unlisted = exclude(roots, [p.relative_to(roots.work).as_posix() for p in writes if not p.exists()])
    apply(writes)
    out(f"This worktree uses its own {KIT}/ (shared): each branch keeps its own records. Nothing tracked changed."
        if roots.home == roots.work else f"This worktree uses SAKO in {roots.kit.as_posix()}/ ({manifest['footprint']}); its "
        "records stay there. Nothing tracked changed.")
    if unlisted:
        notes.append("Could not write .git/info/exclude (read-only here); add these lines to it: " + " ".join(unlisted))
    reason = "the main checkout's record" if manifest["clients"] else "none recorded in the main checkout"
    for line in [hooks_written(roots, manifest["clients"] or [], reason)] + stray(roots) + notes:
        out(line)


def cmd_remove(args: argparse.Namespace) -> int:
    roots, _ = resolve(args)
    if roots.worktree:
        raise SakoError(f"run remove in the main checkout {roots.main.as_posix()}; it also clears the hooks of its worktrees")
    if not (roots.main / MANIFEST).exists():
        raise SakoError(f"no {MANIFEST} in this repository; nothing to remove")
    with ledger_lock(roots, True, "install"):
        manifest, deletes, notes = installation(roots.main), [], []
        shared, ahead = manifest["footprint"] == "shared", git(roots.kit, "log", "--branches", "--not", "--remotes",
                                                                "--oneline", check=False) if (roots.kit / ".git/HEAD").is_file() else ""
        if ahead:  # unpushed commits, or all of them when the records repository has no remote
            notes.append(f"The records repository has {len(ahead.splitlines())} commit(s) that exist only here; push them "
                         f"with: git -C {KIT} push -u origin HEAD, after git -C {KIT} remote add origin <url> if it has no remote.")
        for relative, expected in manifest["files"].items():
            path = client_path(roots.main, relative, notes)
            if not path or path in deletes or tracked(roots.main, path.relative_to(roots.main).as_posix()) \
                    and not (shared and relative.startswith(KIT)):
                continue
            if path.exists() and digest(read_text(path, relative)) != expected:
                raise SakoError(f"{relative} has local changes; preserve them before removing the kit")
            deletes.append(path)
        writes: dict[Path, str] = {}
        for checkout in checkouts(roots):
            writes.update(wiring(roots, checkout, [], remove=True)[0])
        apply(writes)
        for path in deletes + [roots.main / MANIFEST]:
            path.unlink(missing_ok=True)
            with suppress(OSError):
                path.parent.rmdir()
    shutil.rmtree(roots.state, ignore_errors=True)
    print("\n".join(notes + [f"Removed the SAKO kit and its hooks. {CONFIG} and {KIT}/work/ keep your records; "
                              f"delete {KIT}/ to leave no trace."] + ([f"{KIT}/ is tracked: commit the removal with git add -A -- "
        f"{KIT} && git commit, or take the records out of Git too with git rm -r --cached {KIT} && git commit."] if shared else [])))
    return 0


# ----------------------------------------------------------------------------
# command line


def resolve(args: argparse.Namespace) -> tuple[Roots, dict]:
    if hasattr(args, "resolved"):
        return args.resolved
    roots = resolve_roots(Path.cwd())
    return roots, load_config(roots.kit)


def need_session(args: argparse.Namespace) -> str:
    if not args.session:
        raise SakoError(f"{args.command} needs --session <id>, SAKO_SESSION, or the session ID of Claude Code or Codex "
                        "(not both at once, as when one client runs inside the other)")
    return str(args.session)


def cmd_check(args: argparse.Namespace) -> int:
    roots, config = resolve(args)
    if args.gate:
        code, message = gate(roots, config, need_session(args))
        if code:
            print(message, file=sys.stderr)
        else:
            print("SAKO gate: clear" + (f"\n{message}" if message else ""))
        return code
    rows, _, findings, entry, marker = overview(roots, config, args.session)
    state, why = evidence_state(roots, config)
    print(f"evidence: {state}; {why}")
    if args.session:
        print(changes_line(roots, rows, entry, marker))
    print("\n".join([f"ledger: {len(findings)} finding(s)" if findings else "ledger: consistent"] + [f"  {f}" for f in findings]))
    return 1 if findings else 0


def cell(text: str, what: str) -> str:
    if not text.strip() or any(c in text for c in "|\r\n"):
        raise SakoError(f"{what} must be non-empty and on one line, without |")
    return text.strip()


def row_line(titles: list[str], cells: dict[str, str]) -> str:
    """One row in its table's column order; columns SAKO does not own keep their cells."""
    return "| " + " | ".join(cells.get(column(title)) or "-" for title in titles) + " |"


def insert_row(text: str, cells: dict[str, str], titles: list[str]) -> tuple[str, list[str]]:
    """Append a row to the file's first task table, or start that table with titles."""
    lines = text.splitlines()
    for number, line in record_lines(text):
        if line.startswith("|") and number < len(lines) and re.fullmatch(r"\|[\s:|-]+", lines[number].strip()):
            found = [c.strip() for c in line.strip("|").split("|")]
            if "id" in map(column, found):
                end = number + 1
                while end < len(lines) and lines[end].strip().startswith("|"):
                    end += 1
                lines.insert(end, row_line(found, cells))
                return "\n".join(lines).rstrip() + "\n", found
    lines += ["", "| " + " | ".join(titles) + " |", "|" + "---|" * len(titles), row_line(titles, cells)]
    return "\n".join(lines).rstrip() + "\n", titles


def receipt_part(roots: Roots, config: dict, marker: str) -> str:
    """Who closed, when, and what the configured check said at that moment."""
    check = "no automated check"
    if config["verify_command"]:
        stamp, (state, _) = read_json(roots.local / "stamp.json"), evidence_state(roots, config)
        if state == "fresh":
            check = f"check pass {stamp['time']} on {stamp['content_id']}"
        elif stamp:
            now = content_id(roots.work, list(config["verify_paths"]))
            check = f"check stale: last pass {stamp.get('time')} on {stamp.get('content_id')}, now {now}"
        else:
            check = "no passing check recorded"
    return f"[{marker}; closed {time.strftime('%Y-%m-%d')}; {check}]"


def add_task(rows: list[Row], active: Path, text: str, records: str, args: argparse.Namespace) -> int:
    source = cell(args.source, "--source") if args.source is not None else ""
    if args.source is not None and source in EMPTY_CELL:
        raise SakoError("--source needs a stable reference, not a dash")
    after = sorted(set(args.after or []))
    for dependency in after:
        if dependency not in {r.id for r in rows}:
            raise SakoError(f"--after {dependency} needs an existing task, done ones included")
    pri = (args.pri or "P2").upper()
    if pri not in PRIORITIES:
        raise SakoError("--pri takes P1, P2 or P3")
    task, done_when = cell(args.task, "the task"), cell(args.done_when, "--done-when")
    touches = [cell(t, "--scope") for t in args.scope]
    for touch in touches:
        if "," in touch:
            raise SakoError("a scope path cannot contain a comma in the Markdown format")
        scope(touch)
    existing = [r for r in rows if source and r.source == source]
    if existing:
        if len(existing) != 1:
            raise SakoError(f"source {source!r} is mapped more than once; reconcile the records before retrying")
        old = existing[0]
        if (old.task, old.done_when, set(map(scope, old.touches)), set(old.after)) != (
                task, done_when, set(map(scope, touches)), set(after)):
            raise SakoError(f"source {source!r} already maps to {old.id} with a different task, done when, scope or "
                            "prerequisites; reconcile that row, or use a distinct source for different work")
        if args.pri and old.doc == "tasks" and old.pri != pri:
            lines = text.splitlines()
            lines[old.line - 1] = row_line(old.titles, dict(old.cells, pri=pri))
            atomic_write(active, "\n".join(lines).rstrip() + "\n")
            print(f"{old.id}: priority {old.pri} -> {pri}; reused source {source!r}")
            return 0
        print(f"{old.id}: {old.status}; reused source {source!r}; records unchanged")
        return 0
    number = 1 + max([int(x[2:]) for x in TASK_ID.findall(records)] or [0])
    cells = {"id": f"T-{number}", "pri": pri, "task": task, "done when": done_when, "status": "open",
             "scope": ", ".join(f"`{t}`" for t in touches), "after": ", ".join(after), "source": source}
    atomic_write(active, insert_row(text, cells, list(COLUMNS["tasks"]))[0])
    print(f"T-{number}: open; recorded locally, not committed")
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    roots, config = resolve(args)
    rows = read_ledger(roots, config)
    active = roots.kit / str(config["tasks"])
    text = read_text(active, "tasks")
    if args.command == "add":
        closed = roots.kit / str(config["done"])
        return add_task(rows, active, text, text + (read_text(closed, "done") if closed.exists() else ""), args)
    session, present = need_session(args), read_presence(roots)
    live = {key_of(e["marker"]) for e in present}
    seen = {key_of(e["marker"]) for e in present if not e.get("unverified")}  # judged by a process visible here
    entry = my_entry(roots, session)
    if not entry or key_of(entry["marker"]) not in live:
        raise SakoError(f"session {session!r} has not started here; pass --session with the ID the start context printed, "
                        "or run start first (presence SAKO cannot verify, as in a sandbox, lasts 24 hours after a start)")
    mine, done = key_of(entry["marker"]), {r.id for r in rows if r.doc == DONE}
    matches = [r for r in rows if r.id == args.task_id and r.doc == "tasks"]
    if len(matches) != 1:
        raise SakoError(f"{args.task_id} needs exactly one row in {KIT}/{config['tasks']}")
    row = matches[0]
    faults = row_faults(row, {r.id for r in rows})
    if faults:
        raise SakoError(faults[0])
    waiting = [d for d in row.after if d not in done]
    if waiting:
        raise SakoError(f"{row.id} waits for {', '.join(waiting)}; finish those first")
    owner, lines = holder_key(row.claim), text.splitlines()
    if args.command == "claim":
        if not row.touches:
            raise SakoError(f"{row.id} needs a Scope before claiming")
        if row.status == "parked":
            raise SakoError(f"{row.id} is parked; set its status to open once the reason is resolved")
        if row.status == "claimed" and owner != mine:
            if owner in seen:
                raise SakoError(f"{row.id} is held by a live session; choose another task")
            if not args.takeover:
                raise SakoError(f"{row.id} is held by {row.claim.split()[0]}, " + ("which SAKO cannot verify from here (a sandbox, "
                                "or Windows, hides its process); if it stopped," if owner in live else "a stale claim;")
                                + " inspect its handoff and use --takeover <reason>")
        for other in rows:
            if other.id != row.id and other.status == "claimed" and holder_key(other.claim) != mine \
                    and scopes_overlap(row, other):
                raise SakoError(f"{row.id}'s scope overlaps claimed {other.id}; wait or narrow the scopes")
        unverified = ", unverified" if owner != mine and owner in live - seen else ""  # its process was hidden from here
        if owner != mine:
            note = f" (took over from {row.claim.split()[0]}{unverified}: {cell(args.takeover, '--takeover')})" if args.takeover and row.claim else ""
            lines[row.line - 1] = row_line(row.titles, dict(row.cells, status=entry["marker"] + note))
            atomic_write(active, "\n".join(lines).rstrip() + "\n")
        print(f"{row.id}: claimed" + ("; its previous holder could not be verified from here" if unverified else "") + "; recorded "
              f"locally, not committed. If it pauses or needs working notes, keep them in {shlex.quote(runtime_path(roots, handoff(roots, config, row)))}/ "
              "(handoff.md for where it stands).")
        return 0
    if row.status != "claimed" or owner != mine:
        raise SakoError(f"{row.id} must be claimed by this session before closing")
    if config["verify_command"] and changed_since(roots.work, entry["work_start_rev"], list(config["verify_paths"])):
        state, why = evidence_state(roots, config)
        if state != "fresh":
            raise SakoError(f"evidence is {state}: {why}; verify before closing")
    receipt = f"{cell(args.evidence, '--evidence')} {receipt_part(roots, config, entry['marker'])}"
    closed = roots.kit / str(config["done"])
    previous = [r for r in rows if r.id == row.id and r.doc == DONE]
    if previous and holder_key(previous[0].claim) != mine:
        raise SakoError(f"{row.id} already has a done row from another session; reconcile it before closing")
    dropped = []
    if not previous:
        own = {c.lower() for c in COLUMNS["tasks"]}
        titles = list(COLUMNS["done"]) + [t for t in row.titles if column(t) not in own]
        history = read_text(closed, "done") if closed.exists() else "# Done\n"
        done_text, titles = insert_row(history, dict(row.cells, receipt=receipt), titles)
        kept = set(map(column, titles))
        dropped = [f"{t}={row.cells[column(t)]}" for t in row.titles if column(t) not in own | kept
                   and row.cells[column(t)] not in EMPTY_CELL]
        # Append first, then remove: an interrupted close leaves both rows, and
        # repeating the same command finishes it without losing the record.
        atomic_write(closed, done_text)
    del lines[row.line - 1]
    atomic_write(active, "\n".join(lines).rstrip() + "\n")
    print(f"{row.id}: done; recorded locally, not committed" +
          (f"; not carried, the done file has no such column: {', '.join(dropped)}" if dropped else ""))
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    try:
        payload = json.loads("" if sys.stdin.isatty() else sys.stdin.read())
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    event = str(payload.get("hook_event_name") or args.event or "")
    session_id = str(payload.get("session_id") or args.session or "")
    if not session_id:
        print("sako: hook input carries no session_id; nothing recorded", file=sys.stderr)
        return 0
    try:
        roots, config = resolve(args)
        if args.client:
            note_hook(roots, args.client, event)
        with ledger_lock(roots):
            if event == "SessionStart":
                try:
                    entry = write_presence(roots, session_id)
                except SakoError as error:
                    print(f"sako: {error}; peers will not see this session")
                    entry = presence_entry(roots, session_id)
                print(render_start(roots, config, entry))
            elif event == "SessionEnd":
                remove_presence(roots, session_id)
            elif event == "Stop":
                if payload.get("stop_hook_active"):
                    return 0
                code, message = gate(roots, config, session_id)
                if code:
                    print(message, file=sys.stderr)
                return code
            return 0
    except (SakoError, OSError) as error:  # OSError: a file another program holds open, on Windows
        if event == "Stop" and not payload.get("stop_hook_active"):
            print(f"SAKO gate: {error}\nFix what it names before stopping, or say why you are leaving it.", file=sys.stderr)
            return 2
        print(f"sako: {error}")
        return 0
    except Exception as error:  # a bug in the runtime must never wedge a session
        print(f"sako: unexpected error during {event or 'hook'}, skipped: {error!r}", file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # UTF-8 everywhere: Windows pipes default to cp1252
        with suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="sako.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"sako {VERSION}")
    parser.set_defaults(session=None, gate=False, event=None, client=None)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, handler, **kwargs) -> argparse.ArgumentParser:
        p = sub.add_parser(name, **kwargs)  # every verb takes --session, so the printed command line works for all
        p.add_argument("--session", help="your session identifier (the marker is derived from it)")
        p.set_defaults(handler=handler)
        return p

    add("start", lambda a: print(render_start(*resolve(a), write_presence(resolve(a)[0], need_session(a)))),
        help="record presence and print the session context")
    add("end", lambda a: remove_presence(resolve(a)[0], need_session(a)), help="remove your presence entry")
    p = add("check", cmd_check, help="ledger, claims, scopes and evidence")
    p.add_argument("--gate", action="store_true", help="exit 2 when the session should not stop yet")
    add("verify", lambda a: verify(*resolve(a)), help="run the project's check command and stamp the content")
    add("status", lambda a: print(render_status(*resolve(a), a.session)), help="roots, presence, rows, evidence, what landed")
    add("next", lambda a: print("\n".join(next_lines(read_ledger(*resolve(a)), {e["marker"] for e in read_presence(
        resolve(a)[0])}))), help="the task to start now and why, what else is ready, and what waits")
    p = add("add", cmd_task, help="allocate and append one task under the ledger lock")
    p.add_argument("task")
    p.add_argument("--done-when", required=True, help="the observable completion condition")
    p.add_argument("--scope", action="append", required=True, help="a path prefix the task may change; repeat")
    p.add_argument("--pri", help="P1, P2 (default) or P3; on repeat intake it updates the priority")
    p.add_argument("--source", help="stable source item reference; matching intake reuses its task, done ones included")
    p.add_argument("--after", action="append", help="existing prerequisite task ID; repeat for multiple dependencies")
    for name in ("claim", "close"):
        p = add(name, cmd_task, help=f"{name} a task under the local ledger lock")
        p.add_argument("task_id")
        p.add_argument("--takeover" if name == "claim" else "--evidence", required=name == "close")
    p = add("hook", cmd_hook, help="adapter entry reading hook JSON on stdin")
    p.add_argument("client", nargs="?", choices=sorted(CLIENTS), help="the client whose hook this is")
    p.add_argument("--event", help="event name when the input carries none")
    add("remove", cmd_remove, help="remove unmodified kit assets and its integration entries; preserve project records")
    p = sub.add_parser("init", help="install the kit in this repository, or wire this worktree; no flags needed")
    p.add_argument("--client", action="append", choices=["claude", "codex", "none"], help="wire this client "
                   "instead of the detected ones; repeat for both; none means no hooks. The choice is kept")
    p.add_argument("--update", action="store_true", help="replace unmodified kit files with this release's; records stay")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--repo", nargs="?", const="", metavar="URL", help="records in their own repository inside .sako/; a URL is cloned or added as remote")
    g.add_argument("--shared", action="store_true", help="records committed with the code; prints the commit command")
    p.set_defaults(handler=lambda a: init(Path.cwd(), a.client or [], update=a.update, url=a.repo or None,
                                          footprint="shared" if a.shared else "repo" if a.repo is not None else None))

    args = parser.parse_args(argv)
    clients = [v for v in (os.environ.get("CLAUDE_CODE_SESSION_ID"), os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")) if v]
    args.session = args.session or os.environ.get("SAKO_SESSION") or (clients[0] if len(clients) == 1 else None)  # nested: neither
    try:
        if args.command in ("start", "end", "check", "status", "next", "add", "claim", "close", "verify"):
            args.resolved = resolve(args)
            with ledger_lock(args.resolved[0], args.command in ("add", "claim", "close", "verify"),
                             "verify" if args.command == "verify" else "ledger"):
                return int(args.handler(args) or 0)
        return int(args.handler(args) or 0)
    except SakoError as error:
        print(f"sako: {error}", file=sys.stderr)
        return 3
    except OSError as error:
        hint = ("; another program may have it open: close that, then retry" if sys.platform == "win32" else
                "; if a sandbox keeps the kit folder read-only (Codex in a linked worktree), trust the project in Codex "
                "and run uvx sako init in that worktree outside the sandbox, or start Codex with --add-dir <main checkout>/.sako"
                ) if error.errno in (errno.EROFS, errno.EACCES, errno.EPERM) else ""
        print(f"sako: {error.strerror or error}: {error.filename2 or error.filename or ''}{hint}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # a bug: say so plainly, keep the traceback for the report
        import traceback
        traceback.print_exc()
        print(f"sako: unexpected error: {error!r} (this is a bug; please report it)", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
