"""
Magik Mega Panel Lite - persistent floating-panel counter
Based on SuperBatcheur 2.0, stripped of image/text loading functionality.
"""

import server
import json
import os
import threading
import time
import uuid
import asyncio
import errno
import logging
import math
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Any, Tuple


class StatePersistenceError(RuntimeError):
    """Persistence failed; execution must not claim a successful counter output."""


class StateCorruptionError(StatePersistenceError):
    """Saved state cannot be trusted without recovery."""


class MagikMegaPanelLite:
    """
    Sequential index counter controlled by a compact floating panel.
    Pure batch index management — no file loading.
    """

    DESCRIPTION = (
        "Advances a persisted index each run. "
        "The floating panel target drives the Set Next value directly — "
        "no separate target input is needed."
    )

    STATE_FILE = Path(os.path.expanduser("~/.comfyui_magik_mega_panel_lite_state.json"))
    _lock = threading.Lock()
    LOCK_TIMEOUT_SECONDS = 30
    LOCK_POLL_SECONDS = 0.05

    # STATE_FILE is shared by every MagikMegaPanelLite node ever created, across
    # every workflow — each gets its own entry keyed by a workflow-persisted UUID. Deleting a
    # node (or a whole workflow) doesn't remove its entry, so without pruning
    # this file would grow forever. Two rules, applied together on every
    # write:
    #   - age:   entries untouched for PRUNE_MAX_AGE_DAYS are dropped. Generous
    #            on purpose — a workflow you just haven't opened in a while
    #            shouldn't lose its place.
    #   - count: a hard backstop — if the file still exceeds PRUNE_MAX_ENTRIES
    #            even after the age rule, the oldest-touched entries beyond
    #            the cap are dropped regardless of age.
    PRUNE_MAX_AGE_DAYS = 90
    PRUNE_MAX_ENTRIES  = 2000

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # ── Knob INT (first / top) ──────────────────────────────────
                "knobValue": (
                    "INT",
                    {
                        "default": 1,
                        "min": 0,
                        "max": 999999,
                        "step": 1,
                    },
                ),
                # ── Batcheur controls ───────────────────────────────────────
                "freeze": ("BOOLEAN", {"default": False}),
                "padding": ("INT", {"default": 3, "min": 1, "max": 12}),
                "current_index": ("INT", {"default": 1, "min": 0}),
                "reset_trigger": ("INT", {"default": 0}),
                "state_id": ("STRING", {"default": ""}),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (
        "INT",       # batch index
        "STRING",    # padded index
    )
    RETURN_NAMES = (
        "index",
        "padded_index",
    )
    FUNCTION = "process"
    CATEGORY = "custom"

    # All state I/O occurs inside _state_transaction: an in-process lock
    # plus an OS lock on a stable sidecar (never the atomically replaced JSON).

    @staticmethod
    def _state_key(state_id: str) -> str:
        try:
            return str(uuid.UUID(state_id))
        except (ValueError, TypeError, AttributeError):
            raise ValueError(
                "Magik Mega Panel Lite requires a valid state_id UUID. Reload the "
                "workflow with the updated frontend, or supply a UUID in API prompts."
            ) from None

    @classmethod
    @contextmanager
    def _state_transaction(cls):
        deadline = time.monotonic() + cls.LOCK_TIMEOUT_SECONDS
        if not cls._lock.acquire(timeout=cls.LOCK_TIMEOUT_SECONDS):
            raise StatePersistenceError("Magik Mega Panel Lite: timed out waiting for the state lock.")
        lock_file = None
        acquired = False
        try:
            cls.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock_path = cls.STATE_FILE.with_name(cls.STATE_FILE.name + ".lock")
            lock_file = open(lock_path, "a+b")
            if os.name == "nt":
                import msvcrt
                # Lock the first byte, with the same file offset in every process.
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
            elif os.name == "posix":
                import fcntl
            else:
                raise StatePersistenceError("Magik Mega Panel Lite: this platform has no supported state file lock.")
            while True:
                try:
                    if os.name == "nt":
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    if time.monotonic() >= deadline:
                        raise StatePersistenceError(
                            f"Magik Mega Panel Lite: another process holds {lock_path}; lock wait timed out."
                        ) from exc
                    time.sleep(cls.LOCK_POLL_SECONDS)
            yield
        except OSError as exc:
            raise StatePersistenceError(
                f"Magik Mega Panel Lite: state file operation failed for {cls.STATE_FILE}: {exc}"
            ) from exc
        finally:
            try:
                if lock_file is not None:
                    try:
                        if acquired:
                            if os.name == "nt":
                                lock_file.seek(0)
                                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    finally:
                        lock_file.close()
            finally:
                cls._lock.release()

    @staticmethod
    def _json_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StateCorruptionError(f"Duplicate state JSON key: {key!r}")
            result[key] = value
        return result

    @staticmethod
    def _json_constant(value):
        raise StateCorruptionError(f"Non-finite JSON value: {value}")

    @classmethod
    def _load_json(cls, path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle, object_pairs_hook=cls._json_object, parse_constant=cls._json_constant)
        except (ValueError, UnicodeError) as exc:
            raise StateCorruptionError(f"Invalid state JSON in {path}: {exc}") from exc

    @staticmethod
    def _valid_entry(key, entry):
        if not isinstance(key, str) or not key or not isinstance(entry, dict):
            return False
        if type(entry.get("index")) is not int or entry["index"] < 0:
            return False
        if type(entry.get("last_reset", 0)) is not int:
            return False
        if "last_output" in entry and (type(entry["last_output"]) is not int or entry["last_output"] < 0):
            return False
        if "ts" in entry:
            ts = entry["ts"]
            try:
                if type(ts) not in (int, float) or not math.isfinite(ts) or ts < 0:
                    return False
            except OverflowError:
                return False
        return True

    @classmethod
    def _validate_state(cls, state):
        if not isinstance(state, dict):
            raise StateCorruptionError("State JSON must contain an object of counter entries.")
        invalid = [key for key, entry in state.items() if not cls._valid_entry(key, entry)]
        if invalid:
            raise StateCorruptionError(f"Invalid counter entries: {', '.join(map(repr, invalid))}")
        return state

    @classmethod
    def _atomic_write(cls, path, state):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(state, handle, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            temporary = None
            if os.name == "posix":
                descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except (OSError, ValueError, TypeError) as exc:
            raise StatePersistenceError(
                f"Magik Mega Panel Lite could not durably save {path}: {exc}. "
                "Execution stopped; if replacement completed before the error, verify the saved index before retrying."
            ) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def _preserve_corrupt_file(cls):
        archive = cls.STATE_FILE.with_name(
            cls.STATE_FILE.name + f".corrupt.{time.time_ns()}.{uuid.uuid4().hex}.json"
        )
        # Never replace a corrupt file until its original bytes are preserved.
        with open(cls.STATE_FILE, "rb") as source, open(archive, "xb") as target:
            target.write(source.read())
            target.flush()
            os.fsync(target.fileno())
        return archive

    @classmethod
    def _read_state_file_nolock(cls) -> Dict[str, Any]:
        backup = cls.STATE_FILE.with_name(cls.STATE_FILE.name + ".bak")
        main_missing = False
        try:
            main = cls._load_json(cls.STATE_FILE)
            try:
                return cls._validate_state(main)
            except StateCorruptionError as exc:
                problem = exc
        except FileNotFoundError:
            main_missing = True
            main = None
            problem = StateCorruptionError("Primary state file is missing.")
        except StateCorruptionError as exc:
            main = None
            problem = exc
        try:
            recovered = cls._validate_state(cls._load_json(backup))
        except FileNotFoundError as exc:
            if main_missing:
                return {} # Neither file contains a saved snapshot yet.
            raise StateCorruptionError(
                f"Magik Mega Panel Lite: {problem} No recovery backup exists. "
                "Original state is unchanged; restore or repair it before running."
            ) from exc
        except StateCorruptionError as exc:
            raise StateCorruptionError(
                f"Magik Mega Panel Lite: {problem} Backup is also invalid: {exc}. "
                "Neither file was overwritten; restore or repair the state before running."
            ) from exc
        if isinstance(main, dict):
            # Keep every healthy primary entry; recover only damaged entries.
            repaired = dict(main)
            invalid = [key for key, entry in main.items() if not cls._valid_entry(key, entry)]
            absent = [key for key in invalid if key not in recovered]
            if absent:
                raise StateCorruptionError(
                    f"Magik Mega Panel Lite: no backup for damaged entries {absent!r}. "
                    "Original state is unchanged; restore or repair it before running."
                )
            for key in invalid:
                repaired[key] = recovered[key]
            recovered = cls._validate_state(repaired)
        archive = None if main_missing else cls._preserve_corrupt_file()
        cls._atomic_write(cls.STATE_FILE, recovered)
        logging.getLogger(__name__).warning(
            "Magik Mega Panel Lite recovered state from %s; affected counters may resume "
            "from the previous saved revision. Original damaged bytes: %s", backup, archive or "primary was missing"
        )
        return recovered

    @classmethod
    def _write_state_file_nolock(cls, state: Dict[str, Any]) -> None:
        cls._validate_state(state)
        backup = cls.STATE_FILE.with_name(cls.STATE_FILE.name + ".bak")
        try:
            previous = cls._validate_state(cls._load_json(cls.STATE_FILE))
        except FileNotFoundError:
            previous = {}
        # Preserve the previous known-good revision before committing new state.
        # A failed backup write prevents advancing the primary file.
        cls._atomic_write(backup, previous)
        cls._atomic_write(cls.STATE_FILE, state)

    @classmethod
    def _prune_state(cls, state: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        cutoff = now - cls.PRUNE_MAX_AGE_DAYS * 86400
        # Entries written before "ts" existed (or somehow missing it) are
        # treated as fresh rather than pruned on sight — default to `now`.
        state = {k: v for k, v in state.items() if v.get("ts", now) >= cutoff}
        if len(state) > cls.PRUNE_MAX_ENTRIES:
            newest_first = sorted(state.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
            state = dict(newest_first[: cls.PRUNE_MAX_ENTRIES])
        return state

    @classmethod
    def _read_state_dict(cls) -> Dict[str, Any]:
        with cls._state_transaction():
            return cls._read_state_file_nolock()

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        state = cls._read_state_dict()
        uid = cls._state_key(kwargs.get("state_id", ""))
        node_state = state.get(uid) or {}
        return (
            node_state.get("index"),
            node_state.get("last_reset"),
            kwargs.get("reset_trigger", 0),
            kwargs.get("freeze", False),
            kwargs.get("padding", 3),
            kwargs.get("current_index", 1),
            kwargs.get("knobValue", 1),
        )

    # ── Main execution ───────────────────────────────────────────────────────

    def process(
        self,
        knobValue: int = 1,
        freeze: bool = False,
        padding: int = 3,
        current_index: int = 1,
        reset_trigger: int = 0,
        unique_id: str = "",
        state_id: str = "",
    ) -> Tuple[int, str]:

        state_key = self._state_key(state_id)

        if type(current_index) is not int or current_index < 0:
            raise ValueError("Magik Mega Panel Lite current_index must be a nonnegative integer.")
        if type(reset_trigger) is not int:
            raise ValueError("Magik Mega Panel Lite reset_trigger must be an integer.")

        if type(padding) is not int or padding < 1 or padding > 12:
            padding = 3

        # Held for the whole read-modify-write so a concurrent execution of a
        # different node instance can't read this same file in between our
        # read and our write and silently lose its own update.
        with self._state_transaction():
            state = self._read_state_file_nolock()
            node_state = state.get(state_key)

            if node_state is None or reset_trigger != node_state.get("last_reset", 0):
                output_value = current_index
                next_value = current_index if freeze else current_index + 1
                node_state = {"index": next_value, "last_reset": reset_trigger}
            else:
                last_saved = node_state.get("index", current_index)
                output_value = last_saved
                next_value = last_saved if freeze else last_saved + 1
                node_state["index"] = next_value

            # last_output is the most recent value actually produced (as
            # opposed to "index", which is what process() will produce NEXT
            # time it runs). The state API route below uses it so the
            # frontend can recover true progress for a node it hasn't
            # personally heard an execution event from this session.
            node_state["last_output"] = output_value
            # Timestamp this entry as "touched now" — used by _prune_state to
            # garbage-collect entries from nodes that no longer exist in any
            # open workflow, so the state file doesn't grow forever.
            node_state["ts"] = time.time()

            new_next = node_state["index"]
            state[state_key] = node_state
            state = self._prune_state(state)
            self._write_state_file_nolock(state)

        padded_out = str(output_value).zfill(padding)

        # Notify frontend
        try:
            if server.PromptServer.instance:
                server.PromptServer.instance.send_sync("magik_mega_panel_lite_executed", {
                    "node_id":      unique_id,
                    "state_id":     state_key,
                    "current":      output_value,
                    "next":         new_next,
                    "freeze":       freeze,
                    "padded_index": padded_out,
                    "knob_value":   knobValue,
                    "padding":      padding,
                    "reset_trigger": reset_trigger,
                }, server.PromptServer.instance.client_id)
        except Exception as e:
            print(f"[MagikMegaPanelLite] Warning: {e}")

        return (output_value, padded_out)


# ── State API route ──────────────────────────────────────────────────────────
#
# Lets the frontend ask "what's the real persisted state for node X?" directly,
# instead of only ever finding out via the magik_mega_panel_lite_executed push event
# (which it can only have heard if this exact node executed at least once during
# the current page session). Used by:
#   - the per-node display, to recover true state immediately after a workflow
#     load/tab switch instead of showing stale defaults until the next run, and
#   - the queue controller widget, which may be tracking a *different*
#     MagikMegaPanelLite/SuperBatcheur2 node elsewhere in the graph and otherwise
#     has no reliable way to learn its current progress.
try:
    from aiohttp import web

    @server.PromptServer.instance.routes.get("/magik_mega_panel_lite/state/{state_id}")
    async def magik_mega_panel_lite_get_state(request):
        try:
            state_key = MagikMegaPanelLite._state_key(request.match_info.get("state_id", ""))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        try:
            state = await asyncio.to_thread(MagikMegaPanelLite._read_state_dict)
        except StatePersistenceError as exc:
            return web.json_response({"error": str(exc)}, status=503)
        node_state = state.get(state_key)
        if not node_state:
            return web.json_response({"found": False})
        return web.json_response({
            "found":       True,
            "index":       node_state.get("index"),
            "last_output": node_state.get("last_output"),
            "last_reset":  node_state.get("last_reset", 0),
        })
except Exception as e:
    print(f"[MagikMegaPanelLite] Could not register state route: {e}")
