#!/usr/bin/env python3
"""Inspect and validate HWPX packages without opening Hancom Office.

This is a deliberately narrow package gate.  It checks ZIP safety, the HWPX
``mimetype`` convention, required members, XML well-formedness, manifest
targets, and a small text/object inventory.  It does not claim schema validity,
reopen safety, or visual correctness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


SCHEMA_VERSION = "hwp-automation.hwpx-inspection/v1"
DEFAULT_MAX_ENTRY_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_SIZE = 200 * 1024 * 1024
REQUIRED_MEMBERS = ("mimetype", "Contents/content.hpf", "Contents/header.xml", "Contents/section0.xml")
XML_SUFFIXES = frozenset({".xml", ".hpf", ".rdf"})
ZIP_METADATA_FIELDS = (
    "date_time",
    "compress_type",
    "flag_bits",
    "create_system",
    "create_version",
    "extract_version",
    "volume",
    "internal_attr",
    "external_attr",
    "comment",
    "extra",
)


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _issue(code: str, message: str, *, member: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if member is not None:
        result["member"] = member
    return result


def _check(status: str, *, issues: Sequence[dict[str, Any]] = (), detail: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"status": status}
    if issues:
        result["issues"] = list(issues)
    if detail is not None:
        result["detail"] = detail
    return result


def _safe_member_name(name: str) -> bool:
    if not isinstance(name, str) or not name or "\x00" in name:
        return False
    # ZIP member names are not URLs, but rejecting decoded separators and
    # traversal segments closes the common percent-encoding bypass as well.
    decoded = name
    for _ in range(8):
        unquoted = unquote(decoded)
        if unquoted == decoded:
            break
        decoded = unquoted
    if "\x00" in decoded:
        return False
    normalized = decoded.replace("\\", "/")
    if normalized.startswith(("/", "\\")) or posixpath.isabs(normalized):
        return False
    windows = PureWindowsPath(decoded)
    if windows.drive or windows.is_absolute():
        return False
    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        return False
    return not any(part[:2].endswith(":") for part in parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry_record(info: zipfile.ZipInfo, data: bytes) -> dict[str, Any]:
    return {
        "name": info.filename,
        "size": len(data),
        "compressed_size": int(info.compress_size),
        "compression": "stored" if info.compress_type == zipfile.ZIP_STORED else str(info.compress_type),
        "crc32": f"{int(info.CRC) & 0xFFFFFFFF:08x}",
        "sha256": _sha256(data),
    }


def _read_archive(
    path: Path,
    *,
    max_entry_size: int,
    max_archive_size: int,
) -> tuple[dict[str, tuple[zipfile.ZipInfo, bytes]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read a bounded archive and return members, records, and safety issues."""

    issues: list[dict[str, Any]] = []
    try:
        archive = zipfile.ZipFile(path, "r")
    except FileNotFoundError:
        return {}, [], [_issue("missing_file", f"HWPX file does not exist: {path}")]
    except (OSError, zipfile.BadZipFile) as exc:
        return {}, [], [_issue("invalid_zip", f"Cannot read HWPX ZIP: {exc}")]

    members: dict[str, tuple[zipfile.ZipInfo, bytes]] = {}
    records: list[dict[str, Any]] = []
    total_size = 0
    try:
        infos = archive.infolist()
        for info in infos:
            name = str(info.filename)
            if name in members:
                issues.append(_issue("duplicate_member", f"Duplicate ZIP member: {name}", member=name))
                continue
            if not _safe_member_name(name):
                issues.append(_issue("member_path_escape", f"Unsafe ZIP member path: {name}", member=name))
                continue
            if _is_symlink(info):
                issues.append(_issue("symlink_member", f"Symbolic-link ZIP member is not supported: {name}", member=name))
                continue
            if info.file_size > max_entry_size:
                issues.append(_issue("entry_too_large", f"ZIP member exceeds size limit: {name}", member=name))
                continue
            total_size += int(info.file_size)
            if total_size > max_archive_size:
                issues.append(_issue("archive_too_large", "Total uncompressed HWPX size exceeds limit"))
                break
            try:
                data = archive.read(info)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                issues.append(_issue("member_read_failed", f"Cannot read ZIP member: {exc}", member=name))
                continue
            if len(data) > max_entry_size:
                issues.append(_issue("entry_too_large", f"Decompressed ZIP member exceeds size limit: {name}", member=name))
                continue
            members[name] = (info, data)
            records.append(_entry_record(info, data))
    finally:
        archive.close()
    return members, records, issues


