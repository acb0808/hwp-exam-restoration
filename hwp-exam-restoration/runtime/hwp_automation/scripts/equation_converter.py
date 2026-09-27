from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ConversionResult:
    latex: str
    ok: bool
    hwpeqn: str | None = None
    error: str | None = None


class LatexConversionError(RuntimeError):
    pass


# Global process-level cache for formula conversions
_GLOBAL_CONVERSION_CACHE: dict[str, ConversionResult] = {}


def _fast_path_convert(latex: str) -> ConversionResult | None:
    """
    Instantly convert basic LaTeX identifiers and numbers without spawning Node.js.
    For single alphanumeric characters (e.g. 'x', 'y', 'r', '1', '2'),
    LaTeX and HWP equation representations are identical.
    """
    stripped = latex.strip()
    if re.fullmatch(r"[A-Za-z0-9]", stripped):
        return ConversionResult(latex=latex, ok=True, hwpeqn=stripped)
    return None


class EquationConverter:
    """
    Bridge connecting LaTeX formulas to HWP Equation (hwpeqn) strings
    using Node.js and the hwp-eqn-ts library.
    Includes in-memory caching and fast-path heuristics for maximum throughput.
    """

    def __init__(
        self,
        bridge_script: Path | str | None = None,
        node_executable: str = "node",
        use_cache: bool = True,
    ) -> None:
        if bridge_script is None:
            script_dir = Path(__file__).resolve().parent
            candidates = [
                script_dir / "bridge" / "latex_to_hwpeqn.js",
                script_dir.parent / "bridge" / "latex_to_hwpeqn.js",
            ]
            self.bridge_script = next((c for c in candidates if c.is_file()), candidates[0])
        else:
            self.bridge_script = Path(bridge_script).resolve()

        self.node_executable = node_executable
        self.use_cache = use_cache
        self._cache = _GLOBAL_CONVERSION_CACHE

    def clear_cache(self) -> None:
        """Clear the in-memory conversion cache."""
        self._cache.clear()

    def _check_environment(self) -> None:
        if shutil.which(self.node_executable) is None:
            raise LatexConversionError(
                f"Node.js executable '{self.node_executable}' not found in PATH."
            )
        if not self.bridge_script.is_file():
            raise LatexConversionError(
                f"Bridge script not found at '{self.bridge_script}'."
            )

    def convert_many(self, formulas: Sequence[str]) -> list[ConversionResult]:
        if not formulas:
            return []

        results_map: dict[str, ConversionResult] = {}
        uncached_formulas: list[str] = []

        for f in formulas:
            if self.use_cache and f in self._cache:
                results_map[f] = self._cache[f]
                continue

            fast_res = _fast_path_convert(f)
            if fast_res is not None:
                if self.use_cache:
                    self._cache[f] = fast_res
                results_map[f] = fast_res
                continue

            if f not in results_map and f not in uncached_formulas:
                uncached_formulas.append(f)

        if uncached_formulas:
            self._check_environment()
            payload = json.dumps({"formulas": uncached_formulas}, ensure_ascii=False)

            try:
                completed = subprocess.run(
                    [self.node_executable, str(self.bridge_script)],
                    input=payload,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                    cwd=str(self.bridge_script.parent),
                    timeout=15.0,
                )
            except subprocess.TimeoutExpired as exc:
                raise LatexConversionError("LaTeX conversion timed out after 15 seconds") from exc
            except OSError as exc:
                raise LatexConversionError(f"Failed to execute Node.js converter: {exc}") from exc

            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or "Unknown error"
                raise LatexConversionError(f"Node.js converter exited with code {completed.returncode}: {detail}")

            try:
                response = json.loads(completed.stdout)
                raw_results = response.get("results", [])
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise LatexConversionError(f"Invalid JSON from bridge: {completed.stdout[:300]}") from exc

            for latex, item in zip(uncached_formulas, raw_results, strict=True):
                res = ConversionResult(
                    latex=latex,
                    ok=bool(item.get("ok")),
                    hwpeqn=item.get("hwpeqn"),
                    error=item.get("error"),
                )
                if self.use_cache:
                    self._cache[latex] = res
                results_map[latex] = res

        return [results_map[f] for f in formulas]

    def convert(self, formula: str) -> ConversionResult:
        if self.use_cache and formula in self._cache:
            return self._cache[formula]

        fast_res = _fast_path_convert(formula)
        if fast_res is not None:
            if self.use_cache:
                self._cache[formula] = fast_res
            return fast_res

        results = self.convert_many([formula])
        if not results:
            return ConversionResult(latex=formula, ok=False, error="No result returned")
        return results[0]
