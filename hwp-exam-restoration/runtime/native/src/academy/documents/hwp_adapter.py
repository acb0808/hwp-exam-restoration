"""Safe boundary around the preserved Hancom HWP/HWPX toolkit.

The upstream skill exposes a convenient ``HwpAgentToolkit`` API, but its PDF
helper can start a warm daemon which first terminates every process matching a
global HWP automation pattern.  That is unsafe for an academy workstation.
This adapter keeps structural/modifier/live selection in one place and requests
an adapter-owned COM session for any operation that needs Hancom.  The default
live factory uses direct ``DispatchEx`` rather than pyhwpx's
``EnsureDispatch``/running-object path; ownership is still verified by a
read-only Hwp.exe process preflight and a returned window-handle PID check
before mutation.  Unavailable or conflicting evidence stops before mutation.
It never calls the preserved daemon's startup or cleanup path.
"""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import inspect
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from xml.etree import ElementTree as ET

from .locks import AppSessionLock, LockBusyError


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_VENDOR_ROOT = _PROJECT_ROOT / "vendor" / "hwp"
_MAX_FORMULA_LENGTH = 20_000
_MAX_HWP_UNIT = 2_000_000
_HWP_UNITS_PER_INCH = 7_200
_MILLIMETERS_PER_INCH = 25.4
_VALID_MODES = {"structural", "modifier", "live"}
_HWP_XML_NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
}
_HWP_A4 = (59_528, 84_186)
_HWP_PAGE_SIZES = {
    "A3": (84_186, 119_055),
    "A4": _HWP_A4,
    "A5": (42_093, 59_528),
    "B4": (72_000, 101_800),
    "LETTER": (61_200, 79_200),
    "LEGAL": (61_200, 100_800),
}


class HwpAdapterError(RuntimeError):
    """Raised for a runtime or adapter-boundary failure."""


class HwpOwnershipError(HwpAdapterError):
    """Raised when a live HWP session cannot be proven adapter-owned."""


HwpOwnershipPreflight = Callable[[], bool]
HwpOwnershipVerifier = Callable[[Any], bool]


def validate_equation(equation: str, *, field: str = "equation") -> str:
    """Validate a LaTeX/HWP equation before it reaches the vendor toolkit."""

    if not isinstance(equation, str) or not equation.strip():
        raise ValueError(f"invalid_equation: {field} must be a non-empty string")
    if len(equation) > _MAX_FORMULA_LENGTH:
        raise ValueError(f"invalid_equation: {field} is too long")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in equation):
        raise ValueError(f"invalid_equation: {field} contains a control character")
    return equation


def validate_image_dimensions(width_hwpunit: int, height_hwpunit: int) -> tuple[int, int]:
    """Validate image dimensions in HWP units (not pixels, points, or mm)."""

    # bool is an int subclass but is never a meaningful document dimension.
    if isinstance(width_hwpunit, bool) or isinstance(height_hwpunit, bool):
        raise ValueError("invalid_image_dimensions: dimensions must be integers")
    if not isinstance(width_hwpunit, int) or not isinstance(height_hwpunit, int):
        raise ValueError("invalid_image_dimensions: dimensions must be integers")
    if not (1 <= width_hwpunit <= _MAX_HWP_UNIT and 1 <= height_hwpunit <= _MAX_HWP_UNIT):
        raise ValueError("invalid_image_dimensions: dimensions must be positive HWP units")
    return width_hwpunit, height_hwpunit


def _hwpunit_to_millimeters(value: int) -> float:
    """Convert the adapter's HWP-unit picture size to raw COM millimeters."""

    return round(value / _HWP_UNITS_PER_INCH * _MILLIMETERS_PER_INCH, 2)


def _safe_name(value: str, fallback: str = "document") -> str:
    value = re.sub(r"[^A-Za-z0-9가-힣._-]+", "-", value.strip())
    value = value.strip(".-")
    return value[:96] or fallback


