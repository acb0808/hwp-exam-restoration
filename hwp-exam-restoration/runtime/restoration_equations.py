"""Safe equation handoff for the experimental HWP Exam Studio skill.

The module is intentionally a pure boundary.  It translates the JSON contract
from :mod:`latex_to_hwpeqn` into receipts that the native HWP layer can consume
without pretending that parsing or script generation is rendering validation.
No COM, HWP process, legacy Node bridge, or document mutation is performed here.
"""

from __future__ import annotations

import copy
import json
from importlib.resources import files
from typing import Any, Iterable

from latex_to_hwpeqn import convert_document_for_editor, convert_for_editor


class EquationError(ValueError):
    """A rejected equation handoff with a stable machine-readable ``code``."""

    def __init__(self, code: str, message: str, *, diagnostics=None):
        self.code = code
        # Preserve engine evidence for the public diagnostic boundary. Do not
        # reparse failing expressions or replace their original source.
        self.diagnostics = copy.deepcopy(diagnostics or [])
        super().__init__(f"{code}: {message}")


_SOURCE_KEYS = ("latex", "editor_json", "hwpeqn")
_KNOWN_REQUIREMENTS = {
    "tree_v1",
    "environment",
    "extra_row_spacing",
    "style_declaration",
    "math_alphabet",
    "font",
    "math_style",
    "tex_spacing",
    "fallback_review",
}
_NODE_TYPES = {
    "empty",
    "literal",
    "atom",
    "text",
    "style_declaration",
    "seq",
    "group",
    "frac",
    "atop",
    "binom",
    "root",
    "scripts",
    "overset",
    "accent",
    "font",
    "math_alphabet",
    "modulo",
    "prefix",
    "relation",
    "delimited",
    "row",
    "environment",
}
_LAYOUT_REQUIREMENTS = {
    "environment",
    "extra_row_spacing",
    "math_style",
    "tex_spacing",
    "font",
    "math_alphabet",
}


def _error(code: str, message: str) -> EquationError:
    return EquationError(code, message)


def _schema() -> dict[str, Any]:
    resource = files("latex_to_hwpeqn").joinpath("schemas/editor-equation-v1.schema.json")
    with resource.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _validate_editor_schema(payload: Any) -> None:
    """Validate a v1 payload with the bundled JSON Schema validator.

    This adapter treats schema validation as a required safety boundary.  A
    missing optional dependency must fail loudly instead of silently accepting
    an unverified editor payload.
    """

    if not isinstance(payload, dict):
        raise _error("editor_json_schema", "editor_json must be an object")

    try:
        import jsonschema  # type: ignore
    except ImportError as exc:
        raise _error("jsonschema_unavailable", "jsonschema is required to validate editor_json") from exc

    try:
        jsonschema.Draft202012Validator(_schema()).validate(payload)
    except jsonschema.ValidationError as exc:
        path = list(exc.absolute_path)
        if path[:1] == ["requirements"] and "feature" in path:
            raise _error("unknown_requirement", exc.message) from exc
        if "type" in path and "tree" in path:
            raise _error("unknown_node_type", exc.message) from exc
        raise _error("editor_json_schema", exc.message) from exc


def _walk_nodes(node: Any, path: str = "/tree") -> Iterable[tuple[str, dict[str, Any]]]:
    if not isinstance(node, dict):
        raise _error("unknown_node_type", f"node at {path} must be an object")
    node_type = node.get("type")
    if node_type not in _NODE_TYPES:
        raise _error("unknown_node_type", f"unsupported node type at {path}: {node_type!r}")
    yield path, node
    for key in ("children", "cells", "rows"):
        children = node.get(key, [])
        if not isinstance(children, list):
            raise _error("editor_json_schema", f"{path}/{key} must be an array")
        for index, child in enumerate(children):
            yield from _walk_nodes(child, f"{path}/{key}/{index}")