def _manifest_targets(
    members: Mapping[str, tuple[zipfile.ZipInfo, bytes]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return manifest entries and reference issues for ``content.hpf``."""

    entry = members.get("Contents/content.hpf")
    if entry is None:
        return [], [_issue("missing_manifest", "Contents/content.hpf is missing")]
    data = entry[1]
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return [], [_issue("xml_unsafe_entity", "DTD/entity declarations are not supported", member="Contents/content.hpf")]
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return [], [_issue("malformed_xml", f"Malformed content.hpf: {exc}", member="Contents/content.hpf")]

    refs: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in root.iter():
        if _local_name(item.tag) != "item":
            continue
        item_id = str(item.attrib.get("id", ""))
        href = str(item.attrib.get("href", ""))
        if item_id and item_id in seen_ids:
            issues.append(_issue("duplicate_manifest_id", f"Duplicate manifest id: {item_id}", member="Contents/content.hpf"))
        if item_id:
            seen_ids.add(item_id)
        # Native Hancom uses package-root paths, including root settings.xml.
        # Retain support for compact manifests relative to Contents.
        parsed_href = urlsplit(href)
        href_path = parsed_href.path
        normalized_href = href_path.replace("\\", "/")
        # Most HWPX manifests use root-relative ``Contents/...`` and
        # ``BinData/...`` hrefs; compact fixtures sometimes use a path
        # relative to ``Contents``.  Accept both explicit forms without
        # allowing an absolute or parent traversal path.
        if normalized_href in members or normalized_href.startswith(("Contents/", "BinData/", "META-INF/", "Preview/", "Scripts/")):
            joined = posixpath.normpath(normalized_href)
        else:
            joined = posixpath.normpath(posixpath.join("Contents", normalized_href))
        unsafe = not href or bool(parsed_href.scheme or parsed_href.netloc) or href_path.startswith(("/", "\\")) or ".." in PurePosixPath(normalized_href).parts
        exists = not unsafe and joined in members
        ref = {"id": item_id or None, "href": href, "target": joined, "exists": exists}
        refs.append(ref)
        if unsafe:
            issues.append(_issue("manifest_href_escape", f"Unsafe manifest href: {href}", member="Contents/content.hpf"))
        elif not exists:
            issues.append(_issue("manifest_target_missing", f"Manifest target is missing: {joined}", member="Contents/content.hpf"))
    if not refs:
        issues.append(_issue("manifest_empty", "content.hpf contains no manifest items", member="Contents/content.hpf"))
    return refs, issues


def _parse_xml_members(
    members: Mapping[str, tuple[zipfile.ZipInfo, bytes]],
) -> tuple[dict[str, ET.Element], list[dict[str, Any]]]:
    trees: dict[str, ET.Element] = {}
    issues: list[dict[str, Any]] = []
    for name, (_, data) in members.items():
        if Path(name).suffix.lower() not in XML_SUFFIXES:
            continue
        upper = data.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            issues.append(_issue("xml_unsafe_entity", "DTD/entity declarations are not supported", member=name))
            continue
        try:
            trees[name] = ET.fromstring(data)
        except ET.ParseError as exc:
            issues.append(_issue("malformed_xml", f"Malformed XML: {exc}", member=name))
    return trees, issues


def _inventory(
    trees: Mapping[str, ET.Element],
    member_names: Iterable[str] = (),
) -> dict[str, Any]:
    sections = [root for name, root in trees.items() if name.startswith("Contents/section") and name.lower().endswith(".xml")]
    paragraphs = 0
    text_fragments: list[str] = []
    equations = tables = pictures = 0
    binary_refs: set[str] = set()
    for root in sections:
        for node in root.iter():
            local = _local_name(node.tag)
            if local == "p":
                paragraphs += 1
            elif local == "t":
                text_fragments.append(node.text or "")
            elif local == "equation":
                equations += 1
            elif local == "tbl":
                tables += 1
            elif local == "pic":
                pictures += 1
            for key, value in node.attrib.items():
                if str(key).lower().endswith("binaryitemidref") and value:
                    binary_refs.add(str(value))
    return {
        "sections": len(sections),
        "paragraphs": paragraphs,
        "text_fragments": text_fragments,
        "text_characters": sum(len(value) for value in text_fragments),
        "equations": equations,
        "tables": tables,
        "pictures": pictures,
        "binary_refs": sorted(binary_refs),
        "binary_members": sorted(name for name in member_names if name.startswith("BinData/")),
    }


def _section_reference_check(
    trees: Mapping[str, ET.Element],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header = trees.get("Contents/header.xml")
    section = trees.get("Contents/section0.xml")
    if header is None or section is None:
        return _check("not_applicable", detail="header.xml and section0.xml are required for ID-reference checks"), []
    char_ids = {str(node.attrib["id"]) for node in header.iter() if _local_name(node.tag) == "charPr" and "id" in node.attrib}
    para_ids = {str(node.attrib["id"]) for node in header.iter() if _local_name(node.tag) == "paraPr" and "id" in node.attrib}
    if not char_ids and not para_ids:
        return _check("not_applicable", detail="header.xml declares no character/paragraph property IDs"), []
    issues: list[dict[str, Any]] = []
    for node in section.iter():
        char_ref = node.attrib.get("charPrIDRef")
        para_ref = node.attrib.get("paraPrIDRef")
        if char_ref is not None and char_ids and str(char_ref) not in char_ids:
            issues.append(_issue("missing_char_pr_ref", f"Unknown charPrIDRef: {char_ref}", member="Contents/section0.xml"))
        if para_ref is not None and para_ids and str(para_ref) not in para_ids:
            issues.append(_issue("missing_para_pr_ref", f"Unknown paraPrIDRef: {para_ref}", member="Contents/section0.xml"))
    return _check("failed" if issues else "passed", issues=issues), issues


def inspect_hwpx(
    path: str | os.PathLike[str],
    *,
    max_entry_size: int = DEFAULT_MAX_ENTRY_SIZE,
    max_archive_size: int = DEFAULT_MAX_ARCHIVE_SIZE,
) -> dict[str, Any]:
    """Inspect one HWPX path and return a JSON-serializable report."""

    resolved = Path(path).expanduser().resolve(strict=False)
    if resolved.suffix.lower() == ".hwp":
        issue = _issue("unsupported_binary_hwp", "Binary .hwp is outside this HWPX package inspector")
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(resolved),
            "ok": False,
            "issues": [issue],
            "checks": {"archive": _check("failed", issues=[issue])},
            "members": [],
            "inventory": {"sections": 0, "paragraphs": 0, "text_fragments": [], "text_characters": 0, "equations": 0, "tables": 0, "pictures": 0, "binary_refs": [], "binary_members": []},
            "verification": {"package": "failed", "reopen": "not_performed", "visual": "not_performed"},
        }
    if resolved.suffix.lower() != ".hwpx":
        issue = _issue("unsupported_extension", "Expected an .hwpx input")
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(resolved),
            "ok": False,
            "issues": [issue],
            "checks": {"archive": _check("failed", issues=[issue])},
            "members": [],
            "inventory": {},
            "verification": {"package": "failed", "reopen": "not_performed", "visual": "not_performed"},
        }

    members, records, archive_issues = _read_archive(
        resolved,
        max_entry_size=max_entry_size,
        max_archive_size=max_archive_size,
    )
    issues = list(archive_issues)
    checks: dict[str, Any] = {}
    if archive_issues and not members:
        checks["archive"] = _check("failed", issues=archive_issues)
        return {
            "schema_version": SCHEMA_VERSION,
            "path": str(resolved),
            "ok": False,
            "issues": issues,
            "checks": checks,
            "members": records,
            "inventory": {},
            "verification": {"package": "failed", "reopen": "not_performed", "visual": "not_performed"},
        }
    checks["archive"] = _check("failed" if archive_issues else "passed", issues=archive_issues, detail="bounded ZIP read")

    names = list(members)
    mimetype_issues: list[dict[str, Any]] = []
    if not names or names[0] != "mimetype":
        mimetype_issues.append(_issue("mimetype_not_first", "mimetype must be the first ZIP member", member="mimetype"))
    mimetype = members.get("mimetype")
    if mimetype is None:
        mimetype_issues.append(_issue("missing_mimetype", "mimetype is missing", member="mimetype"))
    else:
        if mimetype[0].compress_type != zipfile.ZIP_STORED:
            mimetype_issues.append(_issue("mimetype_not_stored", "mimetype must use ZIP_STORED", member="mimetype"))
        if mimetype[1] != b"application/hwp+zip":
            mimetype_issues.append(_issue("invalid_mimetype", "mimetype payload must be application/hwp+zip", member="mimetype"))
    checks["mimetype"] = _check("failed" if mimetype_issues else "passed", issues=mimetype_issues)
    issues.extend(mimetype_issues)

    required_issues = [
        _issue("missing_required_member", f"Required HWPX member is missing: {name}", member=name)
        for name in REQUIRED_MEMBERS
        if name not in members
    ]
    checks["required_members"] = _check("failed" if required_issues else "passed", issues=required_issues, detail=list(REQUIRED_MEMBERS))
    issues.extend(required_issues)

    trees, xml_issues = _parse_xml_members(members)
    checks["xml"] = _check("failed" if xml_issues else "passed", issues=xml_issues)
    issues.extend(xml_issues)

    refs, ref_issues = _manifest_targets(members)
    checks["manifest_refs"] = _check("failed" if ref_issues else "passed", issues=ref_issues, detail=refs)
    issues.extend(ref_issues)

    section_check, section_issues = _section_reference_check(trees)
    checks["section_refs"] = section_check
    issues.extend(section_issues)

    inventory = _inventory(trees, members)
    package_status = "failed" if issues else "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "path": str(resolved),
        "ok": not issues,
        "issues": issues,
        "checks": checks,
        "members": records,
        "inventory": inventory,
        "verification": {"package": package_status, "reopen": "not_performed", "visual": "not_performed"},
    }


def _zip_metadata(info: zipfile.ZipInfo) -> tuple[Any, ...]:
    return tuple(getattr(info, name) for name in ZIP_METADATA_FIELDS)


def preservation_receipt(
    before: str | os.PathLike[str],
    after: str | os.PathLike[str],
    *,
    allowed_changes: Iterable[str] = (),
    max_entry_size: int = DEFAULT_MAX_ENTRY_SIZE,
    max_archive_size: int = DEFAULT_MAX_ARCHIVE_SIZE,
) -> dict[str, Any]:
    """Measure package/member preservation between two HWPX files.

    ``zip_metadata`` compares exposed ``ZipInfo`` fields only.  It does not
    claim that raw local-record bytes survived a repack.
    """

    before_path = Path(before).expanduser().resolve(strict=False)
    after_path = Path(after).expanduser().resolve(strict=False)
    before_members, _, before_issues = _read_archive(
        before_path,
        max_entry_size=max_entry_size,
        max_archive_size=max_archive_size,
    )
    after_members, _, after_issues = _read_archive(
        after_path,
        max_entry_size=max_entry_size,
        max_archive_size=max_archive_size,
    )
    before_report = inspect_hwpx(
        before_path,
        max_entry_size=max_entry_size,
        max_archive_size=max_archive_size,
    )
    after_report = inspect_hwpx(
        after_path,
        max_entry_size=max_entry_size,
        max_archive_size=max_archive_size,
    )
    issues = [*before_report.get("issues", []), *after_report.get("issues", [])]
    allowed = {str(value).replace("\\", "/") for value in allowed_changes}
    all_names = set(before_members) | set(after_members)
    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    for name in sorted(all_names):
        if name not in before_members:
            added.append(name)
        elif name not in after_members:
            removed.append(name)
        elif before_members[name][1] != after_members[name][1]:
            changed.append(name)
    all_differences = set(changed) | set(added) | set(removed)
    unexpected = sorted(all_differences - allowed)
    if unexpected:
        issues.extend(_issue("unexpected_changed_member", f"Member changed outside allowed set: {name}", member=name) for name in unexpected)

    untouched_names = sorted(all_names - allowed)
    payload_verified = payload_changed = metadata_verified = metadata_changed = 0
    for name in untouched_names:
        if name not in before_members or name not in after_members:
            payload_changed += 1
            metadata_changed += 1
            continue
        before_info, before_data = before_members[name]
        after_info, after_data = after_members[name]
        if before_data == after_data:
            payload_verified += 1
        else:
            payload_changed += 1
        if _zip_metadata(before_info) == _zip_metadata(after_info):
            metadata_verified += 1
        else:
            metadata_changed += 1

    try:
        whole_package_identical = before_path.read_bytes() == after_path.read_bytes()
    except OSError:
        whole_package_identical = False
    package_ok = bool(before_report.get("ok")) and bool(after_report.get("ok")) and not unexpected
    return {
        "schema_version": "hwp-automation.mutation-receipt/v1",
        "before": str(before_path),
        "after": str(after_path),
        "ok": package_ok,
        "allowed_changes": sorted(allowed),
        "changed_members": sorted(all_differences),
        "added_members": added,
        "removed_members": removed,
        "unexpected_changes": unexpected,
        "whole_package_identical": whole_package_identical,
        "preservation": {
            "untouched_part_payloads": {"verified": payload_verified, "changed": payload_changed},
            "zip_metadata": {"verified": metadata_verified, "changed": metadata_changed},
            "whole_package_identical": whole_package_identical,
        },
        "verification": {
            "package": "passed" if package_ok else "failed",
            "reopen": "not_performed",
            "visual": "not_performed",
        },
        "validation": {
            "before": {
                "ok": bool(before_report.get("ok")),
                "issues": list(before_report.get("issues", [])),
            },
            "after": {
                "ok": bool(after_report.get("ok")),
                "issues": list(after_report.get("issues", [])),
            },
        },
        "issues": issues,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect an HWPX package without opening Hancom Office")
    parser.add_argument("path", help="input .hwpx path")
    parser.add_argument("--compare-to", default=None, help="optional original .hwpx for a preservation receipt")
    parser.add_argument("--json", action="store_true", dest="json_mode", help="emit a machine-readable report")
    args = parser.parse_args(argv)
    if args.compare_to:
        report = preservation_receipt(args.compare_to, args.path)
    else:
        report = inspect_hwpx(args.path)
    if args.json_mode:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        state = "VALID" if report.get("ok") else "INVALID"
        print(f"{state}: {report.get('after') or report.get('path')}")
        for issue in report.get("issues", []):
            print(f"- {issue.get('code')}: {issue.get('message')}")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":  # pragma: no cover - exercised by CLI
    raise SystemExit(main())


__all__ = ["inspect_hwpx", "main", "preservation_receipt"]