def _resolve_output_dir(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _assert_input_file(source_path: str | os.PathLike[str]) -> Path:
    source = Path(source_path).expanduser().resolve()
    if source.suffix.lower() not in {".hwp", ".hwpx"}:
        raise ValueError("invalid_source: expected .hwp or .hwpx")
    if not source.is_file():
        raise FileNotFoundError(source)
    return source


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _check_output_paths(paths: Sequence[Path], output_dir: Path) -> None:
    for path in paths:
        resolved = path.resolve()
        if not _is_inside(resolved, output_dir):
            raise ValueError(f"output path escapes run directory: {path}")
        if path.exists():
            raise FileExistsError(path)


@contextmanager
def _vendor_import_path() -> Iterator[None]:
    """Make preserved top-level imports resolve without editing vendor bytes."""

    if not _VENDOR_ROOT.is_dir():
        raise HwpAdapterError(f"preserved HWP vendor root is missing: {_VENDOR_ROOT}")
    root = str(_VENDOR_ROOT)
    inserted = root not in sys.path
    if inserted:
        sys.path.insert(0, root)
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(root)
            except ValueError:
                pass


def _load_vendor_toolkit() -> Any:
    with _vendor_import_path():
        module = importlib.import_module("agent_toolkit")
    return module.HwpAgentToolkit


def _load_vendor_converter() -> Any:
    with _vendor_import_path():
        module = importlib.import_module("equation_converter")
    return module.EquationConverter


_HWP_PROCESS_NAMES = {"hwp", "hwp.exe"}


class _HwpProcessSnapshot(set[int]):
    """A set-compatible HWP PID snapshot with optional identity evidence."""

    def __init__(
        self,
        values: Sequence[int] = (),
        identities: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(values)
        self.identities: dict[int, dict[str, Any]] = {
            int(pid): dict(identity) for pid, identity in (identities or {}).items()
        }


def _is_hwp_process_info(info: Mapping[str, Any]) -> bool:
    names: list[str] = []
    for value in (info.get("name"), info.get("exe")):
        if value:
            names.append(Path(str(value)).name.lower())
    return any(name in _HWP_PROCESS_NAMES for name in names)


def _filetime_to_epoch(value: Any) -> float | None:
    try:
        ticks = (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)
        seconds = ticks / 10_000_000 - 11_644_473_600
    except (AttributeError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _windows_process_name_via_dotnet(process_id: int) -> str | None:
    """Read a Windows process name through .NET when Win32 access is denied."""

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        return None
    # Keep the script fixed and pass only a validated decimal PID as an
    # argument.  ProcessName is a read-only system enumeration and avoids
    # opening protected processes with any write-capable access.
    script = (
        "& { param($targetPid); "
        "$ErrorActionPreference='Stop'; "
        "$p=[System.Diagnostics.Process]::GetProcessById([int]$targetPid); "
        "[Console]::Out.Write($p.ProcessName) }"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script, str(process_id)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    value = str(getattr(completed, "stdout", "")).strip()
    if not value or any(ord(char) < 32 and char not in "\t\r\n" for char in value):
        return None
    return value.splitlines()[0].strip() or None


def _windows_process_identity(process_id: int) -> dict[str, Any] | None:
    """Read a Windows process image and creation time without mutating it.

    ``psutil`` can expose a protected process with an empty ``name``/``exe``
    record.  ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` plus
    ``QueryFullProcessImageNameW`` is a read-only OS identity query that can
    classify such a process.  Any inability to identify the process remains
    ``None`` so a caller can fail closed.
    """

    try:
        pid = int(process_id)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = getattr(kernel32, "OpenProcess")
        try:
            open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_process.restype = wintypes.HANDLE
        except Exception:
            pass
        # PROCESS_QUERY_LIMITED_INFORMATION is read-only.
        handle = open_process(0x1000, False, pid)
        if not handle:
            process_name = _windows_process_name_via_dotnet(pid)
            if not process_name:
                return None
            return {
                "pid": pid,
                "name": process_name,
                "exe": None,
                "create_time": None,
            }

        try:
            image_path: str | None = None
            query_image = getattr(kernel32, "QueryFullProcessImageNameW", None)
            if callable(query_image):
                try:
                    query_image.argtypes = [
                        wintypes.HANDLE,
                        wintypes.DWORD,
                        wintypes.LPWSTR,
                        ctypes.POINTER(wintypes.DWORD),
                    ]
                    query_image.restype = wintypes.BOOL
                except Exception:
                    pass
                buffer = ctypes.create_unicode_buffer(32_768)
                length = wintypes.DWORD(len(buffer))
                if query_image(handle, 0, buffer, ctypes.byref(length)):
                    image_path = buffer.value[: int(length.value)]

            # Older Windows builds and a few protected processes may reject
            # QueryFullProcessImageNameW while still allowing the psapi query.
            if not image_path:
                try:
                    psapi = ctypes.WinDLL("psapi", use_last_error=True)
                    get_image = getattr(psapi, "GetProcessImageFileNameW", None)
                except Exception:
                    get_image = None
                if callable(get_image):
                    try:
                        get_image.argtypes = [
                            wintypes.HANDLE,
                            wintypes.LPWSTR,
                            wintypes.DWORD,
                        ]
                        get_image.restype = wintypes.DWORD
                    except Exception:
                        pass
                    buffer = ctypes.create_unicode_buffer(32_768)
                    length = get_image(handle, buffer, len(buffer))
                    if length:
                        image_path = buffer.value[: int(length)]

            if not image_path:
                return None
            identity: dict[str, Any] = {
                "pid": pid,
                "name": Path(image_path).name,
                "exe": image_path,
                "create_time": None,
            }
            get_times = getattr(kernel32, "GetProcessTimes", None)
            if callable(get_times):
                try:
                    get_times.argtypes = [
                        wintypes.HANDLE,
                        ctypes.POINTER(wintypes.FILETIME),
                        ctypes.POINTER(wintypes.FILETIME),
                        ctypes.POINTER(wintypes.FILETIME),
                        ctypes.POINTER(wintypes.FILETIME),
                    ]
                    get_times.restype = wintypes.BOOL
                except Exception:
                    pass
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                if get_times(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                ):
                    identity["create_time"] = _filetime_to_epoch(creation)
            return identity
        finally:
            close_handle = getattr(kernel32, "CloseHandle", None)
            if callable(close_handle):
                try:
                    close_handle.argtypes = [wintypes.HANDLE]
                    close_handle.restype = wintypes.BOOL
                except Exception:
                    pass
                close_handle(handle)
    except Exception:
        return None


def _process_id(process: Any, info: Mapping[str, Any] | None = None) -> int | None:
    value = info.get("pid") if info is not None else None
    if value is None:
        value = getattr(process, "pid", None)
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _snapshot_hwp_process_ids() -> set[int] | None:
    """Read current HWP PIDs and identity evidence without OS mutation."""

    try:
        import psutil  # type: ignore
    except Exception:
        return None
    try:
        process_iter = psutil.process_iter(["pid", "name", "exe", "create_time"])
        result: set[int] = set()
        identities: dict[int, dict[str, Any]] = {}
        for process in process_iter:
            try:
                info = process.info
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except Exception as exc:
                denied = getattr(psutil, "AccessDenied", ())
                if denied and isinstance(exc, denied):
                    pid = _process_id(process)
                    native = _windows_process_identity(pid) if pid is not None else None
                    if native is None:
                        return None
                    info = native
                else:
                    return None
            if not isinstance(info, Mapping):
                return None
            identity_values = (info.get("name"), info.get("exe"))
            has_readable_identity = any(
                isinstance(value, (str, os.PathLike)) and str(value).strip()
                for value in identity_values
            )
            # Windows exposes PID 0 as a pseudo-process (for example,
            # ``System Idle Process``).  A known non-HWP image can be skipped
            # before requiring a positive PID; an unreadable identity still
            # fails closed below.
            if has_readable_identity and not _is_hwp_process_info(info):
                continue
            pid = _process_id(process, info)
            if pid is None:
                return None
            if not has_readable_identity:
                # Neither psutil identity field was readable.  Ask Windows
                # for the authoritative read-only image path instead of
                # treating a protected process as absent.
                native = _windows_process_identity(pid)
                if native is None and sys.platform == "win32":
                    # A protected non-HWP process may deny the image-path and
                    # creation-time queries while Windows still exposes its
                    # process name through the read-only .NET enumeration.
                    # That name is sufficient to exclude a confirmed
                    # non-HWP process; an unknown name remains fail-closed.
                    process_name = _windows_process_name_via_dotnet(pid)
                    if process_name:
                        native = {
                            "pid": pid,
                            "name": process_name,
                            "exe": None,
                            "create_time": None,
                        }
                if native is None:
                    return None
                info = {
                    **dict(info),
                    **{
                        key: value
                        for key, value in native.items()
                        if value is not None and (not isinstance(value, str) or value.strip())
                    },
                }
                if not any(
                    isinstance(value, (str, os.PathLike)) and str(value).strip()
                    for value in (info.get("name"), info.get("exe"))
                ):
                    return None
            if not _is_hwp_process_info(info):
                continue
            result.add(pid)
            identities[pid] = {
                "pid": pid,
                "name": info.get("name"),
                "exe": info.get("exe"),
                "create_time": info.get("create_time"),
            }
        return _HwpProcessSnapshot(result, identities)
    except Exception:
        return None


def _snapshot_identities(snapshot: Any) -> Mapping[int, Mapping[str, Any]]:
    if isinstance(snapshot, _HwpProcessSnapshot):
        return snapshot.identities
    if isinstance(snapshot, Mapping):
        identities: dict[int, Mapping[str, Any]] = {}
        for key, value in snapshot.items():
            try:
                pid = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, Mapping):
                identities[pid] = value
        return identities
    return {}


def _identity_create_time(identity: Mapping[str, Any]) -> float | None:
    value = identity.get("create_time")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _hwp_window_process_id(hwp: Any) -> int | None:
    """Read a pyhwpx window handle and resolve it to an owning process ID."""

    roots: list[Any] = []
    for candidate in (getattr(hwp, "hwp", None), hwp):
        if candidate is not None and all(candidate is not existing for existing in roots):
            roots.append(candidate)
    handle: Any = None
    for root in roots:
        windows = getattr(root, "XHwpWindows", None)
        active = getattr(windows, "Active_XHwpWindow", None) if windows is not None else None
        handle = getattr(active, "WindowHandle", None) if active is not None else None
        if callable(handle):
            try:
                handle = handle()
            except Exception:
                return None
        if handle:
            break
    try:
        hwnd = int(handle)
    except (TypeError, ValueError):
        return None
    if hwnd <= 0:
        return None
    try:
        from win32process import GetWindowThreadProcessId  # type: ignore

        result = GetWindowThreadProcessId(hwnd)
    except Exception:
        return None
    try:
        pid = result[1] if isinstance(result, (tuple, list)) else result
        pid = int(pid)
    except (TypeError, ValueError, IndexError):
        return None
    return pid if pid > 0 else None


def _default_hwp_ownership_verifier(
    hwp: Any,
    before_ids: set[int],
    *,
    factory_started_at: float | None = None,
) -> bool:
    """Verify a new HWP PID and reject identities created before activation."""

    pid = _hwp_window_process_id(hwp)
    if pid is None or pid in before_ids:
        return False
    after_ids = _snapshot_hwp_process_ids()
    if after_ids is None or pid not in after_ids:
        return False
    if not isinstance(after_ids, _HwpProcessSnapshot):
        # A PID delta without immutable identity evidence is insufficient: a
        # user process may have appeared in the preflight/factory race and the
        # COM constructor may have attached to it.
        return False
    identity = after_ids.identities.get(pid)
    if identity is None or not _is_hwp_process_info(identity):
        return False
    create_time = _identity_create_time(identity)
    if create_time is None:
        return False
    if factory_started_at is not None and create_time < factory_started_at:
        return False
    return True


def _vendor_toolkit_instance(toolkit: Any) -> bool:
    module_name = getattr(type(toolkit), "__module__", "")
    return module_name in {"agent_toolkit", "src.agent_toolkit"} or _VENDOR_ROOT.as_posix() in str(
        getattr(inspect.getmodule(type(toolkit)), "__file__", "")
    ).replace("\\", "/")


def _make_structural_toolkit(title: str) -> Any:
    toolkit_type = _load_vendor_toolkit()
    converter_type = _load_vendor_converter()
    bridge = _VENDOR_ROOT / "bridge" / "latex_to_hwpeqn.js"
    converter = converter_type(bridge_script=bridge)
    return _StructuralTemplateToolkit(toolkit_type(mode="structural", converter=converter, title=title))


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_find(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent.iter():
        if _xml_local_name(child.tag) == name:
            return child
    return None


def _xml_find_all(parent: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in parent.iter() if _xml_local_name(child.tag) == name]


def _hwp_units_from_mm(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_renderer_template_projection: {field} must be numeric")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"invalid_renderer_template_projection: {field} must be non-negative")
    units = int(round(float(value) / _MILLIMETERS_PER_INCH * _HWP_UNITS_PER_INCH))
    return max(minimum, units)


def _hwp_units_from_pt(value: Any, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_renderer_template_projection: {field} must be numeric")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(f"invalid_renderer_template_projection: {field} must be non-negative")
    # Char heights in header.xml are hundredths of a point, while paragraph
    # margins use HWP units.  Keep those units distinct at this boundary.
    return max(minimum, int(round(float(value) * 100)))


def _projection_page_values(projection: Mapping[str, Any]) -> tuple[int, int, dict[str, int], int, int]:
    page = projection.get("page", {})
    if not isinstance(page, Mapping):
        raise ValueError("invalid_renderer_template_projection: page must be a mapping")
    size_name = str(page.get("size", "A4")).strip().upper()
    if size_name not in _HWP_PAGE_SIZES:
        raise ValueError(f"invalid_renderer_template_projection: unsupported page size {size_name!r}")
    width, height = _HWP_PAGE_SIZES[size_name]
    orientation = str(page.get("orientation", "portrait")).strip().lower()
    if orientation not in {"portrait", "landscape"}:
        raise ValueError("invalid_renderer_template_projection: orientation must be portrait or landscape")
    if orientation == "landscape":
        width, height = height, width
    margins = page.get("margins_mm", {})
    if not isinstance(margins, Mapping):
        raise ValueError("invalid_renderer_template_projection: margins_mm must be a mapping")
    margin_units = {
        side: _hwp_units_from_mm(margins.get(side, 16), field=f"page.margins_mm.{side}")
        for side in ("top", "right", "bottom", "left")
    }
    # Refuse a projection that would leave a negative text area.  This keeps a
    # malformed template from producing a HWPX that opens with a clipped page.
    if margin_units["left"] + margin_units["right"] >= width:
        raise ValueError("invalid_renderer_template_projection: horizontal margins exceed page width")
    if margin_units["top"] + margin_units["bottom"] >= height:
        raise ValueError("invalid_renderer_template_projection: vertical margins exceed page height")
    columns = page.get("columns", 1)
    if isinstance(columns, bool) or not isinstance(columns, int) or not 1 <= columns <= 8:
        raise ValueError("invalid_renderer_template_projection: columns must be an integer from 1 to 8")
    column_gap_mm = page.get("column_gap_mm", 4)
    column_gap = _hwp_units_from_mm(column_gap_mm, field="page.column_gap_mm")
    if columns > 1 and columns * column_gap > width - margin_units["left"] - margin_units["right"]:
        raise ValueError("invalid_renderer_template_projection: column gap exceeds text width")
    return width, height, margin_units, columns, column_gap


def _set_nested_margin_spacing(header_root: ET.Element, *, gap_hwp: int, workspace_hwp: int) -> None:
    """Apply item/workspace spacing to the base paragraph properties.

    The builder uses paragraph property 0 for ordinary renderer paragraphs.
    Updating that shared definition makes the projection effective for every
    ordered text block without rewriting each generated paragraph.
    """

    for para in _xml_find_all(header_root, "paraPr"):
        if para.get("id") != "0":
            continue
        switches = _xml_find_all(para, "switch")
        for switch in switches:
            margins = _xml_find_all(switch, "margin")
            for margin in margins:
                next_node = next((node for node in margin if _xml_local_name(node.tag) == "next"), None)
                prev_node = next((node for node in margin if _xml_local_name(node.tag) == "prev"), None)
                if next_node is not None:
                    next_node.set("value", str(max(gap_hwp, workspace_hwp)))
                if prev_node is not None and workspace_hwp:
                    prev_node.set("value", str(workspace_hwp))


def _qr_projection_details(projection: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate and normalize the QR patch consumed by the HWPX writer.

    ``apply_qr_insertion`` verifies the complete handoff, including the QR
    payload and its binding.  The structural writer still checks the copied
    projection at the last byte-writing boundary so a caller cannot silently
    produce a document with a missing, changed, or differently sized image.
    """

    header = projection.get("header", {})
    if not isinstance(header, Mapping):
        raise ValueError("invalid_renderer_template_projection: header must be a mapping")
    raw = header.get("qr")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("invalid_renderer_template_projection: header.qr must be a mapping")
    if raw.get("enabled", True) is False:
        return None
    if raw.get("format", "png") != "png":
        raise ValueError("invalid_renderer_template_projection: header.qr must be a PNG")
    if raw.get("surface", "header") != "header":
        raise ValueError("invalid_renderer_template_projection: header.qr surface must be header")
    if raw.get("anchor", "top-right") != "top-right":
        raise ValueError("invalid_renderer_template_projection: header.qr anchor must be top-right")
    if raw.get("page_scope", "all") != "all":
        raise ValueError("invalid_renderer_template_projection: header.qr page_scope must be all")
    image_value = raw.get("image_path") or raw.get("path")
    if not isinstance(image_value, (str, os.PathLike)):
        raise ValueError("invalid_renderer_template_projection: header.qr image_path is required")
    image_path = Path(image_value).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    width, height = validate_image_dimensions(
        raw.get("width_hwpunit"),
        raw.get("height_hwpunit"),
    )
    expected_sha = raw.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64 or any(
        char.lower() not in "0123456789abcdef" for char in expected_sha
    ):
        raise ValueError("invalid_renderer_template_projection: header.qr sha256 is invalid")
    actual_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if actual_sha != expected_sha.lower():
        raise ValueError("invalid_renderer_template_projection: header.qr image hash mismatch")
    url = raw.get("url") or raw.get("qr_url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("invalid_renderer_template_projection: header.qr url is required")
    label = raw.get("caption", raw.get("label", ""))
    if label is None:
        label = ""
    if not isinstance(label, str):
        raise ValueError("invalid_renderer_template_projection: header.qr caption must be text")
    para_pr_id = raw.get("para_pr_id", 20)
    if isinstance(para_pr_id, bool) or not isinstance(para_pr_id, int) or para_pr_id < 0:
        raise ValueError("invalid_renderer_template_projection: header.qr para_pr_id is invalid")
    return {
        "image_path": image_path,
        "image_bytes": image_path.read_bytes(),
        "sha256": actual_sha,
        "url": url.strip(),
        "label": label,
        "width_hwpunit": width,
        "height_hwpunit": height,
        "para_pr_id": para_pr_id,
        "out_margin": 283,
    }


def _next_hwpx_image_id(section_root: ET.Element, members: Sequence[tuple[zipfile.ZipInfo, bytes]]) -> str:
    """Return the next image manifest ID without colliding with body images."""

    ids: set[str] = set()
    for image in _xml_find_all(section_root, "img"):
        value = image.get("binaryItemIDRef")
        if value:
            ids.add(value)
    for info, _data in members:
        if info.filename.startswith("BinData/"):
            ids.add(Path(info.filename).stem)
    maximum = max(
        (int(match.group(1)) for value in ids if (match := re.fullmatch(r"image(\d+)", value))),
        default=0,
    )
    candidate = f"image{maximum + 1}"
    while candidate in ids:
        maximum += 1
        candidate = f"image{maximum + 1}"
    return candidate


def _next_hwpx_control_id(section_root: ET.Element) -> int:
    """Return a stable free numeric control ID for an adapter-owned picture."""

    values: list[int] = []
    for node in section_root.iter():
        for value in (node.get("id"), node.get("instid")):
            try:
                if value is not None:
                    values.append(int(value))
            except (TypeError, ValueError):
                continue
    return max(values, default=5_000_000) + 1


def _build_hwpx_qr_picture(qr: Mapping[str, Any], *, image_id: str, picture_id: int) -> ET.Element:
    """Build the HWPX picture object used by both header story mirrors."""

    # The image dimensions are deliberately proportional; HWP recalculates
    # the visible size from hp:sz while preserving the QR's square modules.
    original = 100_000
    pic = ET.Element(
        f"{{{_HWP_XML_NS['hp']}}}pic",
        {
            "id": str(picture_id),
            "zOrder": "0",
            "numberingType": "PICTURE",
            "textWrap": "TOP_AND_BOTTOM",
            "textFlow": "BOTH_SIDES",
            "lock": "0",
            "dropcapstyle": "None",
            "href": "",
            "groupLevel": "0",
            "instid": str(picture_id),
            "reverse": "0",
        },
    )
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}offset", {"x": "0", "y": "0"})
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}orgSz", {"width": str(original), "height": str(original)})
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}curSz", {"width": "0", "height": "0"})
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}flip", {"horizontal": "0", "vertical": "0"})
    ET.SubElement(
        pic,
        f"{{{_HWP_XML_NS['hp']}}}rotationInfo",
        {"angle": "0", "centerX": str(original // 2), "centerY": str(original // 2), "rotateimage": "1"},
    )
    rendering = ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}renderingInfo")
    for name in ("transMatrix", "scaMatrix", "rotMatrix"):
        ET.SubElement(
            rendering,
            f"{{{_HWP_XML_NS['hc']}}}{name}",
            {"e1": "1", "e2": "0", "e3": "0", "e4": "0", "e5": "1", "e6": "0"},
        )
    ET.SubElement(
        pic,
        f"{{{_HWP_XML_NS['hc']}}}img",
        {"binaryItemIDRef": image_id, "bright": "0", "contrast": "0", "effect": "REAL_PIC", "alpha": "0"},
    )
    rect = ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}imgRect")
    for name, x, y in (("pt0", 0, 0), ("pt1", original, 0), ("pt2", original, original), ("pt3", 0, original)):
        ET.SubElement(rect, f"{{{_HWP_XML_NS['hc']}}}{name}", {"x": str(x), "y": str(y)})
    ET.SubElement(
        pic,
        f"{{{_HWP_XML_NS['hp']}}}imgClip",
        {"left": "0", "right": str(original), "top": "0", "bottom": str(original)},
    )
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}inMargin", {"left": "0", "right": "0", "top": "0", "bottom": "0"})
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}imgDim", {"dimwidth": str(original), "dimheight": str(original)})
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}effects")
    ET.SubElement(
        pic,
        f"{{{_HWP_XML_NS['hp']}}}sz",
        {
            "width": str(qr["width_hwpunit"]),
            "widthRelTo": "ABSOLUTE",
            "height": str(qr["height_hwpunit"]),
            "heightRelTo": "ABSOLUTE",
            "protect": "0",
        },
    )
    # A floating object aligned to the column's right edge stays in the
    # header's top-right quiet zone while the title remains at the left.
    ET.SubElement(
        pic,
        f"{{{_HWP_XML_NS['hp']}}}pos",
        {
            "treatAsChar": "0",
            "affectLSpacing": "0",
            "flowWithText": "1",
            "allowOverlap": "0",
            "holdAnchorAndSO": "0",
            "vertRelTo": "PARA",
            "horzRelTo": "COLUMN",
            "vertAlign": "TOP",
            "horzAlign": "RIGHT",
            "vertOffset": "0",
            "horzOffset": "0",
        },
    )
    margin = str(qr["out_margin"])
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}outMargin", {"left": margin, "right": margin, "top": margin, "bottom": margin})
    label = qr["label"] or "정답·해설"
    ET.SubElement(pic, f"{{{_HWP_XML_NS['hp']}}}shapeComment").text = f"QR|{label}|{qr['url']}"
    return pic


def _apply_hwpx_template_projection(hwpx_path: Path, projection: Mapping[str, Any]) -> None:
    """Apply a renderer template to generated HWPX XML without Office.

    The preserved vendor builder intentionally owns only the default A4
    skeleton.  This adapter-owned post-save pass is the supported structural
    template boundary: it changes section page geometry/columns, definition
    fonts and spacing, equation font names, and deterministic header/footer
    paragraphs while preserving every ZIP member and generated body element.
    """

    width, height, margins, columns, column_gap = _projection_page_values(projection)
    typography = projection.get("typography", {})
    if not isinstance(typography, Mapping):
        raise ValueError("invalid_renderer_template_projection: typography must be a mapping")
    fonts = projection.get("fonts", {})
    if not isinstance(fonts, Mapping):
        raise ValueError("invalid_renderer_template_projection: fonts must be a mapping")
    body_typography = typography.get("body", {})
    math_typography = typography.get("math", {})
    body_font = (body_typography.get("font_family") if isinstance(body_typography, Mapping) else None) or fonts.get("body")
    math_font = (math_typography.get("font_family") if isinstance(math_typography, Mapping) else None) or fonts.get("math")
    if body_font is not None and (not isinstance(body_font, str) or not body_font.strip()):
        raise ValueError("invalid_renderer_template_projection: body font must be a non-empty string")
    if math_font is not None and (not isinstance(math_font, str) or not math_font.strip()):
        raise ValueError("invalid_renderer_template_projection: math font must be a non-empty string")
    body_size = body_typography.get("size_pt") if isinstance(body_typography, Mapping) else None
    math_size = math_typography.get("size_pt") if isinstance(math_typography, Mapping) else None
    spacing = projection.get("spacing", {})
    if not isinstance(spacing, Mapping):
        raise ValueError("invalid_renderer_template_projection: spacing must be a mapping")
    gap_pt = spacing.get("item_gap_pt", projection.get("question_spacing_pt", 0)) or 0
    workspace_mm = spacing.get("work_space_mm", projection.get("answer_space_mm", 0)) or 0
    gap_hwp = _hwp_units_from_pt(gap_pt, field="spacing.item_gap_pt")
    workspace_hwp = _hwp_units_from_mm(workspace_mm, field="spacing.work_space_mm")
    qr = _qr_projection_details(projection)

    section_name = "Contents/section0.xml"
    header_name = "Contents/header.xml"
    try:
        with zipfile.ZipFile(hwpx_path, "r") as source:
            members = [(info, source.read(info.filename)) for info in source.infolist()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise HwpAdapterError(f"hwp_template_apply_failed: invalid generated HWPX: {exc}") from exc

    section_bytes = next((data for info, data in members if info.filename == section_name), None)
    header_bytes = next((data for info, data in members if info.filename == header_name), None)
    if section_bytes is None or header_bytes is None:
        raise HwpAdapterError("hwp_template_apply_failed: generated HWPX lacks section/header XML")
    try:
        section_root = ET.fromstring(section_bytes)
        header_root = ET.fromstring(header_bytes)
    except ET.ParseError as exc:
        raise HwpAdapterError("hwp_template_apply_failed: generated XML is malformed") from exc

    # Keep the QR member ID distinct from body images and reuse an adapter QR
    # ID when this projection is applied a second time.  Reuse makes the
    # structural operation idempotent and avoids accumulating orphan PNGs.
    previous_qr_ids: set[str] = set()
    for picture in _xml_find_all(section_root, "pic"):
        comment = _xml_find(picture, "shapeComment")
        if comment is None or not (comment.text or "").startswith("QR|"):
            continue
        image = _xml_find(picture, "img")
        if image is not None and image.get("binaryItemIDRef"):
            previous_qr_ids.add(str(image.get("binaryItemIDRef")))
    if qr is not None:
        image_id = sorted(previous_qr_ids)[0] if previous_qr_ids else _next_hwpx_image_id(section_root, members)
        picture_id = _next_hwpx_control_id(section_root)
        qr_picture = _build_hwpx_qr_picture(qr, image_id=image_id, picture_id=picture_id)
    else:
        image_id = None
        qr_picture = None

    secpr = _xml_find(section_root, "secPr")
    if secpr is None:
        raise HwpAdapterError("hwp_template_apply_failed: section properties are missing")

    # The preserved builder emits content tables as inline objects.  Native
    # HWP treats an inline multi-row table as one indivisible character, so
    # rows past the first page can disappear even when the table advertises
    # ``pageBreak=\"CELL\"``.  Keep compact one-row layout tables inline, but
    # let real content tables paginate by cell without changing their font
    # size or row geometry.
    for table in _xml_find_all(section_root, "tbl"):
        try:
            row_count = int(table.get("rowCnt", "0"))
        except (TypeError, ValueError):
            continue
        if row_count <= 1 or table.get("pageBreak", "CELL").upper() not in {"CELL", "TABLE"}:
            continue
        position = _xml_find(table, "pos")
        if position is not None:
            position.set("treatAsChar", "0")

    page_pr = _xml_find(secpr, "pagePr")
    if page_pr is None:
        page_pr = ET.SubElement(secpr, f"{{{_HWP_XML_NS['hp']}}}pagePr")
    # OWPML uses the historical ``landscape`` enum as a paper orientation:
    # ``WIDELY`` is the portrait (tall-page) value and ``NARROWLY`` is the
    # landscape (wide-page) value.  The dimensions above already carry the
    # actual orientation, so derive the flag from their final ordering.
    page_pr.set("landscape", "NARROWLY" if width > height else "WIDELY")
    page_pr.set("width", str(width))
    page_pr.set("height", str(height))
    margin = _xml_find(page_pr, "margin")
    if margin is None:
        margin = ET.SubElement(page_pr, f"{{{_HWP_XML_NS['hp']}}}margin")
    header_reserve = margin.get("header", "4252")
    footer_reserve = margin.get("footer", "4252")
    if qr is not None:
        try:
            existing_header_reserve = int(header_reserve)
        except (TypeError, ValueError):
            existing_header_reserve = 0
        # Reserve the square image and a caption/title line.  The one-millimeter
        # picture out-margin is part of the quiet zone and must fit inside the
        # header before body content begins.
        header_reserve = str(max(existing_header_reserve, qr["height_hwpunit"] + 2 * qr["out_margin"] + 1701))
        margin.set("header", header_reserve)
    margin.set("left", str(margins["left"]))
    margin.set("right", str(margins["right"]))
    margin.set("top", str(margins["top"]))
    margin.set("bottom", str(margins["bottom"]))
    # Preserve the default header/footer reserve unless a template explicitly
    # disables its corresponding field.  These values are part of page layout,
    # not content margins.
    header = projection.get("header", {})
    footer = projection.get("footer", {})
    if isinstance(header, Mapping) and header.get("enabled") is False:
        margin.set("header", "0")
    if isinstance(footer, Mapping) and footer.get("enabled") is False:
        margin.set("footer", "0")
    if "column_gap_mm" in (projection.get("page") or {}):
        secpr.set("spaceColumns", str(column_gap))
    colpr = _xml_find(section_root, "colPr")
    if colpr is None:
        ctrl = _xml_find(section_root, "ctrl")
        if ctrl is None:
            ctrl = ET.SubElement(section_root, f"{{{_HWP_XML_NS['hp']}}}ctrl")
        colpr = ET.SubElement(ctrl, f"{{{_HWP_XML_NS['hp']}}}colPr")
    colpr.set("colCount", str(columns))
    colpr.set("sameSz", "1")
    colpr.set("sameGap", "1" if columns > 1 else "0")

    # Apply body typography to the default character property and use the
    # equation object's explicit font/baseUnit for math.  This leaves special
    # title/heading styles intact while making ordinary renderer content obey
    # the selected template.
    if body_font or body_size is not None:
        for char_pr in _xml_find_all(header_root, "charPr"):
            if char_pr.get("id") == "0":
                if body_size is not None:
                    char_pr.set("height", str(_hwp_units_from_pt(body_size, field="typography.body.size_pt")))
                font_refs = _xml_find_all(char_pr, "fontRef")
                if body_font:
                    for font_ref in font_refs:
                        for attr in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"):
                            font_ref.set(attr, "0")
    if body_font:
        for font in _xml_find_all(header_root, "font"):
            font.set("face", body_font)
    for equation in _xml_find_all(section_root, "equation"):
        if math_font:
            equation.set("font", math_font)
        if math_size is not None:
            equation.set("baseUnit", str(_hwp_units_from_pt(math_size, field="typography.math.size_pt")))
    _set_nested_margin_spacing(header_root, gap_hwp=gap_hwp, workspace_hwp=workspace_hwp)

    # HWPX carries two synchronized representations of a header/footer:
    # the logical story and its first-run control mirror.  The logical story
    # owns the page-type application binding; the mirror is what older Hancom
    # readers inspect while laying out the section.  Keep both in lockstep so
    # the story applies to every page instead of only the odd-page default.
    first_body = next((node for node in section_root if _xml_local_name(node.tag) == "p"), None)
    first_run = None
    if first_body is not None:
        first_run = next(
            (
                node
                for node in first_body
                if _xml_local_name(node.tag) == "run"
                and any(_xml_local_name(child.tag) == "secPr" for child in node)
            ),
            None,
        )

    # A generated HWPX always has a secPr carrier run, but keep this pass
    # tolerant of a minimal fixture by creating the carrier if it is absent.
    if first_body is None:
        first_body = ET.Element(f"{{{_HWP_XML_NS['hp']}}}p", {"id": "0", "paraPrIDRef": "0", "styleIDRef": "0", "pageBreak": "0", "columnBreak": "0", "merged": "0"})
        section_root.insert(0, first_body)
    if first_run is None:
        first_run = ET.Element(f"{{{_HWP_XML_NS['hp']}}}run", {"charPrIDRef": "0"})
        first_body.insert(0, first_run)
        first_run.append(secpr)

    # Remove the adapter-owned stories from prior applications while retaining
    # unrelated controls such as colPr.  This makes the operation idempotent
    # and prevents stale odd/even controls from competing with the new story.
    for run in _xml_find_all(section_root, "run"):
        for control in list(run):
            if _xml_local_name(control.tag) != "ctrl":
                continue
            removed_story = False
            for child in list(control):
                if _xml_local_name(child.tag) in {"header", "footer"}:
                    control.remove(child)
                    removed_story = True
            if removed_story and not list(control):
                run.remove(control)
    for child in list(secpr):
        if _xml_local_name(child.tag) in {"header", "footer", "headerApply", "footerApply"}:
            secpr.remove(child)

    def _story_enabled(spec: Any) -> bool:
        return (
            isinstance(spec, Mapping)
            and spec.get("enabled", True) is not False
            and isinstance(spec.get("text"), str)
            and bool(spec["text"])
        )

    def _append_text_run(parent: ET.Element, value: str) -> None:
        if not value:
            return
        run = ET.SubElement(parent, f"{{{_HWP_XML_NS['hp']}}}run", {"charPrIDRef": "0"})
        text_node = ET.SubElement(run, f"{{{_HWP_XML_NS['hp']}}}t")
        text_node.text = value

    def _append_auto_number_run(parent: ET.Element, *, number_type: str, number: str) -> None:
        run = ET.SubElement(parent, f"{{{_HWP_XML_NS['hp']}}}run", {"charPrIDRef": "0"})
        control = ET.SubElement(run, f"{{{_HWP_XML_NS['hp']}}}ctrl")
        auto_num = ET.SubElement(
            control,
            f"{{{_HWP_XML_NS['hp']}}}autoNum",
            {"num": number, "numType": number_type},
        )
        ET.SubElement(
            auto_num,
            f"{{{_HWP_XML_NS['hp']}}}autoNumFormat",
            {
                "type": "DIGIT",
                "userChar": "",
                "prefixChar": "",
                "suffixChar": "",
                "supscript": "0",
            },
        )

    def _append_story_text(paragraph: ET.Element, text: str) -> None:
        # ``{page}`` and ``{pages}`` are template tokens, not visible text.
        # Emit ordinary text as hp:t runs and page fields as hp:autoNum
        # controls so native HWP recalculates the values on every page.
        token_pattern = re.compile(r"\{pages?\}")
        cursor = 0
        for match in token_pattern.finditer(text):
            _append_text_run(paragraph, text[cursor : match.start()])
            if match.group(0) == "{page}":
                _append_auto_number_run(paragraph, number_type="PAGE", number="1")
            else:
                _append_auto_number_run(paragraph, number_type="TOTAL_PAGE", number="2")
            cursor = match.end()
        _append_text_run(paragraph, text[cursor:])

    def _header_footer(text: str, kind: str, para_pr_id: str, paragraph_id: str) -> ET.Element:
        element = ET.Element(
            f"{{{_HWP_XML_NS['hp']}}}{kind}",
            {"id": "0", "applyPageType": "BOTH"},
        )
        sub_list = ET.SubElement(
            element,
            f"{{{_HWP_XML_NS['hp']}}}subList",
            {
                "textDirection": "HORIZONTAL",
                "lineWrap": "BREAK",
                "vertAlign": "TOP" if kind == "header" else "BOTTOM",
                "linkListIDRef": "0",
                "linkListNextIDRef": "0",
                "textWidth": str(max(width - margins["left"] - margins["right"], 0)),
                "textHeight": header_reserve if kind == "header" else footer_reserve,
                "hasTextRef": "0",
                "hasNumRef": "0",
            },
        )
        paragraph = ET.SubElement(
            sub_list,
            f"{{{_HWP_XML_NS['hp']}}}p",
            {
                "id": paragraph_id,
                "paraPrIDRef": para_pr_id,
                "styleIDRef": "0",
                "pageBreak": "0",
                "columnBreak": "0",
                "merged": "0",
            },
        )
        _append_story_text(paragraph, text)
        if kind == "header" and qr_picture is not None:
            # This is a floating picture anchored to the title paragraph.  Its
            # right-column/top-paragraph position keeps the header text clear
            # while allowing the same story to repeat on every page.
            qr_run = ET.SubElement(paragraph, f"{{{_HWP_XML_NS['hp']}}}run", {"charPrIDRef": "0"})
            qr_run.append(deepcopy(qr_picture))
            caption = qr.get("label", "")
            if caption:
                caption_para = ET.SubElement(
                    sub_list,
                    f"{{{_HWP_XML_NS['hp']}}}p",
                    {
                        "id": str(int(paragraph_id) + 1),
                        "paraPrIDRef": "21",
                        "styleIDRef": "0",
                        "pageBreak": "0",
                        "columnBreak": "0",
                        "merged": "0",
                    },
                )
                _append_text_run(caption_para, caption)
        return element

    logical_stories: list[tuple[str, ET.Element]] = []
    if _story_enabled(header) or qr_picture is not None:
        header_text = header.get("text", "") if isinstance(header, Mapping) else ""
        logical_stories.append(("header", _header_footer(header_text, "header", "9", "900000001")))
    if _story_enabled(footer):
        logical_stories.append(("footer", _header_footer(footer["text"], "footer", "0", "900000002")))

    for kind, story in logical_stories:
        secpr.append(story)
        apply = ET.SubElement(
            secpr,
            f"{{{_HWP_XML_NS['hp']}}}{kind}Apply",
            {"applyPageType": "BOTH", "idRef": story.attrib["id"]},
        )
        # Select the carrier run's direct control.  A descendant search would
        # find the ctrl wrapping an autoNum field inside the logical footer
        # after that story has been appended, nesting the mirror in the field.
        control = next(
            (child for child in first_run if _xml_local_name(child.tag) == "ctrl"),
            None,
        )
        if control is None:
            control = ET.SubElement(first_run, f"{{{_HWP_XML_NS['hp']}}}ctrl")
        control.append(deepcopy(story))

    # Register the QR PNG in the HWPX package manifest.  The picture's
    # binaryItemIDRef, manifest item, and BinData member must agree or native
    # HWP silently drops the image while still opening the document.
    content_name = "Contents/content.hpf"
    content_bytes = next((data for info, data in members if info.filename == content_name), None)
    obsolete_qr_ids = previous_qr_ids - ({image_id} if image_id is not None else set())
    rendered_content: bytes | None = None
    if qr is not None or obsolete_qr_ids:
        opf_namespace = "http://www.idpf.org/2007/opf/"
        if content_bytes is not None:
            try:
                content_root = ET.fromstring(content_bytes)
            except ET.ParseError as exc:
                raise HwpAdapterError("hwp_template_apply_failed: content manifest is malformed") from exc
        else:
            content_root = ET.Element(
                f"{{{opf_namespace}}}package",
                {"version": "2.0", "unique-identifier": "BookId"},
            )
            ET.SubElement(content_root, f"{{{opf_namespace}}}metadata")
        manifest = next((node for node in content_root.iter() if _xml_local_name(node.tag) == "manifest"), None)
        if manifest is None:
            manifest = ET.SubElement(content_root, f"{{{opf_namespace}}}manifest")
        for item in list(manifest):
            item_id = item.get("id")
            href = item.get("href", "")
            if item_id in obsolete_qr_ids or (image_id is not None and item_id == image_id):
                manifest.remove(item)
            elif any(href == f"BinData/{old_id}.png" for old_id in obsolete_qr_ids):
                manifest.remove(item)
        if image_id is not None:
            manifest.append(
                ET.Element(
                    f"{{{opf_namespace}}}item",
                    {
                        "id": image_id,
                        "href": f"BinData/{image_id}.png",
                        "media-type": "image/png",
                        "isEmbeded": "1",
                    },
                )
            )
        ET.register_namespace("opf", opf_namespace)
        rendered_content = ET.tostring(content_root, encoding="utf-8", xml_declaration=True)

    ET.register_namespace("hp", _HWP_XML_NS["hp"])
    ET.register_namespace("hh", _HWP_XML_NS["hh"])
    ET.register_namespace("hs", _HWP_XML_NS["hs"])
    ET.register_namespace("hc", _HWP_XML_NS["hc"])
    rendered_section = ET.tostring(section_root, encoding="utf-8", xml_declaration=True)
    rendered_header = ET.tostring(header_root, encoding="utf-8", xml_declaration=True)
    temp_path = hwpx_path.with_name(f".{hwpx_path.name}.{uuid.uuid4().hex}.tmp")
    replaced_members = {f"BinData/{old_id}.png" for old_id in obsolete_qr_ids}
    if image_id is not None:
        replaced_members.add(f"BinData/{image_id}.png")
    try:
        with zipfile.ZipFile(temp_path, "w") as target:
            for info, data in members:
                if info.filename in replaced_members:
                    continue
                if info.filename == section_name:
                    data = rendered_section
                elif info.filename == header_name:
                    data = rendered_header
                elif info.filename == content_name and rendered_content is not None:
                    data = rendered_content
                target.writestr(info, data)
            if rendered_content is not None and not any(info.filename == content_name for info, _data in members):
                target.writestr(content_name, rendered_content, compress_type=zipfile.ZIP_DEFLATED)
            if qr is not None and image_id is not None:
                target.writestr(f"BinData/{image_id}.png", qr["image_bytes"], compress_type=zipfile.ZIP_DEFLATED)
        os.replace(temp_path, hwpx_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


class _StructuralTemplateToolkit:
    """Adapter-owned wrapper that makes structural template settings effective."""

    mode = "structural"
    # The preserved structural toolkit exposes convenience methods that enter
    # its warm COM/PDF daemon.  Structural generation is deliberately local;
    # advertise those capabilities as unavailable so render_document cannot
    # accidentally cross that unsafe boundary through __getattr__.
    can_export_pdf = False
    can_export_native_images = False

    def __init__(self, vendor_toolkit: Any) -> None:
        self._vendor = vendor_toolkit
        self._projection: dict[str, Any] | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._vendor, name)

    def apply_template_projection(self, projection: Mapping[str, Any]) -> bool:
        if not isinstance(projection, Mapping):
            raise ValueError("invalid_renderer_template_projection")
        # Validate all page/spacing values before any document bytes are saved.
        _projection_page_values(projection)
        self._projection = dict(projection)
        return True

    def add_workspace(self, millimeters: float) -> Any:
        """Add a deterministic writing area after an exam item."""

        if millimeters <= 0:
            return None
        lines = max(1, int(round(float(millimeters) / 4.0)))
        writer = getattr(self._vendor, "write_text", None)
        if not callable(writer):
            raise HwpAdapterError("selected toolkit does not support workspace blocks")
        return writer("\n" * lines)

    def save(self, filepath: Path | str) -> Path:
        saver = getattr(self._vendor, "save", None)
        if not callable(saver):
            raise HwpAdapterError("selected toolkit does not support saving")
        saved = saver(filepath)
        target = Path(saved if isinstance(saved, (str, os.PathLike)) else filepath).resolve()
        if self._projection is not None:
            _apply_hwpx_template_projection(target, self._projection)
        return target


def _make_modifier_toolkit(source: Path) -> Any:
    toolkit_type = _load_vendor_toolkit()
    converter_type = _load_vendor_converter()
    bridge = _VENDOR_ROOT / "bridge" / "latex_to_hwpeqn.js"
    converter = converter_type(bridge_script=bridge)
    return toolkit_type(mode="modifier", converter=converter, hwpx_path=source)


class _DispatchExSession:
    """Own a direct ``DispatchEx`` object and release this thread's COM init."""

    def __init__(self, dispatch: Any, pythoncom: Any) -> None:
        self._dispatch = dispatch
        self._pythoncom = pythoncom
        self._com_released = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._dispatch, name)

    @staticmethod
    def _successful(result: Any, operation: str) -> Any:
        if result is False:
            raise HwpAdapterError(f"hwp_live_operation_failed: {operation}")
        return result

    def _raw_callable(self, *names: str) -> Callable[..., Any] | None:
        for name in names:
            candidate = getattr(self._dispatch, name, None)
            if callable(candidate):
                return candidate
        return None

    def _run_action(self, action: str) -> Any:
        runner = self._raw_callable("run", "Run")
        if runner is None:
            raise HwpAdapterError(
                f"hwp_live_session_unavailable: {action} action is unavailable"
            )
        return self._successful(runner(action), action)

    def break_paragraph(self) -> Any:
        """Start a new paragraph through the raw HWP action bridge."""

        breaker = self._raw_callable("break_para", "BreakPara")
        if breaker is not None:
            return self._successful(breaker(), "break_para")
        return self._run_action("BreakPara")

    def open(self, path: str) -> Any:
        opener = self._raw_callable("open", "Open")
        if opener is None:
            raise HwpAdapterError("hwp_live_session_unavailable: session cannot open a document")
        return self._successful(opener(path), "open")

    def save_as(self, path: str, *args: Any, **kwargs: Any) -> Any:
        saver = self._raw_callable("save_as", "SaveAs")
        if saver is None:
            raise HwpAdapterError("hwp_live_session_unavailable: session cannot save a document")
        try:
            result = saver(path, *args, **kwargs)
        except TypeError:
            # A few COM wrappers expose only the filename argument.  Retrying
            # without an optional format is safe because the target suffix is
            # already controlled by the adapter.
            result = saver(path)
        return self._successful(result, "save_as")

    def insert_text(self, text: str) -> Any:
        inserter = self._raw_callable("insert_text", "InsertText")
        if inserter is not None:
            return self._successful(inserter(text), "insert_text")

        parameter_set = getattr(getattr(self._dispatch, "HParameterSet", None), "HInsertText", None)
        action = getattr(self._dispatch, "HAction", None)
        get_default = getattr(action, "GetDefault", None)
        execute = getattr(action, "Execute", None)
        hset = getattr(parameter_set, "HSet", None)
        if not all(callable(value) for value in (get_default, execute)) or hset is None:
            raise HwpAdapterError(
                "hwp_live_session_unavailable: InsertText helper is unavailable"
            )
        get_default("InsertText", hset)
        parameter_set.Text = text
        return self._successful(execute("InsertText", hset), "insert_text")

    def _create_table(self, rows: int, cols: int) -> Any:
        creator = self._raw_callable("create_table", "CreateTable")
        if creator is not None:
            return self._successful(creator(rows, cols), "create_table")

        parameter_set = getattr(getattr(self._dispatch, "HParameterSet", None), "HTableCreation", None)
        action = getattr(self._dispatch, "HAction", None)
        get_default = getattr(action, "GetDefault", None)
        execute = getattr(action, "Execute", None)
        hset = getattr(parameter_set, "HSet", None)
        if not all(callable(value) for value in (get_default, execute)) or hset is None:
            raise HwpAdapterError(
                "hwp_live_session_unavailable: TableCreate helper is unavailable"
            )
        get_default("TableCreate", hset)
        parameter_set.Rows = rows
        parameter_set.Cols = cols
        return self._successful(execute("TableCreate", hset), "create_table")

    def _move_to_next_table_cell(self) -> Any:
        mover = self._raw_callable("table_right_cell", "TableRightCell")
        if mover is not None:
            return self._successful(mover(), "table_right_cell")
        return self._run_action("TableRightCell")

    def _leave_table(self) -> Any:
        """Return the caret to the document list after filling a table."""

        # ``Close`` is the HWP action for leaving the current sub-list.  Calling
        # a COM ``Close`` method here would close the document, so route only
        # through Run and keep this boundary explicit.
        return self._run_action("Close")

    def insert_table(
        self,
        rows: int,
        cols: int,
        data: Sequence[Sequence[str]],
        **options: Any,
    ) -> Any:
        inserter = self._raw_callable("insert_table", "InsertTable")
        if inserter is not None:
            try:
                return self._successful(inserter(rows, cols, data, **options), "insert_table")
            except TypeError:
                try:
                    return self._successful(inserter(rows, cols, data), "insert_table")
                except TypeError:
                    # Fall through to the documented create-and-fill path if
                    # the raw helper accepts dimensions only.
                    pass

        self._create_table(rows, cols)
        flat = [str(cell) for row in data for cell in row]
        for index, cell in enumerate(flat):
            self.insert_text(cell)
            if index + 1 < len(flat):
                self._move_to_next_table_cell()
        self._leave_table()
        return True

    def insert_picture(
        self,
        path: str,
        *,
        embedded: bool = True,
        width_hwpunit: int = 0,
        height_hwpunit: int = 0,
        caption: str = "",
        para_pr_id: int = 20,
    ) -> Any:
        inserter = self._raw_callable("insert_picture", "InsertPicture")
        if inserter is None:
            raise HwpAdapterError(
                "hwp_live_session_unavailable: InsertPicture helper is unavailable"
            )
        if bool(width_hwpunit) != bool(height_hwpunit):
            raise ValueError("invalid_image_dimensions: width and height must be provided together")
        has_dimensions = bool(width_hwpunit and height_hwpunit)
        kwargs: dict[str, Any] = {
            "Path": str(path),
            "Embedded": bool(embedded),
            "sizeoption": 1 if has_dimensions else 0,
            "Reverse": False,
            "watermark": False,
            "Effect": 0,
            "Width": _hwpunit_to_millimeters(width_hwpunit) if has_dimensions else 0,
            "Height": _hwpunit_to_millimeters(height_hwpunit) if has_dimensions else 0,
        }
        # para_pr_id is an adapter paragraph style identifier; the raw COM
        # picture action has no equivalent parameter and is intentionally not
        # sent as an unknown COM keyword.
        del para_pr_id

        def finish(result: Any) -> Any:
            result = self._successful(result, "insert_picture")
            # Raw InsertPicture has no caption field.  Keep the picture in its
            # own paragraph, then insert the caption in a following paragraph;
            # a failure is surfaced instead of reporting a completed document
            # with silently missing text.
            self.break_paragraph()
            if caption:
                self.insert_text(caption)
                self.break_paragraph()
            return result
        try:
            result = inserter(**kwargs)
        except TypeError as first_error:
            # A Python-shaped wrapper may spell the same raw parameters in
            # lower case and take the path positionally.  Retry with the full
            # option set; silently dropping dimensions would change the
            # requested picture geometry by orders of magnitude.
            try:
                result = inserter(
                    str(path),
                    embedded=bool(embedded),
                    sizeoption=1 if has_dimensions else 0,
                    reverse=False,
                    watermark=False,
                    effect=0,
                    width=_hwpunit_to_millimeters(width_hwpunit) if has_dimensions else 0,
                    height=_hwpunit_to_millimeters(height_hwpunit) if has_dimensions else 0,
                )
            except TypeError:
                try:
                    result = inserter(
                        str(path),
                        bool(embedded),
                        1 if has_dimensions else 0,
                        False,
                        False,
                        0,
                        _hwpunit_to_millimeters(width_hwpunit) if has_dimensions else 0,
                        _hwpunit_to_millimeters(height_hwpunit) if has_dimensions else 0,
                    )
                except TypeError as final_error:
                    raise HwpAdapterError(
                        "hwp_live_session_unavailable: InsertPicture signature is incompatible"
                    ) from final_error
        return finish(result)

    def create_page_image(self, path: str, **kwargs: Any) -> Any:
        renderer = self._raw_callable("create_page_image", "CreatePageImage")
        if renderer is None:
            raise HwpAdapterError(
                "hwp_live_session_unavailable: CreatePageImage helper is unavailable"
            )
        options = dict(kwargs)
        pgno = options.pop("pgno", options.pop("Pgno", -1))
        resolution = options.pop("resolution", options.pop("Resolution", 300))
        depth = options.pop("depth", options.pop("Depth", 24))
        image_format = options.pop("format", options.pop("Format", "bmp"))
        if options:
            names = ", ".join(sorted(options))
            raise TypeError(f"unsupported CreatePageImage options: {names}")
        if isinstance(pgno, bool) or not isinstance(pgno, int):
            raise ValueError("invalid_page_image_page: pgno must be an integer")
        if isinstance(resolution, bool) or not isinstance(resolution, int) or resolution <= 0:
            raise ValueError("invalid_page_image_resolution")
        if isinstance(depth, bool) or not isinstance(depth, int) or depth <= 0:
            raise ValueError("invalid_page_image_depth")
        if not isinstance(image_format, str) or not image_format.strip():
            raise ValueError("invalid_page_image_format")
        image_format = image_format.lower()
        if image_format not in {"bmp", "gif"}:
            raise ValueError("invalid_page_image_format: raw COM supports bmp or gif")

        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        if pgno == -1:
            page_count = getattr(self._dispatch, "PageCount", None)
            if callable(page_count):
                page_count = page_count()
            if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
                raise HwpAdapterError("hwp_live_session_unavailable: page count is unavailable")
            extension = target.suffix or f".{image_format}"
            for index in range(1, page_count + 1):
                numbered = target.with_name(f"{target.stem}{index:03d}{extension}")
                self.create_page_image(
                    str(numbered), pgno=index, resolution=resolution, depth=depth,
                    format=image_format,
                )
            return True
        if pgno == 0:
            current_page = getattr(self._dispatch, "current_page", None)
            if current_page is None:
                current_page = getattr(self._dispatch, "CurrentPage", None)
            if callable(current_page):
                current_page = current_page()
            if isinstance(current_page, bool) or not isinstance(current_page, int) or current_page < 1:
                raise HwpAdapterError("hwp_live_session_unavailable: current page is unavailable")
            pgno = current_page
        page_count = getattr(self._dispatch, "PageCount", None)
        if callable(page_count):
            page_count = page_count()
        if isinstance(page_count, int) and not isinstance(page_count, bool):
            if not 1 <= pgno <= page_count:
                raise IndexError(f"pgno must be between 1 and {page_count}")
        raw_pgno = pgno - 1
        temporary = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.bmp")
        try:
            try:
                result = renderer(
                    Path=str(temporary), pgno=raw_pgno, resolution=resolution,
                    depth=depth, Format=image_format,
                )
            except TypeError as first_error:
                # Python-shaped doubles/wrappers often use a positional path
                # and a lower-case format.  Preserve every page/render option
                # on this signature-compatible retry.
                try:
                    result = renderer(
                        str(temporary), pgno=raw_pgno, resolution=resolution,
                        depth=depth, format=image_format,
                    )
                except TypeError as final_error:
                    raise HwpAdapterError(
                        "hwp_live_session_unavailable: CreatePageImage signature is incompatible"
                    ) from final_error
            result = self._successful(result, "create_page_image")
            if not temporary.is_file():
                raise HwpAdapterError("hwp_live_operation_failed: CreatePageImage produced no image")
            if target.suffix.lower() == ".bmp" and image_format == "bmp":
                temporary.replace(target)
            else:
                try:
                    from PIL import Image
                except Exception as exc:
                    raise HwpAdapterError(
                        "hwp_runtime_unavailable: Pillow is required to convert native page images"
                    ) from exc
                with Image.open(temporary) as image:
                    image.save(target)
            if not target.is_file():
                raise HwpAdapterError("hwp_live_operation_failed: converted page image is missing")
            return result
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def set_visibility(self, visible: bool) -> None:
        windows = getattr(self._dispatch, "XHwpWindows", None)
        active = getattr(windows, "Active_XHwpWindow", None) if windows is not None else None
        if active is None:
            raise HwpAdapterError("hwp_runtime_unavailable: new session has no active HWP window")
        active.Visible = visible

    def _release_com(self) -> None:
        if self._com_released:
            return
        self._com_released = True
        try:
            self._pythoncom.CoUninitialize()
        except Exception:
            pass

    def quit(self, *args: Any, **kwargs: Any) -> Any:
        try:
            closer = getattr(self._dispatch, "Quit", None) or getattr(self._dispatch, "quit", None)
            if not callable(closer):
                return None
            try:
                return closer(*args, **kwargs)
            except TypeError:
                return closer()
        finally:
            self._release_com()


class _LiveToolkitCompatibility:
    """Expose adapter-safe document operations over a verified COM session.

    The preserved toolkit's live methods are intentionally thin convenience
    wrappers.  Some of them silently do nothing when a raw COM object lacks a
    pyhwpx helper, so direct ``DispatchEx`` sessions use this explicit bridge
    for text, table, picture, and save operations.  Equation insertion remains
    delegated to the preserved verified controller.
    """

    mode = "live"

    def __init__(self, session: _DispatchExSession, vendor_toolkit: Any) -> None:
        self._session = session
        self._vendor = vendor_toolkit
        raw = session._dispatch
        self.can_export_pdf = callable(getattr(raw, "save_as", None)) or callable(
            getattr(raw, "SaveAs", None)
        )
        self.can_export_native_images = (
            getattr(raw, "PageCount", None) is not None
            and (
                callable(getattr(raw, "create_page_image", None))
                or callable(getattr(raw, "CreatePageImage", None))
            )
        )

    def get_state(self) -> dict[str, Any]:
        return self._vendor.get_state()

    def write_text(self, text: str, **kwargs: Any) -> Any:
        # The preserved controller performs a post-action cursor inspection;
        # the session helper it discovers here prevents its raw ``Run``
        # fallback from silently dropping the text payload.
        result = self._vendor.write_text(text, **kwargs)
        # Raw InsertText appends characters but does not terminate the
        # paragraph.  The public adapter treats each write_text call as one
        # paragraph, matching structural mode and preventing title/body text
        # from being concatenated in a live document.
        self._session.break_paragraph()
        return result

    def write_math_paragraph(self, text: str, **kwargs: Any) -> Any:
        return self._vendor.write_math_paragraph(text, **kwargs)

    def insert_math(self, **kwargs: Any) -> Any:
        return self._vendor.insert_math(**kwargs)

    def insert_table(
        self,
        rows: int,
        cols: int,
        data: Sequence[Sequence[str]],
        **options: Any,
    ) -> Any:
        return self._session.insert_table(rows, cols, data, **options)

    def insert_picture(self, image_path: Path | str, **kwargs: Any) -> Any:
        return self._session.insert_picture(str(Path(image_path).resolve()), **kwargs)

    def save(self, filepath: Path | str | None = None) -> Path:
        if filepath is None:
            raise ValueError("filepath is required when saving in live mode")
        target = Path(filepath).resolve()
        self._session.save_as(str(target))
        return target


def _make_default_hwp_factory(*, visible: bool = False, new: bool = True) -> Any:
    """Create HWP through COM ``DispatchEx`` without a ROT/active-object attach."""

    if new is not True:
        raise TypeError("the adapter requires a new HWP session")
    # Restricted sandbox tokens can leave a DCOM-launched Hwp.exe behind while
    # activation hangs, before HWND ownership evidence can be recorded. Refuse
    # before CoInitialize/DispatchEx. This is not an administrator requirement.
    try:
        from .hwp_desktop import desktop_activation_allowed
        desktop_allowed = desktop_activation_allowed()
    except Exception as exc:
        raise HwpAdapterError("desktop_preflight_unavailable") from exc
    if desktop_allowed is False:
        raise HwpAdapterError("restricted_desktop_token_requires_host_execution")
    if desktop_allowed is not True:
        raise HwpAdapterError("desktop_preflight_unavailable")
    try:
        import pythoncom  # type: ignore
        import win32com.client as win32_client  # type: ignore
    except Exception as exc:
        raise HwpAdapterError("hwp_runtime_unavailable: pywin32 is not importable") from exc

    dispatch: Any | None = None
    pythoncom.CoInitialize()
    try:
        # DispatchEx uses CoCreateInstanceEx directly.  This avoids pyhwpx.Hwp's
        # EnsureDispatch/connection path, which may attach to an active HWP.
        # Visibility is applied only after the returned process passes the
        # adapter's ownership verifier.
        dispatch = win32_client.DispatchEx("HWPFrame.HwpObject")
        return _DispatchExSession(dispatch, pythoncom)
    except Exception:
        if dispatch is not None:
            try:
                closer = getattr(dispatch, "Quit", None) or getattr(dispatch, "quit", None)
                if callable(closer):
                    try:
                        closer(save=False)
                    except TypeError:
                        closer()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        raise


def _call_insert_picture(
    toolkit: Any,
    image_path: Path,
    *,
    width_hwpunit: int,
    height_hwpunit: int,
    caption: str = "",
    para_pr_id: int = 20,
) -> Any:
    """The sole call site for the preserved picture signature."""

    validate_image_dimensions(width_hwpunit, height_hwpunit)
    return toolkit.insert_picture(
        image_path,
        width_hwpunit=width_hwpunit,
        height_hwpunit=height_hwpunit,
        caption=caption,
        para_pr_id=para_pr_id,
    )


def _normalise_image(item: Any) -> tuple[Path, int, int, str, int]:
    if isinstance(item, (str, os.PathLike)):
        value: Mapping[str, Any] = {"path": item}
    elif isinstance(item, Mapping):
        value = item
    else:
        raise ValueError("invalid_image: expected a path or mapping")
    raw_path = value.get("path") or value.get("image_path")
    if not isinstance(raw_path, (str, os.PathLike)):
        raise ValueError("invalid_image: path is required")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    width, height = validate_image_dimensions(
        value.get("width_hwpunit", 41954), value.get("height_hwpunit", 19928)
    )
    caption = value.get("caption", "")
    if not isinstance(caption, str):
        raise ValueError("invalid_image: caption must be a string")
    para_pr_id = value.get("para_pr_id", 20)
    if isinstance(para_pr_id, bool) or not isinstance(para_pr_id, int) or para_pr_id < 0:
        raise ValueError("invalid_image: para_pr_id must be a non-negative integer")
    return path, width, height, caption, para_pr_id


def _normalise_table(item: Any) -> tuple[int, int, list[list[str]], dict[str, Any]]:
    options: dict[str, Any] = {}
    if isinstance(item, Mapping):
        raw_data = item.get("data", [])
        options = {
            key: item[key]
            for key in ("col_widths", "col_aligns", "header_shaded", "booktabs")
            if key in item
        }
        rows = item.get("rows")
        cols = item.get("cols")
    else:
        raw_data = item
        rows = cols = None
    if not isinstance(raw_data, Sequence) or isinstance(raw_data, (str, bytes)):
        raise ValueError("invalid_table: data must be a sequence of rows")
    data: list[list[str]] = []
    for row in raw_data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            raise ValueError("invalid_table: each row must be a sequence")
        data.append([str(cell) for cell in row])
    inferred_rows = len(data)
    inferred_cols = max((len(row) for row in data), default=0)
    if rows is None:
        rows = inferred_rows
    if cols is None:
        cols = inferred_cols
    if isinstance(rows, bool) or isinstance(cols, bool) or not isinstance(rows, int) or not isinstance(cols, int):
        raise ValueError("invalid_table: rows and cols must be integers")
    if rows < 1 or cols < 1 or rows != inferred_rows or any(len(row) != cols for row in data):
        raise ValueError("invalid_table: rows and columns do not match data")
    return rows, cols, data, options


def _normalise_formula(item: Any) -> dict[str, Any]:
    if isinstance(item, str):
        return {"latex": validate_equation(item)}
    if not isinstance(item, Mapping):
        raise ValueError("invalid_equation: expected a string or mapping")
    raw = item.get("raw_hwpeqn")
    latex = item.get("latex", item.get("equation"))
    if raw is None and latex is None:
        raise ValueError("invalid_equation: latex or raw_hwpeqn is required")
    if raw is not None:
        validate_equation(raw, field="raw_hwpeqn")
    if latex is not None:
        validate_equation(latex, field="latex")
    font_size = item.get("font_size_pt", 10.0)
    if isinstance(font_size, bool) or not isinstance(font_size, (int, float)) or not 1 <= float(font_size) <= 200:
        raise ValueError("invalid_equation: font_size_pt must be between 1 and 200")
    return {
        "latex": latex or "",
        "raw_hwpeqn": raw,
        "font_size_pt": float(font_size),
        "inline": bool(item.get("inline", True)),
    }


def _validate_inline_equations(text: str) -> None:
    """Apply the explicit equation boundary to each ``$...$`` expression."""

    if "$" not in text:
        return
    if text.count("$") % 2:
        raise ValueError("invalid_equation: unmatched dollar delimiter")
    parts = text.split("$")
    for expression in parts[1::2]:
        validate_equation(expression, field="inline")


def _apply_template_projection(toolkit: Any, projection: Any) -> list[str]:
    """Apply or explicitly report the renderer template boundary."""

    if projection is None:
        return []
    if not isinstance(projection, Mapping):
        raise ValueError("invalid_renderer_template_projection")
    applier = getattr(toolkit, "apply_template_projection", None)
    if not callable(applier):
        return ["renderer_template_unapplied"]
    result = applier(dict(projection))
    if result is False:
        raise HwpAdapterError("renderer_template_rejected")
    return ["apply_template_projection"]


def _write_renderer_block(toolkit: Any, block: Mapping[str, Any], mode: str) -> list[str]:
    """Write one renderer-schema-2 block without changing its source order."""

    kind = block.get("type")
    if kind in {
        "paragraph",
        "choices",
        "surface",
        "answer",
        "explanation",
        "grading_criteria",
        "scoring_notes",
        "report_facts",
        "report_interpretation",
        "report_section",
    }:
        text = block.get("text", "")
        if not isinstance(text, str):
            raise ValueError(f"invalid_renderer_block:{kind}")
        if not hasattr(toolkit, "write_text"):
            raise HwpAdapterError("selected toolkit does not support renderer text blocks")
        kwargs = {
            key: block[key]
            for key in ("para_pr_id", "char_pr_id", "page_break", "underline")
            if key in block
        }
        # HWP text insertion treats a newline inside one payload as ordinary
        # text, which made report facts run together in native output.  Keep
        # the renderer block contract intact while emitting each fact line as
        # its own paragraph.  This preserves the source values and nesting;
        # the composer remains responsible for choosing labels.
        if kind == "report_facts" and "\n" in text:
            lines = text.splitlines()
            for line in lines:
                toolkit.write_text(line, **kwargs)
            return [f"write_text:{kind}"] * len(lines)
        toolkit.write_text(text, **kwargs)
        return [f"write_text:{kind}"]
    if kind == "table":
        rows, cols, data, options = _normalise_table(block)
        if not hasattr(toolkit, "insert_table"):
            raise HwpAdapterError("selected toolkit does not support renderer tables")
        toolkit.insert_table(rows, cols, data, **options)
        return ["insert_table"]
    if kind == "equation":
        formula = _normalise_formula(block.get("equation", block))
        if not hasattr(toolkit, "insert_math"):
            raise HwpAdapterError("selected toolkit does not support renderer equations")
        toolkit.insert_math(**formula)
        return ["insert_math"]
    if kind in {"image", "diagram"}:
        path = block.get("path") or block.get("image_path")
        if path:
            image_path, width, height, caption, para_pr_id = _normalise_image(block)
            _call_insert_picture(
                toolkit,
                image_path,
                width_hwpunit=width,
                height_hwpunit=height,
                caption=caption,
                para_pr_id=para_pr_id,
            )
            return ["insert_picture"]
        if kind == "diagram" and isinstance(block.get("text"), str) and block["text"]:
            if not hasattr(toolkit, "write_text"):
                raise HwpAdapterError("selected toolkit does not support diagram fallback text")
            toolkit.write_text(f"[도형] {block['text']}")
            return ["renderer_block_fallback:diagram"]
        raise ValueError(f"renderer_block_unrenderable:{kind}")
    raise ValueError(f"renderer_block_unrenderable:{kind}")


def _write_renderer_blocks(toolkit: Any, document: Mapping[str, Any], mode: str) -> list[str]:
    """Write renderer schema 2 blocks in item/report order."""

    operations = _apply_template_projection(toolkit, document.get("template_projection"))
    title = document.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise ValueError("invalid_document: title must be a string")
        if title and hasattr(toolkit, "write_text"):
            toolkit.write_text(title)
            operations.append("write_text:title")
    blocks = document.get("blocks", [])
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        raise ValueError("invalid_renderer_blocks")
    for entry_index, entry in enumerate(blocks):
        if not isinstance(entry, Mapping):
            raise ValueError("invalid_renderer_item_block")
        if entry.get("type") == "item":
            nested = entry.get("blocks", [])
            if not isinstance(nested, Sequence) or isinstance(nested, (str, bytes)):
                raise ValueError("invalid_renderer_item_blocks")
            for block in nested:
                if not isinstance(block, Mapping):
                    raise ValueError("invalid_renderer_block")
                operations.extend(_write_renderer_block(toolkit, block, mode))
            # The exam template's writing area is an item-local layout rule,
            # so apply it at the item boundary while the ordered renderer still
            # knows which blocks belong to that item.  Answers/report templates
            # normally set this to zero and therefore do not gain blank pages.
            projection = document.get("template_projection")
            spacing = projection.get("spacing", {}) if isinstance(projection, Mapping) else {}
            if isinstance(spacing, Mapping):
                workspace_mm = spacing.get(
                    "work_space_mm",
                    projection.get("answer_space_mm", 0) if isinstance(projection, Mapping) else 0,
                )
                has_later_item = any(
                    isinstance(candidate, Mapping) and candidate.get("type") == "item"
                    for candidate in blocks[entry_index + 1 :]
                )
                if (
                    not has_later_item
                    and isinstance(workspace_mm, (int, float))
                    and not isinstance(workspace_mm, bool)
                    and workspace_mm > 0
                ):
                    # A final writing area that does not fit after the last
                    # item creates a footer-only blank page in native HWP.
                    # Keep workspace blocks between items; the final item
                    # already owns the page's available answer area.
                    continue
                if isinstance(workspace_mm, (int, float)) and not isinstance(workspace_mm, bool) and workspace_mm > 0:
                    add_workspace = getattr(toolkit, "add_workspace", None)
                    if callable(add_workspace):
                        add_workspace(workspace_mm)
                        operations.append("add_workspace")
        else:
            operations.extend(_write_renderer_block(toolkit, entry, mode))
    return operations


def _write_document(toolkit: Any, document: Mapping[str, Any], mode: str) -> list[str]:
    """Apply the small document model to structural/live/modifier toolkits."""

    operations: list[str] = []
    if document.get("renderer_schema_version") == "2.0":
        return _write_renderer_blocks(toolkit, document, mode)
    title = document.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise ValueError("invalid_document: title must be a string")
        if title:
            if hasattr(toolkit, "write_text"):
                toolkit.write_text(title)
                operations.append("write_text:title")

    paragraphs = document.get("paragraphs", [])
    if paragraphs is None:
        paragraphs = []
    if not isinstance(paragraphs, Sequence) or isinstance(paragraphs, (str, bytes)):
        raise ValueError("invalid_document: paragraphs must be a sequence")
    for item in paragraphs:
        if isinstance(item, str):
            value: Mapping[str, Any] = {"text": item}
        elif isinstance(item, Mapping):
            value = item
        else:
            raise ValueError("invalid_paragraph: expected a string or mapping")
        text = value.get("text", "")
        if not isinstance(text, str):
            raise ValueError("invalid_paragraph: text must be a string")
        if value.get("latex") is not None and hasattr(toolkit, "write_math_paragraph"):
            latex_text = value["latex"]
            validate_equation(latex_text, field="latex")
            toolkit.write_math_paragraph(latex_text)
            operations.append("write_math_paragraph")
        elif hasattr(toolkit, "write_math_paragraph") and "$" in text:
            # Only explicit dollar-delimited expressions are treated as math.
            _validate_inline_equations(text)
            toolkit.write_math_paragraph(text)
            operations.append("write_math_paragraph")
        elif hasattr(toolkit, "write_text"):
            kwargs = {
                key: value[key]
                for key in ("para_pr_id", "char_pr_id", "page_break", "underline")
                if key in value
            }
            toolkit.write_text(text, **kwargs)
            operations.append("write_text:paragraph")

    formulas = document.get("equations", [])
    if formulas is None:
        formulas = []
    if not isinstance(formulas, Sequence) or isinstance(formulas, (str, bytes)):
        raise ValueError("invalid_document: equations must be a sequence")
    for item in formulas:
        formula = _normalise_formula(item)
        if not hasattr(toolkit, "insert_math"):
            raise HwpAdapterError("selected toolkit does not support equation insertion")
        toolkit.insert_math(**formula)
        operations.append("insert_math")

    tables = document.get("tables", [])
    if tables is None:
        tables = []
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        raise ValueError("invalid_document: tables must be a sequence")
    # Accept the ergonomic single-table form ``tables: [[...], [...]]`` as
    # well as the multi-table form ``tables: [{data: ...}, ...]``.
    if tables and all(
        isinstance(row, Sequence) and not isinstance(row, (str, bytes))
        and all(not isinstance(cell, (Sequence, Mapping)) or isinstance(cell, (str, bytes)) for cell in row)
        for row in tables
    ):
        tables = [tables]
    for item in tables:
        rows, cols, data, options = _normalise_table(item)
        if not hasattr(toolkit, "insert_table"):
            raise HwpAdapterError("selected toolkit does not support table insertion")
        toolkit.insert_table(rows, cols, data, **options)
        operations.append("insert_table")

    images = document.get("images", [])
    if images is None:
        images = []
    if not isinstance(images, Sequence) or isinstance(images, (str, bytes)):
        raise ValueError("invalid_document: images must be a sequence")
    for item in images:
        image_path, width, height, caption, para_pr_id = _normalise_image(item)
        _call_insert_picture(
            toolkit,
            image_path,
            width_hwpunit=width,
            height_hwpunit=height,
            caption=caption,
            para_pr_id=para_pr_id,
        )
        operations.append("insert_picture")

    # Modifier operations are opt-in and source-bound.  No document is opened
    # from a field inside the content mapping, which prevents accidental edits.
    modifications = document.get("modifications", [])
    if modifications and mode == "modifier":
        if not isinstance(modifications, Sequence) or isinstance(modifications, (str, bytes)):
            raise ValueError("invalid_document: modifications must be a sequence")
        for item in modifications:
            if not isinstance(item, Mapping) or "target" not in item or "text" not in item:
                raise ValueError("invalid_modification: target and text are required")
            if not hasattr(toolkit, "set_paragraph_text"):
                raise HwpAdapterError("selected toolkit does not support paragraph modification")
            toolkit.set_paragraph_text(item["target"], item["text"])
            operations.append("set_paragraph_text")
    return operations


def insert_picture(
    backend: Any,
    image_path: str | os.PathLike[str],
    *,
    width_hwpunit: int = 41954,
    height_hwpunit: int = 19928,
    caption: str = "",
    para_pr_id: int = 20,
    lock_path: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
    document_key: str | None = None,
) -> Any:
    """Public checked wrapper for the preserved ``insert_picture`` method.

    Picture insertion mutates the selected HWP backend, so it uses the same
    process-aware application lease as the document-level adapter.  Callers
    that already hold a lease should use the private ``_call_insert_picture``
    helper instead of nesting this public wrapper.
    """

    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with _public_session_lock(
        lock_path,
        default_root=_PROJECT_ROOT,
        run_id=run_id,
        document_key=document_key or str(path),
    ):
        return _call_insert_picture(
            backend,
            path,
            width_hwpunit=width_hwpunit,
            height_hwpunit=height_hwpunit,
            caption=caption,
            para_pr_id=para_pr_id,
        )


def _call_export_and_render_images(
    backend: Any,
    hwpx_path: Path,
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int,
) -> tuple[Path, list[Path]]:
    """The sole call site for the preserved export helper signature."""

    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 36 <= dpi <= 600:
        raise ValueError("invalid_dpi: expected an integer from 36 to 600")
    method = getattr(backend, "export_and_render_images", None)
    if not callable(method):
        raise HwpAdapterError("backend does not provide export_and_render_images")
    raw = method(hwpx_path=hwpx_path, pdf_path=pdf_path, output_dir=output_dir, dpi=dpi)
    if not isinstance(raw, tuple) or len(raw) != 2:
        raise HwpAdapterError("export_and_render_images returned an invalid result")
    returned_pdf, returned_images = raw
    returned_pdf = Path(returned_pdf).resolve()
    if not isinstance(returned_images, Sequence) or isinstance(returned_images, (str, bytes)):
        raise HwpAdapterError("export_and_render_images returned invalid image paths")
    images = [Path(item).resolve() for item in returned_images]
    if returned_pdf != pdf_path.resolve():
        raise HwpAdapterError("backend returned a PDF outside the claimed output path")
    if not returned_pdf.is_file():
        raise HwpAdapterError("hwp_export_failed: backend did not create the claimed PDF")
    if any(not _is_inside(item, output_dir.resolve()) for item in images):
        raise HwpAdapterError("backend returned an image outside the run directory")
    if not images or any(not item.is_file() for item in images):
        raise HwpAdapterError("pdf_render_failed: backend did not create every claimed page image")
    return returned_pdf, images


@contextmanager
def _public_session_lock(
    lock_path: str | os.PathLike[str] | None,
    *,
    default_root: Path,
    run_id: str | None,
    document_key: str,
) -> Iterator[AppSessionLock]:
    """Hold the shared application lease for a direct public wrapper call."""

    path = (
        Path(lock_path).expanduser().resolve()
        if lock_path is not None
        else (default_root.resolve() / ".academy-hwp-session.lock")
    )
    lease = AppSessionLock(
        path,
        run_id=_safe_name(run_id or f"wrapper-{uuid.uuid4().hex[:12]}", fallback="wrapper"),
        document_key=document_key,
    )
    with lease:
        yield lease


def export_and_render_images(
    backend: Any,
    hwpx_path: str | os.PathLike[str],
    *,
    pdf_path: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    dpi: int = 150,
    lock_path: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
    hwp_factory: Callable[..., Any] | None = None,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
) -> tuple[Path, list[Path]]:
    """Checked public wrapper for a backend's combined PDF/image export."""

    source = _assert_input_file(hwpx_path)
    if output_dir is None:
        raise ValueError("output_dir is required for export artifacts")
    target_dir = _resolve_output_dir(output_dir)
    target_pdf = Path(pdf_path or (target_dir / f"{source.stem}.pdf")).expanduser().resolve()
    _check_output_paths([target_pdf], target_dir)
    with _public_session_lock(
        lock_path,
        default_root=target_dir.parent,
        run_id=run_id,
        document_key=str(source),
    ):
        # Another caller may finish while this caller waits for the lease.
        _check_output_paths([target_pdf], target_dir)
        # The preserved toolkit's method enters the warm daemon and can run
        # global process cleanup.  Route it through the adapter-owned fresh
        # session instead of invoking that method from a public boundary.
        if _vendor_toolkit_instance(backend):
            exported_pdf = _safe_export_pdf(
                source,
                target_pdf,
                hwp_factory=hwp_factory,
                ownership_preflight=ownership_preflight,
                ownership_verifier=ownership_verifier,
            )
            rendered = _render_pdf_to_images(exported_pdf, target_dir, dpi=dpi, prefix=source.stem)
            return exported_pdf, rendered
        return _call_export_and_render_images(backend, source, target_pdf, target_dir, dpi=dpi)


def _create_new_hwp(
    hwp_factory: Callable[..., Any] | None = None,
    *,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
) -> tuple[Any, bool]:
    """Create a HWP session only after an explicit ownership proof boundary.

    ``pyhwpx`` accepts ``new=True`` to skip its running-object-table scan, but
    its later ``EnsureDispatch`` call is still COM-runtime dependent.  The
    adapter therefore runs a read-only process preflight before invoking the
    factory and a HWND-to-PID verifier for the returned session by default.
    Test or host-specific probes may be injected as a pair of callbacks.  The
    factory result and ``new=True`` alone never establish ownership.  If either
    proof is unavailable or false, no HWP object is returned and no cleanup
    method is called.
    """

    before_ids: set[int] | None = None
    if ownership_preflight is None and ownership_verifier is None:
        # The built-in path is read-only: an existing Hwp.exe means pyhwpx may
        # attach to a user's session, so refuse before calling its constructor.
        before_ids = _snapshot_hwp_process_ids()
        if before_ids is None:
            raise HwpOwnershipError(
                "hwp_ownership_unverified: HWP process preflight was unavailable; "
                "HWP factory was not called"
            )
        if before_ids:
            raise HwpOwnershipError(
                "hwp_ownership_unverified: an existing HWP process was detected; "
                "HWP factory was not called"
            )
    elif ownership_preflight is None or ownership_verifier is None:
        raise HwpOwnershipError(
            "hwp_ownership_unverified: provide both ownership callbacks or neither; "
            "HWP factory was not called"
        )
    else:
        try:
            if ownership_preflight() is not True:
                raise HwpOwnershipError(
                    "hwp_ownership_unverified: an existing or unknown HWP session was detected"
                )
        except HwpOwnershipError:
            raise
        except Exception as exc:
            raise HwpOwnershipError(
                "hwp_ownership_unverified: no-existing-session preflight failed; "
                f"{exc.__class__.__name__}: {exc}"
            ) from exc

    factory = hwp_factory
    if factory is None:
        factory = _make_default_hwp_factory
    factory_started_at = time.time()
    try:
        # The default factory uses DispatchEx rather than pyhwpx's
        # EnsureDispatch path.  The post-create verifier still requires a
        # newly observed HWP PID and immutable process identity evidence.
        hwp = factory(visible=False, new=True)
    except TypeError as exc:
        raise HwpAdapterError(
            "hwp_runtime_unavailable: new HWP session is unsupported; "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc
    except Exception as exc:
        # Preserve the COM exception class/HRESULT in the structured run
        # result.  A generic failure string cannot distinguish a server
        # activation failure from a missing package or a denied activation.
        raise HwpAdapterError(
            "hwp_runtime_unavailable: failed to create new HWP session; "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc
    try:
        verified = (
            _default_hwp_ownership_verifier(
                hwp,
                before_ids,
                factory_started_at=factory_started_at,
            )
            if ownership_verifier is None and before_ids is not None
            else ownership_verifier(hwp)
        )
    except Exception as exc:
        # The verifier could not establish ownership.  Deliberately do not call
        # quit/close here: the returned object may be an existing user session.
        raise HwpOwnershipError(
            "hwp_ownership_unverified: post-create session verification failed; "
            f"{exc.__class__.__name__}: {exc}"
        ) from exc
    if verified is not True:
        # Never close an object whose identity is uncertain.  A conservative
        # needs-review result is safer than terminating a user's HWP process.
        raise HwpOwnershipError(
            "hwp_ownership_unverified: returned HWP session ownership could not be proven"
        )
    set_visibility = getattr(hwp, "set_visibility", None)
    if callable(set_visibility):
        # The DispatchEx wrapper defers this property mutation until after the
        # process/PID proof.  A missing window property does not change the
        # ownership result, so keep the verified session usable for cleanup.
        try:
            set_visibility(False)
        except Exception:
            pass
    return hwp, True


def _close_owned_hwp(hwp: Any) -> None:
    for method_name, kwargs in (("Clear", {"arg": 1}), ("clear", {})):
        method = getattr(hwp, method_name, None)
        if not callable(method):
            continue
        try:
            if method_name == "Clear":
                method(1)
            else:
                method()
        except Exception:
            pass
        break
    for method_name in ("quit", "Quit", "close"):
        method = getattr(hwp, method_name, None)
        if callable(method):
            try:
                if method_name.lower() == "quit":
                    method(save=False)
                else:
                    method()
            except TypeError:
                try:
                    method()
                except Exception:
                    pass
            except Exception:
                pass
            break


def _safe_export_pdf(
    hwpx_path: Path,
    pdf_path: Path,
    *,
    hwp_factory: Callable[..., Any] | None = None,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
    owned_session: Any | None = None,
) -> Path:
    created_here = owned_session is None
    if created_here:
        hwp, owned = _create_new_hwp(
            hwp_factory,
            ownership_preflight=ownership_preflight,
            ownership_verifier=ownership_verifier,
        )
    else:
        hwp, owned = owned_session, True
    if not owned:
        raise HwpAdapterError("refusing to close a non-owned HWP session")
    try:
        opener = getattr(hwp, "open", None) or getattr(hwp, "Open", None)
        if not callable(opener):
            raise HwpAdapterError("hwp_runtime_unavailable: session cannot open a document")
        opener(str(hwpx_path))
        saver = getattr(hwp, "save_as", None) or getattr(hwp, "SaveAs", None)
        if not callable(saver):
            raise HwpAdapterError("hwp_runtime_unavailable: session cannot save PDF")
        try:
            saver(str(pdf_path), "PDF")
        except TypeError:
            saver(str(pdf_path), format="PDF")
        if not pdf_path.is_file():
            raise HwpAdapterError("hwp_export_failed: PDF output was not created")
        return pdf_path
    finally:
        if created_here:
            _close_owned_hwp(hwp)


def _render_pdf_to_images(pdf_path: Path, output_dir: Path, *, dpi: int, prefix: str) -> list[Path]:
    try:
        fitz = importlib.import_module("fitz")
    except Exception as exc:
        raise HwpAdapterError("pdf_renderer_unavailable: PyMuPDF is not importable") from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    result: list[Path] = []
    try:
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        for index in range(len(doc)):
            target = output_dir / f"{prefix}-{index + 1:03d}.png"
            if target.exists():
                raise FileExistsError(target)
            pixmap = doc[index].get_pixmap(matrix=matrix, alpha=False)
            pixmap.save(str(target))
            if not target.is_file():
                raise HwpAdapterError("pdf_render_failed: page image was not created")
            result.append(target)
    finally:
        doc.close()
    if not result:
        raise HwpAdapterError("pdf_render_failed: PDF contained no renderable pages")
    return result


def _safe_export_native_images(
    hwpx_path: Path,
    output_dir: Path,
    *,
    resolution: int,
    prefix: str,
    hwp_factory: Callable[..., Any] | None = None,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
    owned_session: Any | None = None,
) -> list[Path]:
    if isinstance(resolution, bool) or not isinstance(resolution, int) or not 36 <= resolution <= 600:
        raise ValueError("invalid_resolution: expected an integer from 36 to 600")
    created_here = owned_session is None
    if created_here:
        hwp, owned = _create_new_hwp(
            hwp_factory,
            ownership_preflight=ownership_preflight,
            ownership_verifier=ownership_verifier,
        )
    else:
        hwp, owned = owned_session, True
    if not owned:
        raise HwpAdapterError("refusing to close a non-owned HWP session")
    try:
        opener = getattr(hwp, "open", None) or getattr(hwp, "Open", None)
        if not callable(opener):
            raise HwpAdapterError("hwp_runtime_unavailable: session cannot open a document")
        opener(str(hwpx_path))
        # A reused live session may reopen the document while its caret still
        # belongs to a table cell or another sub-list.  Clear any selection and
        # return to the document list before asking HWP for a page image; this
        # keeps CreatePageImage from treating the current cell as the render
        # context.
        runner = getattr(hwp, "_run_action", None)
        if callable(runner):
            runner("Cancel")
            runner("MoveDocBegin")
        else:
            runner = getattr(hwp, "run", None) or getattr(hwp, "Run", None)
            if callable(runner):
                for action in ("Cancel", "MoveDocBegin"):
                    result = runner(action)
                    if result is False:
                        raise HwpAdapterError(f"native_render_failed: {action} action failed")
        page_count = getattr(hwp, "PageCount", None)
        if callable(page_count):
            page_count = page_count()
        if not isinstance(page_count, int) or page_count < 1:
            raise HwpAdapterError("native_render_failed: invalid page count")
        renderer = getattr(hwp, "create_page_image", None) or getattr(hwp, "CreatePageImage", None)
        if not callable(renderer):
            raise HwpAdapterError("native_renderer_unavailable: CreatePageImage is unavailable")
        output_dir.mkdir(parents=True, exist_ok=True)
        images: list[Path] = []
        for index in range(1, page_count + 1):
            target = output_dir / f"{prefix}-{index:03d}.png"
            if target.exists():
                raise FileExistsError(target)
            # Keep the native helper's documented signature in this one place.
            renderer(str(target), pgno=index, resolution=resolution, format="bmp")
            if not target.is_file():
                raise HwpAdapterError("native_render_failed: page image was not created")
            images.append(target)
        if not images:
            raise HwpAdapterError("native_render_failed: no page images were created")
        return images
    finally:
        if created_here:
            _close_owned_hwp(hwp)


def _call_export_native_images(
    backend: Any,
    source: Path,
    *,
    output_dir: Path,
    resolution: int = 150,
    prefix: str,
    hwp_factory: Callable[..., Any] | None = None,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
) -> list[Path]:
    method = getattr(backend, "export_native_images", None)
    if callable(method) and not _vendor_toolkit_instance(backend):
        raw = method(hwpx_path=source, output_dir=output_dir, resolution=resolution, prefix=prefix)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise HwpAdapterError("export_native_images returned invalid image paths")
        paths = [Path(item).resolve() for item in raw]
        if any(not _is_inside(item, output_dir.resolve()) for item in paths):
            raise HwpAdapterError("native renderer returned an image outside the run directory")
        if not paths or any(not item.is_file() for item in paths):
            raise HwpAdapterError("native_render_failed: backend did not create every claimed page image")
        return paths
    return _safe_export_native_images(
        source,
        output_dir,
        resolution=resolution,
        prefix=prefix,
        hwp_factory=hwp_factory,
        ownership_preflight=ownership_preflight,
        ownership_verifier=ownership_verifier,
    )


def export_native_images(
    backend: Any,
    hwpx_path: str | os.PathLike[str],
    *,
    output_dir: str | os.PathLike[str] | None = None,
    resolution: int = 150,
    prefix: str | None = None,
    lock_path: str | os.PathLike[str] | None = None,
    run_id: str | None = None,
    hwp_factory: Callable[..., Any] | None = None,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
) -> list[Path]:
    """Checked public wrapper for native Hancom page images."""

    source = _assert_input_file(hwpx_path)
    if output_dir is None:
        raise ValueError("output_dir is required for export artifacts")
    target_dir = _resolve_output_dir(output_dir)
    name = _safe_name(prefix or source.stem)
    with _public_session_lock(
        lock_path,
        default_root=target_dir.parent,
        run_id=run_id,
        document_key=str(source),
    ):
        return _call_export_native_images(
            backend,
            source,
            output_dir=target_dir,
            resolution=resolution,
            prefix=name,
            hwp_factory=hwp_factory,
            ownership_preflight=ownership_preflight,
            ownership_verifier=ownership_verifier,
        ) if not _vendor_toolkit_instance(backend) else _safe_export_native_images(
            source,
            target_dir,
            resolution=resolution,
            prefix=name,
            hwp_factory=hwp_factory,
            ownership_preflight=ownership_preflight,
            ownership_verifier=ownership_verifier,
        )


def _backend_can_export_pdf(backend: Any) -> bool:
    explicit = getattr(backend, "can_export_pdf", None)
    if explicit is not None:
        return bool(explicit() if callable(explicit) else explicit)
    return callable(getattr(backend, "export_and_render_images", None)) or callable(
        getattr(backend, "save_as", None)
    )


def _backend_can_native_images(backend: Any) -> bool:
    explicit = getattr(backend, "can_export_native_images", None)
    if explicit is not None:
        return bool(explicit() if callable(explicit) else explicit)
    return callable(getattr(backend, "export_native_images", None)) or callable(
        getattr(backend, "create_page_image", None)
    )


def _save_toolkit(toolkit: Any, target: Path) -> Path:
    saver = getattr(toolkit, "save", None)
    if callable(saver):
        saved = saver(target)
    else:
        saver = getattr(toolkit, "SaveAs", None) or getattr(toolkit, "save_as", None)
        if not callable(saver):
            raise HwpAdapterError("selected toolkit does not support saving")
        saver(str(target))
        saved = target
    if not target.is_file():
        raise HwpAdapterError("hwp_generation_failed: HWPX output was not created")
    return Path(saved).resolve() if isinstance(saved, (str, os.PathLike)) else target


def _result(
    *,
    status: str,
    chosen_mode: str,
    output_dir: Path,
    hwpx: Path | None,
    pdf: Path | None = None,
    images: Sequence[Path] = (),
    native_images: Sequence[Path] = (),
    issues: Sequence[str] = (),
    errors: Sequence[str] = (),
    run_id: str | None = None,
    input_path: Path | None = None,
    operations: Sequence[str] = (),
    lock_owner: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    actual_export = {
        "pdf": pdf is not None and pdf.is_file(),
        "images": bool(images) and all(path.is_file() for path in images),
        "native_images": bool(native_images) and all(path.is_file() for path in native_images),
    }
    paths: dict[str, Any] = {
        "output_dir": str(output_dir),
        "hwpx": str(hwpx) if hwpx else None,
        "pdf": str(pdf) if pdf else None,
        "images": [str(path) for path in images],
        "native_images": [str(path) for path in native_images],
    }
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "status": status,
        "chosen_mode": chosen_mode,
        "input": str(input_path) if input_path else None,
        "hwpx": str(hwpx) if hwpx else None,
        "pdf": str(pdf) if pdf else None,
        "images": [str(path) for path in images],
        "native_images": [str(path) for path in native_images],
        "paths": paths,
        "actual_export": actual_export,
        "exported": dict(actual_export),
        "operations": list(operations),
        "issues": list(dict.fromkeys(issues)),
        "errors": list(errors),
        "lock_owner": dict(lock_owner or {}),
    }


def render_document(
    document: Mapping[str, Any],
    output_dir: str | os.PathLike[str],
    *,
    mode: str | None = None,
    source_path: str | os.PathLike[str] | None = None,
    backend: Any | None = None,
    hwp_factory: Callable[..., Any] | None = None,
    ownership_preflight: HwpOwnershipPreflight | None = None,
    ownership_verifier: HwpOwnershipVerifier | None = None,
    run_id: str | None = None,
    dpi: int = 150,
    native_resolution: int = 150,
    lock_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Create/modify/render one document while holding the app session lease.

    Structural mode writes a new HWPX from the supplied mapping.  Modifier mode
    requires the explicit ``source_path`` and always writes a sibling output in
    the run directory.  Live mode requests a hidden ``new=True`` COM session
    only after a read-only process preflight (no existing HWP process) and
    HWND-to-PID verification (this returned session belongs to the run) pass.
    ``ownership_preflight`` and ``ownership_verifier`` can replace that pair
    for deterministic host/test probes, but must be supplied together.  The
    factory result and ``new=True`` alone never establish ownership; absent or
    failed proofs return ``needs_review`` before document mutation and never
    close the uncertain object.  A missing renderer still returns the generated
    HWPX, with ``pdf`` set to ``None`` and ``needs_review`` status.
    """

    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")
    if mode is None:
        mode = "modifier" if source_path is not None else "structural"
    if mode not in _VALID_MODES:
        raise ValueError(f"unknown HWP mode: {mode}")
    if mode == "modifier" and source_path is None:
        raise ValueError("modifier mode requires an explicit source_path")
    source = _assert_input_file(source_path) if source_path is not None else None
    output = _resolve_output_dir(output_dir)
    title = document.get("title", "document")
    if not isinstance(title, str):
        raise ValueError("invalid_document: title must be a string")
    rid = _safe_name(run_id or uuid.uuid4().hex[:12], fallback="run")
    # A caller supplied run ID is the stable retry identity.  Keeping it in
    # the artifact stem prevents a retried run with changed metadata from
    # silently producing a second file or overwriting the first output.
    if run_id:
        base = _safe_name(source.stem + "-modified" if source else run_id)
    else:
        base = _safe_name(source.stem + "-modified" if source else title)
    hwpx_path = output / f"{base}-{rid}.hwpx"
    pdf_path = output / f"{base}-{rid}.pdf"
    preview_dir = output / f"{base}-{rid}-pages"
    native_dir = output / f"{base}-{rid}-native"
    _check_output_paths([hwpx_path, pdf_path, preview_dir, native_dir], output)

    # The preserved modifier is an HWPX ZIP/OWPML editor.  A binary HWP must
    # fail at this adapter boundary with a useful format result rather than
    # reaching zipfile and being reported as an opaque adapter exception.
    if mode == "modifier" and source is not None and source.suffix.lower() == ".hwp":
        return _result(
            status="needs_review",
            chosen_mode=mode,
            output_dir=output,
            hwpx=None,
            issues=("unsupported_format",),
            errors=("modifier mode supports HWPX ZIP sources; binary .hwp is unsupported",),
            run_id=rid,
            input_path=source,
        )

    lock = AppSessionLock(
        lock_path or (output.parent / ".academy-hwp-session.lock"),
        run_id=rid,
        document_key=str(source or hwpx_path),
    )
    try:
        lock.acquire()
    except LockBusyError as exc:
        return _result(
            status="needs_review",
            chosen_mode=mode,
            output_dir=output,
            hwpx=None,
            issues=("lock_busy",),
            errors=(str(exc),),
            run_id=rid,
            input_path=source,
            lock_owner=exc.owner,
        )

    owned_live_session = False
    owned_session: Any | None = None
    toolkit = backend
    operations: list[str] = []
    issues: list[str] = []
    errors: list[str] = []
    pdf: Path | None = None
    images: list[Path] = []
    native_images: list[Path] = []
    try:
        # Recheck under the lease before creating a backend or writing bytes.
        _check_output_paths([hwpx_path, pdf_path, preview_dir, native_dir], output)
        if mode == "live" and toolkit is not None:
            raise HwpOwnershipError(
                "hwp_ownership_unverified: live mode requires an adapter-created "
                "session factory; supplied backend ownership is not accepted"
            )
        if mode == "structural" and toolkit is None:
            toolkit = _make_structural_toolkit(title)
        elif mode == "modifier" and toolkit is None:
            assert source is not None
            toolkit = _make_modifier_toolkit(source)
        elif mode == "live" and toolkit is None:
            toolkit, owned_live_session = _create_new_hwp(
                hwp_factory,
                ownership_preflight=ownership_preflight,
                ownership_verifier=ownership_verifier,
            )
            if owned_live_session:
                # Keep the raw object independently of any toolkit wrapper.
                # HwpAgentToolkit does not expose a close method of its own,
                # while pyhwpx's raw object is the only cleanup boundary we
                # can reliably invoke after a COM session request.
                owned_session = toolkit
            # A raw DispatchEx object gets an explicit compatibility layer
            # after ownership is proven.  The layer keeps cursor/equation
            # observation in the preserved toolkit while routing operations
            # that otherwise silently no-op on raw COM through adapter helpers.
            if isinstance(toolkit, _DispatchExSession):
                toolkit_type = _load_vendor_toolkit()
                vendor_toolkit = toolkit_type(mode="live", hwp_instance=toolkit)
                toolkit = _LiveToolkitCompatibility(toolkit, vendor_toolkit)
            # Custom factories may return a pyhwpx-like object directly.  Keep
            # the historical wrapper path for those explicit host probes.
            elif not hasattr(toolkit, "write_text") and hasattr(toolkit, "Run"):
                toolkit_type = _load_vendor_toolkit()
                toolkit = toolkit_type(mode="live", hwp_instance=toolkit)

        if toolkit is None:
            raise HwpAdapterError("no toolkit backend available")
        operations = _write_document(toolkit, document, mode)
        if "renderer_template_unapplied" in operations:
            issues.append("renderer_template_unapplied")
        if any(operation.startswith("renderer_block_fallback:") for operation in operations):
            issues.append("renderer_block_fallback")
        _save_toolkit(toolkit, hwpx_path)

        if not _backend_can_export_pdf(toolkit):
            issues.append("render_unavailable")
        else:
            try:
                if callable(getattr(toolkit, "export_and_render_images", None)) and not _vendor_toolkit_instance(toolkit):
                    pdf, rendered = _call_export_and_render_images(
                        toolkit, hwpx_path, pdf_path, preview_dir, dpi=dpi
                    )
                    images.extend(path for path in rendered if path.is_file())
                else:
                    pdf = _safe_export_pdf(
                        hwpx_path,
                        pdf_path,
                        hwp_factory=hwp_factory,
                        ownership_preflight=ownership_preflight,
                        ownership_verifier=ownership_verifier,
                        owned_session=owned_session,
                    )
                    images.extend(
                        _render_pdf_to_images(pdf, preview_dir, dpi=dpi, prefix=pdf.stem)
                    )
            except FileExistsError:
                raise
            except HwpOwnershipError as exc:
                issues.append("ownership_unverified")
                errors.append(f"{exc.__class__.__name__}: {exc}")
                pdf = None
            except Exception as exc:
                issues.append("render_unavailable")
                errors.append(f"{exc.__class__.__name__}: {exc}")
                pdf = None

        if _backend_can_native_images(toolkit):
            try:
                if callable(getattr(toolkit, "export_native_images", None)) and not _vendor_toolkit_instance(toolkit):
                    native_images.extend(
                        _call_export_native_images(
                            toolkit,
                            hwpx_path,
                            output_dir=native_dir,
                            resolution=native_resolution,
                            prefix=pdf.stem if pdf else base,
                            hwp_factory=hwp_factory,
                            ownership_preflight=ownership_preflight,
                            ownership_verifier=ownership_verifier,
                        )
                    )
                else:
                    native_images.extend(
                        _safe_export_native_images(
                            hwpx_path,
                            native_dir,
                            resolution=native_resolution,
                            prefix=pdf.stem if pdf else base,
                            hwp_factory=hwp_factory,
                            ownership_preflight=ownership_preflight,
                            ownership_verifier=ownership_verifier,
                            owned_session=owned_session,
                        )
                    )
            except HwpOwnershipError as exc:
                issues.append("ownership_unverified")
                errors.append(f"{exc.__class__.__name__}: {exc}")
            except Exception as exc:
                issues.append("native_render_unavailable")
                errors.append(f"{exc.__class__.__name__}: {exc}")

        completed = (
            pdf is not None
            and pdf.is_file()
            and bool(images)
            and all(path.is_file() for path in images)
        )
        status = "completed" if completed and not issues else "needs_review"
        if not completed and "render_unavailable" not in issues:
            issues.append("render_unavailable")
        return _result(
            status=status,
            chosen_mode=mode,
            output_dir=output,
            hwpx=hwpx_path,
            pdf=pdf,
            images=images,
            native_images=native_images,
            issues=issues,
            errors=errors,
            run_id=rid,
            input_path=source,
            operations=operations,
            lock_owner=lock.owner,
        )
    except (ValueError, FileNotFoundError, FileExistsError):
        raise
    except HwpOwnershipError as exc:
        errors.append(f"{exc.__class__.__name__}: {exc}")
        return _result(
            status="needs_review",
            chosen_mode=mode,
            output_dir=output,
            hwpx=hwpx_path if hwpx_path.is_file() else None,
            pdf=pdf,
            images=images,
            native_images=native_images,
            issues=("ownership_unverified",),
            errors=errors,
            run_id=rid,
            input_path=source,
            operations=operations,
            lock_owner=lock.owner,
        )
    except Exception as exc:
        errors.append(f"{exc.__class__.__name__}: {exc}")
        return _result(
            status="failed",
            chosen_mode=mode,
            output_dir=output,
            hwpx=hwpx_path if hwpx_path.is_file() else None,
            pdf=pdf,
            images=images,
            native_images=native_images,
            issues=("adapter_failed",),
            errors=errors,
            run_id=rid,
            input_path=source,
            operations=operations,
            lock_owner=lock.owner,
        )
    finally:
        if owned_live_session and owned_session is not None:
            _close_owned_hwp(owned_session)
        lock.release()


def check_hwp_runtime() -> dict[str, Any]:
    """Inspect HWP-related capability without launching Office or a daemon."""

    hwp_candidates: list[Path] = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            hwp_candidates.append(Path(root) / "Hnc" / "Office 2022" / "HOffice120" / "Bin" / "Hwp.exe")
    hwp_candidates.append(Path(r"C:\Program Files (x86)\Hnc\Office 2022\HOffice120\Bin\Hwp.exe"))
    hwp_path = next((path for path in hwp_candidates if path.is_file()), None)
    bridge = _VENDOR_ROOT / "bridge" / "latex_to_hwpeqn.js"
    node_candidates = [shutil.which("node"), shutil.which("node.exe")]
    if os.environ.get("ACADEMY_NODE"):
        node_candidates.insert(0, os.environ["ACADEMY_NODE"])
    node_path = next((Path(path) for path in node_candidates if path and Path(path).is_file()), None)
    try:
        importlib.import_module("pyhwpx")
    except Exception:
        pyhwpx_available = False
    else:
        pyhwpx_available = True
    return {
        "document_generation": {
            "available": (_VENDOR_ROOT / "hwpx_builder.py").is_file(),
            "status": "available" if (_VENDOR_ROOT / "hwpx_builder.py").is_file() else "unavailable",
            "tested": False,
            "detail": "Structural HWPX generation is local and does not require Office",
        },
        "hwp_executable": {
            "available": hwp_path is not None,
            "status": "detected_not_tested" if hwp_path else "unavailable",
            "tested": False,
            "path": str(hwp_path) if hwp_path else None,
            "detail": "HWP executable detected; opening a document was not tested" if hwp_path else "HWP executable was not found",
        },
        "pyhwpx": {
            "available": pyhwpx_available,
            "status": "available" if pyhwpx_available else "unavailable",
            "tested": False,
            "detail": "pyhwpx import was checked; no document was opened" if pyhwpx_available else "pyhwpx is not importable",
        },
        "equation_bridge": {
            "available": bridge.is_file() and node_path is not None,
            "status": "available" if bridge.is_file() and node_path is not None else "unavailable",
            "tested": False,
            "path": str(bridge) if bridge.is_file() else None,
            "node_path": str(node_path) if node_path else None,
            "detail": "Equation bridge and Node executable detected" if bridge.is_file() and node_path else "Equation bridge or Node executable unavailable",
        },
        "pdf_export": {
            "available": hwp_path is not None and pyhwpx_available,
            "status": "detected_not_tested" if hwp_path is not None and pyhwpx_available else "unavailable",
            "tested": False,
            "detail": "A fresh HWP session is required; PDF export was not run",
        },
        "native_page_images": {
            "available": hwp_path is not None and pyhwpx_available,
            "status": "detected_not_tested" if hwp_path is not None and pyhwpx_available else "unavailable",
            "tested": False,
            "detail": "CreatePageImage was not run",
        },
    }


__all__ = [
    "HwpAdapterError",
    "HwpOwnershipError",
    "check_hwp_runtime",
    "export_and_render_images",
    "export_native_images",
    "insert_picture",
    "render_document",
    "validate_equation",
    "validate_image_dimensions",
]