def _json_pointer(root: Any, path: str) -> Any:
    """Resolve a local JSON pointer and reject stale/foreign requirement paths."""

    if not isinstance(path, str) or not path.startswith("/"):
        raise _error("requirement_path", f"requirement path must be a local JSON pointer: {path!r}")
    value = root
    for raw_token in path[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            raise _error("requirement_path", f"requirement path does not resolve: {path}")
    return value


def _receipt_requirements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    requirements = payload.get("requirements")
    if not isinstance(requirements, list):
        raise _error("editor_json_schema", "requirements must be an array")

    result: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise _error("unknown_requirement", "each requirement must be an object")
        feature = requirement.get("feature")
        if feature not in _KNOWN_REQUIREMENTS:
            raise _error("unknown_requirement", f"unsupported requirement feature: {feature!r}")
        if feature != "fallback_review":
            path = requirement.get("path")
            if not isinstance(path, str):
                raise _error("requirement_path", f"{feature} requirement has no path")
            _json_pointer(payload, path)
        elif "path" in requirement:
            _json_pointer(payload, requirement["path"])

        item = copy.deepcopy(requirement)
        item["status"] = "accepted_pending_review"
        if feature == "tree_v1":
            # The engine's tree is a faithful handoff for the vocabulary it
            # exposes, but the HWP writer has not claimed full AST semantics.
            item["support"] = "representable_subset"
            item["full_ast_support"] = False
        elif feature in _LAYOUT_REQUIREMENTS:
            item["support"] = "layout_requirement_pending_review"
        result.append(item)
    return result


def _approximations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    diagnostics = payload.get("diagnostics", [])
    if not isinstance(diagnostics, list):
        raise _error("editor_json_schema", "diagnostics must be an array")
    approximations: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        if diagnostic.get("severity") != "warning":
            continue
        item = copy.deepcopy(diagnostic)
        item["status"] = "accepted_pending_review"
        approximations.append(item)
    return approximations


def _check_payload(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Walk before schema validation so an unknown node receives the actionable
    # stable code even though the schema reports it as a generic oneOf error.
    tree = payload.get("tree")
    if isinstance(tree, dict):
        list(_walk_nodes(tree))
    _validate_editor_schema(payload)
    if payload.get("ok") is not True or payload.get("status") != "parsed":
        raise _error("editor_json_error", "equation payload is not a successful parse")
    requirements = _receipt_requirements(payload)
    approximations = _approximations(payload)
    fallback = payload.get("fallback")
    if not isinstance(fallback, dict) or not isinstance(fallback.get("hwpeqn"), str):
        raise _error("editor_json_schema", "parsed payload must contain fallback.hwpeqn")
    if fallback.get("rendering_verified") is not False:
        raise _error("rendering_state", "equation fallback cannot claim rendering verification")
    return requirements, approximations


def _canonical_editor_payload(source: str) -> dict[str, Any]:
    try:
        return convert_for_editor(source)
    except Exception as exc:  # pragma: no cover - defensive engine boundary
        raise _error("latex_conversion", str(exc)) from exc


def _receipt_from_payload(
    payload: dict[str, Any],
    *,
    equation_id: str,
    source_kind: str,
    display: bool,
    check_canonical: bool,
) -> dict[str, Any]:
    requirements, approximations = _check_payload(payload)
    source = payload.get("source", {})
    source_text = source.get("text") if isinstance(source, dict) else None
    if not isinstance(source_text, str):
        raise _error("editor_json_schema", "payload source text is missing")
    if check_canonical:
        fallback = payload.get("fallback")
        if isinstance(fallback, dict) and fallback.get("latex") != source_text:
            raise _error("editor_json_source_mismatch", "fallback latex does not match canonical source text")
        canonical = _canonical_editor_payload(source_text)
        if canonical.get("source") != payload.get("source"):
            raise _error("editor_json_source_mismatch", "source text is not canonical")
        if canonical != payload:
            raise _error("editor_json_not_canonical", "editor_json differs from the engine's canonical payload")
    script = payload["fallback"]["hwpeqn"]
    return {
        "id": equation_id,
        "display": display,
        "source_kind": source_kind,
        "payload": copy.deepcopy(payload),
        "selected_script": script,
        "requirements": requirements,
        "approximations": approximations,
        "fallback_used": True,
        "rendering_verified": False,
        "review_required": True,
    }


def compile_equation(block: dict[str, Any], equation_id: str) -> dict[str, Any]:
    """Compile one equation block into an auditable, native-layer receipt.

    Exactly one of ``latex``, ``editor_json`` and ``hwpeqn`` is accepted.  The
    first two routes always retain the complete editor payload; the raw HWP
    route deliberately carries only the native script and is never presented
    as a parsed tree.
    """

    if not isinstance(block, dict):
        raise _error("equation_block", "equation block must be an object")
    if block.get("type") != "equation":
        raise _error("equation_block", "block type must be equation")
    if not isinstance(equation_id, str) or not equation_id:
        raise _error("equation_id", "equation_id must be a non-empty string")
    display = block.get("display", False)
    if type(display) is not bool:
        raise _error("display", "display must be boolean")
    present = [key for key in _SOURCE_KEYS if key in block]
    if len(present) != 1:
        raise _error("source_kind", "exactly one of latex, editor_json, or hwpeqn is required")
    source_kind = present[0]
    source_value = block[source_kind]

    if source_kind == "latex":
        if not isinstance(source_value, str):
            raise _error("latex_input", "latex must be a string")
        payload = _canonical_editor_payload(source_value)
        if payload.get("ok") is not True:
            diagnostics = payload.get("diagnostics") or []
            code = diagnostics[0].get("code") if diagnostics and isinstance(diagnostics[0], dict) else "latex_conversion"
            raise EquationError(code or "latex_conversion", "LaTeX could not be converted",
                                diagnostics=diagnostics)
        # Public engine output is canonical by construction, but checking it
        # through the same path keeps all source kinds on one receipt contract.
        return _receipt_from_payload(
            payload,
            equation_id=equation_id,
            source_kind=source_kind,
            display=display,
            check_canonical=False,
        )

    if source_kind == "editor_json":
        if not isinstance(source_value, dict):
            raise _error("editor_json_schema", "editor_json must be an object")
        payload = copy.deepcopy(source_value)
        return _receipt_from_payload(
            payload,
            equation_id=equation_id,
            source_kind=source_kind,
            display=display,
            check_canonical=True,
        )

    if not isinstance(source_value, str) or not source_value:
        raise _error("hwpeqn_input", "hwpeqn must be a non-empty string")
    return {
        "id": equation_id,
        "display": display,
        "source_kind": "hwpeqn",
        "payload": None,
        "native_script": source_value,
        "selected_script": source_value,
        "requirements": [],
        "approximations": [],
        "fallback_used": False,
        "rendering_verified": False,
        "review_required": True,
    }


def _segments_for_text(text: str, equation_id_start: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    try:
        document = convert_document_for_editor(text)
    except Exception as exc:  # pragma: no cover - defensive engine boundary
        raise _error("document_conversion", str(exc)) from exc
    if document.get("ok") is not True:
        diagnostics = document.get("diagnostics") or []
        code = diagnostics[0].get("code") if diagnostics and isinstance(diagnostics[0], dict) else "document_conversion"
        raise _error(code or "document_conversion", "explicit math delimiters could not be converted")

    segments: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    next_id = equation_id_start
    for segment in document.get("segments", []):
        if segment.get("kind") == "text":
            segments.append({
                "kind": "text",
                "source": segment["source"],
                "start": segment["start"],
                "end": segment["end"],
                "display": False,
            })
            continue
        if segment.get("kind") != "math" or not isinstance(segment.get("equation"), dict):
            raise _error("document_segment", "document contains an unsupported segment")
        equation_id = f"eq-{next_id:04d}"
        next_id += 1
        display = segment.get("display", False)
        receipt = _receipt_from_payload(
            copy.deepcopy(segment["equation"]),
            equation_id=equation_id,
            source_kind="latex",
            display=bool(display),
            check_canonical=True,
        )
        receipts.append(receipt)
        segments.append({
            "kind": "math",
            "source": segment["source"],
            "start": segment["start"],
            "end": segment["end"],
            "display": bool(display),
            "type": "equation",
            "latex": segment["source"],
            "_equation_id": equation_id,
            "_hwpeqn": receipt["selected_script"],
            "payload": copy.deepcopy(segment["equation"]),
        })
    return segments, receipts, next_id


def compile_request(request: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize equation blocks in a request without mutating caller data.

    Traversal order is the request's natural preorder: item blocks, paragraph
    runs, choice options, then nested box blocks.  Text is split only when it
    contains explicit ``$...$``, ``$$...$$``, ``\\(...\\)`` or ``\\[...\\]``
    delimiters; plain text is never guessed to be mathematical notation.
    """

    if not isinstance(request, dict):
        raise _error("request", "request must be an object")
    normalized = copy.deepcopy(request)
    receipts: list[dict[str, Any]] = []
    context = "question"

    def next_equation_id() -> str:
        return f"eq-{len(receipts) + 1:04d}"

    def visit(block: Any) -> Any:
        if not isinstance(block, dict):
            return block
        block_type = block.get("type")
        if block_type == "equation":
            equation_id = next_equation_id()
            receipt = compile_equation(block, equation_id)
            receipt["context"] = context
            receipts.append(receipt)
            block["_equation_id"] = equation_id
            block["_hwpeqn"] = receipt["selected_script"]
            block["display"] = receipt["display"]
            return block
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise _error("text_block", "text block must contain a string")
            if any(marker in text for marker in ("$", r"\(", r"\[")):
                segments, segment_receipts, _ = _segments_for_text(text, len(receipts) + 1)
                for receipt in segment_receipts:
                    receipt["context"] = context
                receipts.extend(segment_receipts)
                block["_math_segments"] = segments
            return block
        if block_type == "paragraph":
            runs = block.get("runs")
            if not isinstance(runs, list):
                raise _error("paragraph_block", "paragraph runs must be an array")
            block["runs"] = [visit(run) for run in runs]
            return block
        if block_type == "choices":
            options = block.get("options")
            if not isinstance(options, list):
                raise _error("choices_block", "choices options must be an array")
            normalized_options: list[Any] = []
            for option in options:
                if not isinstance(option, list):
                    raise _error("choices_block", "each choices option must be an array")
                normalized_options.append([visit(run) for run in option])
            block["options"] = normalized_options
            return block
        if block_type in {"box", "subquestion"}:
            blocks = block.get("blocks")
            if not isinstance(blocks, list):
                raise _error("box_block", "box blocks must be an array")
            block["blocks"] = [visit(child) for child in blocks]
            return block

        # Other layout/content blocks (image, workspace, etc.) belong to the
        # document layer.  Leave them intact while still refusing unknown
        # equation-like objects above.
        return block

    items = normalized.get("items", [])
    if not isinstance(items, list):
        raise _error("request_items", "request items must be an array")
    for item in items:
        if not isinstance(item, dict):
            raise _error("item", "each request item must be an object")
        blocks = item.get("blocks", [])
        if not isinstance(blocks, list):
            raise _error("item_blocks", "item blocks must be an array")
        item["blocks"] = [visit(block) for block in blocks]
    if normalized.get("answer_sheet"):
        context = "answer"
        answer_layout = normalized.get("answer_layout", {})
        answer_layout["preamble"] = [visit(b) for b in answer_layout.get("preamble", [])]
        for item in items:
            item["answer_blocks"] = [visit(b) for b in item.get("answer_blocks", [{"type": "text", "text": item.get("answer", "")}])]
        answer_layout["postamble"] = [visit(b) for b in answer_layout.get("postamble", [])]
    return normalized, receipts


__all__ = ["EquationError", "compile_equation", "compile_request"]
