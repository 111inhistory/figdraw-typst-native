from __future__ import annotations

import codecs
import functools
import json
import math
import os
import re
import warnings
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from string import Template
from typing import Literal, Any

import typst
import matplotlib as mpl
from matplotlib import cbook
from matplotlib.backend_bases import (
    FigureCanvasBase,
    GraphicsContextBase,
    _Backend,
    RendererBase,
)
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib import transforms as mtransforms
from matplotlib.path import Path as MplPath
from PIL import Image

from .typst_core import (  # noqa: F401
    Command,
    Content,
    Length,
    Pos,
    Raw,
    Rgb,
    TypstType,
    normalize_string,
)


try:  # Optional native Rust core: microsecond in-memory measurement + export.
    import mpl_typst_core
except ImportError:  # pragma: no cover - the legacy `query` engine still works.
    mpl_typst_core = None


def _is_path_target(filename) -> bool:
    return isinstance(filename, str | os.PathLike)


_legacy_warned = False


def _warn_legacy_engine() -> None:
    """Warn once when the deprecated anchor-probe engine is actually used."""
    global _legacy_warned
    if _legacy_warned:
        return
    _legacy_warned = True
    warnings.warn(
        "the 'query' engine of figdraw-typst-native is deprecated and will be "
        "removed in a future release; install `mpl-typst-core` (extra `core`) "
        "or set mpl.rcParams['typst.engine'] = 'core'",
        DeprecationWarning,
        stacklevel=3,
    )


PACKAGE_DIR = Path(__file__).resolve().parent
DOCUMENT_TEMPLATE = PACKAGE_DIR / "templates" / "native_document.typ"
TEXT_MEASURE_TEMPLATE = PACKAGE_DIR / "templates" / "text_measure.typ"
PAGE_PADDING_PT = 12.0


@dataclass(frozen=True)
class TypstNativeConfig:
    document_template: str | os.PathLike = DOCUMENT_TEMPLATE
    text_measure_template: str | os.PathLike = TEXT_MEASURE_TEMPLATE
    font: str | Sequence[str] | Raw = ("Times New Roman", "SimSun")
    text_top_edge: str = "cap-height"
    page_padding: float = PAGE_PADDING_PT
    par_leading: str = "0pt"
    par_spacing: str = "0pt"
    preamble: str = ""
    measure_preamble: str = ""
    engine: str = "auto"


#: Extra keyword arguments Matplotlib passes to every ``print_*`` method.
_MPL_NOISE_KWARGS = (
    "bbox_inches_restore",
    "edgecolor",
    "facecolor",
    "metadata",
    "orientation",
    "pil_kwargs",
)

_RC_DEFAULTS: dict[str, Any] = {
    "typst.document_template": str(DOCUMENT_TEMPLATE),
    "typst.text_measure_template": str(TEXT_MEASURE_TEMPLATE),
    "typst.font": ("Times New Roman", "SimSun"),
    "typst.text_top_edge": "cap-height",
    "typst.page_padding": PAGE_PADDING_PT,
    "typst.par_leading": "0pt",
    "typst.par_spacing": "0pt",
    "typst.preamble": "",
    "typst.measure_preamble": "",
    "typst.engine": "auto",
    "typst.root": None,
    "typst.font_paths": None,
    "typst.ignore_system_fonts": None,
    "typst.sys_inputs": None,
    "typst.pdf_standards": None,
    "typst.package_path": None,
}
_CONFIG_FIELDS = {
    "document_template": "typst.document_template",
    "text_measure_template": "typst.text_measure_template",
    "font": "typst.font",
    "text_top_edge": "typst.text_top_edge",
    "page_padding": "typst.page_padding",
    "par_leading": "typst.par_leading",
    "par_spacing": "typst.par_spacing",
    "preamble": "typst.preamble",
    "measure_preamble": "typst.measure_preamble",
    "engine": "typst.engine",
}


def register_rcparams() -> None:
    for key, default in _RC_DEFAULTS.items():
        mpl.rcParams.validate.setdefault(key, lambda value: value)
        mpl.rcParamsDefault.setdefault(key, default)
        mpl.rcParamsOrig.setdefault(key, default)
        if key not in mpl.rcParams:
            mpl.rcParams[key] = default


register_rcparams()


#: ``typst_<field>`` -> ``<field>``, derived from :data:`_CONFIG_FIELDS`.
_KWARG_FIELDS = {f"typst_{field}": field for field in _CONFIG_FIELDS}


def configure(**kwargs) -> None:
    """Set one or more Typst options as rcParams.

    Accepts either the short field name (``engine="core"``) or the full
    rcParam name (``**{"typst.engine": "core"}``).
    """
    register_rcparams()
    for key, value in kwargs.items():
        rc_key = _CONFIG_FIELDS.get(key, key)
        if not rc_key.startswith("typst."):
            raise KeyError(f"unsupported Typst native config key: {key}")
        mpl.rcParams[rc_key] = value


def get_config() -> TypstNativeConfig:
    register_rcparams()
    return TypstNativeConfig(
        document_template=mpl.rcParams["typst.document_template"],
        text_measure_template=mpl.rcParams["typst.text_measure_template"],
        font=mpl.rcParams["typst.font"],
        text_top_edge=mpl.rcParams["typst.text_top_edge"],
        page_padding=float(mpl.rcParams["typst.page_padding"]),
        par_leading=mpl.rcParams["typst.par_leading"],
        par_spacing=mpl.rcParams["typst.par_spacing"],
        preamble=mpl.rcParams["typst.preamble"],
        measure_preamble=mpl.rcParams["typst.measure_preamble"],
        engine=mpl.rcParams["typst.engine"],
    )


def reset_config() -> None:
    """Restore every Typst option to its default."""
    register_rcparams()
    for key, value in _RC_DEFAULTS.items():
        mpl.rcParams[key] = value


#: Typst lengths are the only edge values that may be written unquoted.
_LENGTH_CODE = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:pt|mm|cm|in|em|rem|%)$")


def _edge_code(value: str | Raw) -> str:
    """Return Typst code for a `text` edge (`top-edge` / `bottom-edge`).

    Lengths such as `1em` are emitted verbatim; metric names such as
    `cap-height` are quoted, because an unquoted metric name is parsed as a
    variable reference and fails with "unknown variable".
    """
    if isinstance(value, Raw):
        return value.to_code()
    text = str(value).strip()
    if _LENGTH_CODE.match(text):
        return text
    return f'"{normalize_string(text)}"'


def _font_code(font: str | Sequence[str] | Raw) -> str:
    if isinstance(font, Raw):
        return font.to_code()
    if isinstance(font, str):
        return f'"{normalize_string(font)}"'
    return "(" + ", ".join(f'"{normalize_string(item)}"' for item in font) + ")"


def _strip_mathdefault(s: str) -> str:
    while r"\mathdefault{" in s:
        idx = s.find(r"\mathdefault{")
        start = idx + len(r"\mathdefault{")
        depth = 1
        end = start
        while end < len(s) and depth > 0:
            if s[end] == "{":
                depth += 1
            elif s[end] == "}":
                depth -= 1
            end += 1
        if depth == 0:
            content = s[start : end - 1]
            s = s[:idx] + content + s[end:]
        else:
            break
    return s


def _text_body(text: str, ismath) -> Content:
    text_content = text.replace(r"\$", "$")
    if ismath not in (True, "TeX"):
        return Content(text_content)

    dollar_indices = [
        match.end() - 1 for match in re.finditer(r"(?<!\\)(?:\\\\)*\$", text)
    ]
    if len(dollar_indices) == 0:
        if ismath is not True:
            return Content(text_content)
        fragment = _strip_mathdefault(text.strip())
        return Content(Command("mi", Raw(f'"{normalize_string(fragment)}"')))
    if len(dollar_indices) % 2 != 0:
        return Content(text_content)

    parts: list[TypstType] = []
    cursor = 0
    for start, end in zip(dollar_indices[0::2], dollar_indices[1::2], strict=True):
        if start > cursor:
            parts.append(text[cursor:start].replace(r"\$", "$"))
        fragment = _strip_mathdefault(text[start + 1 : end].strip())
        parts.append(Command("mi", Raw(f'"{normalize_string(fragment)}"')))
        cursor = end + 1
    if cursor < len(text):
        parts.append(text[cursor:].replace(r"\$", "$"))
    return Content(*parts)


def _config_from_kwargs(kwargs: dict[str, Any]) -> TypstNativeConfig:
    config = kwargs.pop("typst_config", None) or get_config()
    updates = {
        field: kwargs.pop(key) for key, field in _KWARG_FIELDS.items() if key in kwargs
    }
    return replace(config, **updates) if updates else config


def _first(*values):
    """First non-``None`` value, mirroring `savefig`'s explicit-wins-over-rcParam."""
    return next((value for value in values if value is not None), None)


def _reject_unknown_kwargs(kwargs: dict[str, Any]) -> None:
    """Drop Matplotlib's noise kwargs and reject anything else."""
    for name in _MPL_NOISE_KWARGS:
        kwargs.pop(name, None)
    if kwargs:
        names = ", ".join(sorted(kwargs))
        raise TypeError(f"unsupported savefig argument(s) for Typst backend: {names}")


class TypstTextMeasurer:
    """Manages text measurement with a bounded in-memory LRU cache."""

    def __init__(self, config: TypstNativeConfig, maxsize: int = 4096):
        self.config = config
        self.maxsize = maxsize
        self._cache: dict[
            tuple[str, float, bool | str, tuple[str, ...]],
            tuple[float, float, float],
        ] = {}

    def measure(
        self, text: str, size: float, ismath: bool | str, font_names: tuple[str, ...]
    ) -> tuple[float, float, float]:
        key = (text, size, ismath, font_names)
        cached = self._cache.pop(key, None)
        if cached is not None:  # refresh recency
            self._cache[key] = cached
            return cached

        metrics = self._measure_uncached(text, size, ismath, font_names)
        if len(self._cache) >= self.maxsize:
            del self._cache[next(iter(self._cache))]
        self._cache[key] = metrics
        return metrics

    def _measure_uncached(
        self, text: str, size: float, ismath: bool | str, font_names: tuple[str, ...]
    ) -> tuple[float, float, float]:
        engine = self.config.engine
        if engine in ("auto", "core"):
            metrics = _measure_with_core(self.config, text, size, ismath)
            if metrics is not None:
                return metrics
            if engine == "core":
                raise RuntimeError(_CORE_MISSING)

        _warn_legacy_engine()
        text_body = _text_body(text, ismath).to_code(in_content=True)
        source = Template(
            Path(self.config.text_measure_template).read_text(encoding="utf-8")
        ).substitute(
            font_size=Length(size).to_code(),
            text_font=_font_code(self.config.font),
            text_top_edge=_edge_code(self.config.text_top_edge),
            par_leading=self.config.par_leading,
            par_spacing=self.config.par_spacing,
            measure_preamble=self.config.measure_preamble,
            text_body=text_body,
        )
        raw = typst.query(
            source.encode("utf-8"),
            "<measure>",
            field="value",
            one=True,
            root=str(PACKAGE_DIR),
        )
        data = json.loads(raw)
        width_pt = float(data["p2"]["x"]) - float(data["p0"]["x"])
        height_pt = float(data["p3"]["y"]) - float(data["p0"]["y"])
        descent_pt = float(data["p3"]["y"]) - float(data["p1"]["y"])
        return (width_pt, height_pt, descent_pt)


@functools.lru_cache(maxsize=8)
def _get_text_measurer(config: TypstNativeConfig) -> TypstTextMeasurer:
    """Measurer used by the deprecated `query` engine."""
    return TypstTextMeasurer(config)


#: Raised when the core engine is requested but not installed.
_CORE_MISSING = (
    "typst.engine='core' requires the optional `mpl-typst-core` package; "
    "install it or use engine 'auto'/'query'"
)


def _font_families(font: str | Sequence[str] | Raw) -> list[str] | None:
    """Font fallback list in the shape expected by the native core."""
    if isinstance(font, Raw):
        return None
    if isinstance(font, str):
        return [font]
    return [str(name) for name in font]


_core_measurer: Any = None
_core_measurer_key: tuple[str, ...] | None = None


def _get_core_measurer() -> Any:
    """Lazily build the native measurer, rebuilding it when fonts change."""
    global _core_measurer, _core_measurer_key

    if mpl_typst_core is None:
        return None

    font_paths = mpl.rcParams["typst.font_paths"]
    ignore_system_fonts = mpl.rcParams["typst.ignore_system_fonts"]
    key = (str(font_paths), str(bool(ignore_system_fonts)))
    if _core_measurer is None or _core_measurer_key != key:
        _core_measurer = mpl_typst_core.TypstCoreMeasurer(
            extra_font_paths=[str(path) for path in font_paths] if font_paths else [],
            include_system_fonts=not ignore_system_fonts,
        )
        _core_measurer_key = key
    return _core_measurer


def _measure_with_core(
    config: TypstNativeConfig, text: str, size: float, ismath: bool | str
) -> tuple[float, float, float] | None:
    """Measure through the native core, or `None` when it is unavailable."""
    measurer = _get_core_measurer()
    if measurer is None:
        return None
    return measurer.measure_text(
        text,
        font_family=_font_families(config.font),
        font_size_pt=size,
        is_math=ismath,
        top_edge=config.text_top_edge,
        bottom_edge="baseline",
        par_leading_em=0.0,
        par_spacing_em=0.0,
    )


def clear_cache() -> None:
    """Clear the in-memory text measurement cache of both engines."""
    _get_text_measurer.cache_clear()
    global _core_measurer, _core_measurer_key
    _core_measurer = None
    _core_measurer_key = None


def save_cache(file_path: str | os.PathLike) -> None:
    """Persist the `query` engine's measurement cache to ``file_path``.

    Deprecated together with the `query` engine; the `core` engine keeps its
    measurements only in memory.
    """
    path = Path(file_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    measurer = _get_text_measurer(get_config())
    data = {
        json.dumps(list(k), ensure_ascii=False): list(v)
        for k, v in measurer._cache.items()
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache(file_path: str | os.PathLike) -> int:
    """Load a `query` engine measurement cache from ``file_path``.

    Deprecated together with the `query` engine.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Cache file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    measurer = _get_text_measurer(get_config())
    count = 0
    if isinstance(data, dict):
        for k_str, v in data.items():
            if isinstance(v, list) and len(v) == 3:
                raw_k = json.loads(k_str)
                k_tuple = (
                    str(raw_k[0]),
                    float(raw_k[1]),
                    raw_k[2],
                    tuple(raw_k[3]),
                )
                measurer._cache[k_tuple] = (float(v[0]), float(v[1]), float(v[2]))
                count += 1
    return count



MARKER_KEY_TOL = 1e-7
MARKER_KEY_SCALE = round(1 / MARKER_KEY_TOL)
MarkerKey = tuple[tuple[int, ...] | None, tuple[int, ...], tuple[tuple[str, str], ...]]


class RendererTypst(RendererBase):
    def __init__(
        self,
        figure,
        output_dir: Path | None = None,
        image_prefix: str = "figure",
        config: TypstNativeConfig | None = None,
    ):
        super().__init__()
        self.figure = figure
        self.config = config or get_config()
        self.dpi = figure.dpi
        self.width_pt = figure.get_figwidth() * 72.0
        self.height_pt = figure.get_figheight() * 72.0
        self.marker: dict[MarkerKey, str] = {}
        self.definitions: list[str] = []
        self.commands: list[Command] = []
        self.output_dir = output_dir
        self.image_prefix = image_prefix
        self.image_counter = 0
        self._agg = RendererAgg(
            int(math.ceil(figure.get_figwidth() * self.dpi)),
            int(math.ceil(figure.get_figheight() * self.dpi)),
            self.dpi,
        )

    def points_to_pixels(self, points):
        return points * self.dpi / 72.0

    def flipy(self):
        return False

    def get_canvas_width_height(self):
        return (self.width_pt * self.dpi / 72.0, self.height_pt * self.dpi / 72.0)

    def get_text_width_height_descent(self, s, prop, ismath):
        """Measure text size using Typst with bounded in-memory LRU cache."""
        if "\n" in str(s):
            return self._agg.get_text_width_height_descent(s, prop, ismath)

        font_names = tuple(prop.get_family())
        size = float(prop.get_size_in_points())
        measurer = _get_text_measurer(self.config)

        try:
            width_pt, height_pt, descent_pt = measurer.measure(
                str(s), size, ismath, font_names
            )
            return (
                self.points_to_pixels(width_pt),
                self.points_to_pixels(height_pt),
                self.points_to_pixels(descent_pt),
            )
        except Exception:
            return self._agg.get_text_width_height_descent(s, prop, ismath)

    def _clip_bounds(self, gc):
        bbox = gc.get_clip_rectangle()
        if bbox is None:
            return None
        (x0, y0), (x1, y1) = bbox.get_points()
        return x0, y0, x1, y1

    def _draw_path(
        self,
        gc: GraphicsContextBase,
        path: MplPath,
        transform,
        rgbFace=None,
        pos_transform=None,
        clip=True,
    ):
        if pos_transform is None:
            pos_transform = lambda x, y: (x, self.height_pt - y)  # noqa: E731
        path_command: list[Command] = []
        style = self._path_style(gc, rgbFace)
        cur_curve = Command("curve")
        for key, value in style.items():
            cur_curve.add_arg(value, name=key)

        def flush_curve():
            nonlocal cur_curve
            if cur_curve is not None and len(cur_curve.args) > 0:
                path_command.append(cur_curve)
            cur_curve = Command("curve")
            for key, value in style.items():
                cur_curve.add_arg(value, name=key)

        # Simplification follows matplotlib's `path.simplify` rcParam, as it
        # does in the Agg and PGF backends.
        for vertices, code in path.iter_segments(
            transform,
            clip=self._clip_bounds(gc) if clip else None,
        ):
            vertices: list[float] = [x / self.dpi * 72.0 for x in vertices]
            match code:
                case MplPath.MOVETO:
                    cur_curve.add_arg(
                        Command(
                            "curve.move",
                            Pos(*vertices[:2], transform=pos_transform),
                        )
                    )
                case MplPath.LINETO:
                    cur_curve.add_arg(
                        Command(
                            "curve.line",
                            Pos(*vertices[:2], transform=pos_transform),
                        )
                    )
                case MplPath.CURVE3:
                    cur_curve.add_arg(
                        Command(
                            "curve.quad",
                            Pos(*vertices[:2], transform=pos_transform),
                            Pos(*vertices[2:4], transform=pos_transform),
                        )
                    )
                case MplPath.CURVE4:
                    cur_curve.add_arg(
                        Command(
                            "curve.cubic",
                            Pos(*vertices[:2], transform=pos_transform),
                            Pos(*vertices[2:4], transform=pos_transform),
                            Pos(*vertices[4:6], transform=pos_transform),
                        )
                    )
                case MplPath.CLOSEPOLY:
                    cur_curve.add_arg(Command("curve.close"))
                    flush_curve()

        flush_curve()
        return path_command

    def draw_path(
        self, gc: GraphicsContextBase, path: MplPath, transform, rgbFace=None
    ):
        self.commands.extend(
            Command("place", Raw("left + top"), command)
            for command in self._draw_path(gc, path, transform, rgbFace)
        )

    def _path_style(self, gc, rgbFace=None) -> dict:
        """Translate Matplotlib path style into Typst keyword arguments."""
        capstyle_map = {
            "butt": "butt",
            "round": "round",
            "projecting": "square",
        }
        joinstyle_map = {
            "miter": "miter",
            "round": "round",
            "bevel": "bevel",
        }
        stroke: Raw | dict | None = None
        fill: Raw | Rgb | None = None
        style: dict[str, TypstType] = {}

        rgb = tuple(float(value) for value in gc.get_rgb())
        if len(rgb) < 3:
            raise ValueError("color must contain at least RGB channels")
        stroke_color = Rgb(*rgb[:4]) if len(rgb) >= 4 else Rgb(*rgb[:3])

        linewidth = gc.get_linewidth()
        if linewidth > 0:
            stroke = {"paint": stroke_color, "thickness": Length(linewidth)}
            offset, dashes = gc.get_dashes()
            if dashes:
                stroke["dash"] = {
                    "array": [Length(value) for value in dashes],
                    "phase": Length(offset),
                }
            capstyle = capstyle_map.get(
                getattr(gc.get_capstyle(), "name", str(gc.get_capstyle()))
            )
            if capstyle:
                stroke["cap"] = Raw(f'"{capstyle}"')
            joinstyle = joinstyle_map.get(
                getattr(gc.get_joinstyle(), "name", str(gc.get_joinstyle()))
            )
            if joinstyle:
                stroke["join"] = Raw(f'"{joinstyle}"')

        if rgbFace is not None:
            rgba = tuple(float(value) for value in rgbFace)
            if len(rgba) < 3:
                raise ValueError("fill color must contain at least RGB channels")
            fill = Rgb(*rgba[:4]) if len(rgba) >= 4 else Rgb(*rgba[:3])

        if stroke:
            style["stroke"] = stroke
        if fill:
            style["fill"] = fill
        return style

    def _get_marker_key(self, gc, marker: MplPath, rgbFace=None) -> str:
        """Return the cached Typst variable name for the given marker path."""
        codes = (
            None if marker.codes is None else tuple(int(code) for code in marker.codes)  # type: ignore
        )
        vertices = tuple(
            round(float(value) * MARKER_KEY_SCALE)  # type:ignore
            for vertex in marker.vertices  # type: ignore
            for value in vertex  # type: ignore
        )
        formatter = Command("_")
        style = self._path_style(gc, rgbFace)
        marker_key = (
            codes,
            vertices,
            tuple((key, formatter.arg_fmt(value)) for key, value in style.items()),
        )
        marker_name = self.marker.get(marker_key)
        if marker_name is not None:
            return marker_name

        marker_name = f"__marker_{len(self.marker)}"
        marker_commands = self._draw_path(
            gc,
            marker,
            mtransforms.IdentityTransform(),
            rgbFace,
            pos_transform=lambda x, y: (x, -y),
            clip=False,
        )
        marker_body = "\n".join(
            command.to_code(indent_depth=1) for command in marker_commands
        )
        self.definitions.append(f"#let {marker_name} = {{\n{marker_body}\n}}")
        self.marker[marker_key] = marker_name
        return marker_name

    def draw_markers(
        self, gc, marker_path, marker_trans, path: MplPath, trans, rgbFace=None
    ):
        """Manually complete this function for Typst compile optimization"""
        marker = marker_path.transformed(marker_trans)
        marker_name = self._get_marker_key(gc, marker, rgbFace)

        for vertices, code in path.iter_segments(
            trans,
            simplify=False,
            clip=self._clip_bounds(gc),
        ):
            if not len(vertices):
                continue

            x = vertices[-2] / self.dpi * 72.0
            y = self.height_pt - vertices[-1] / self.dpi * 72.0
            self.commands.append(
                Command(
                    "place",
                    Raw("left + top"),
                    Raw(marker_name),
                    dx=Length(x),
                    dy=Length(y),
                )
            )

    def draw_text(self, gc, x, y, s, prop, angle, ismath=False, mtext=None):
        x_pt = x / self.dpi * 72.0
        y_pt = y / self.dpi * 72.0
        text_width, text_height, text_descent = self.get_text_width_height_descent(
            s, prop, ismath
        )
        text_width = text_width / self.dpi * 72.0
        text_height = text_height / self.dpi * 72.0
        text_descent = text_descent / self.dpi * 72.0
        baseline_y = self.height_pt - y_pt
        text_ascent = text_height - text_descent
        align_x = 0.0
        align_y = text_ascent

        rgb = tuple(float(value) for value in gc.get_rgb())
        if len(rgb) < 3:
            raise ValueError("text color must contain at least RGB channels")

        body = _text_body(str(s), ismath)
        text = Command(
            "text",
            body,
            size=Length(prop.get_size_in_points()),
            fill=Rgb(*rgb[:4]) if len(rgb) >= 4 else Rgb(*rgb[:3]),
        )

        # handle text alignment with anchor point if mtext is provided and rotation is needed
        if mtext and (
            (angle == 0 or mtext.get_rotation_mode() == "anchor")
            and mtext.get_verticalalignment() != "center_baseline"
        ):
            x_anchor, y_anchor = mtext.get_transform().transform(
                mtext.get_unitless_position()
            )
            x_pt = x_anchor / self.dpi * 72.0
            baseline_y = self.height_pt - y_anchor / self.dpi * 72.0
            align_x = {
                "left": 0.0,
                "center": text_width / 2,
                "right": text_width,
            }.get(mtext.get_horizontalalignment(), 0.0)
            align_y = {
                "top": 0.0,
                "center": text_height / 2,
                "bottom": text_height,
                "baseline": text_ascent,
            }.get(mtext.get_verticalalignment(), text_ascent)

        if angle:
            theta = math.radians(-angle)
            place_x = x_pt - (math.cos(theta) * align_x - math.sin(theta) * align_y)
            place_y = baseline_y - (
                math.sin(theta) * align_x + math.cos(theta) * align_y
            )
        else:
            place_x = x_pt - align_x
            place_y = baseline_y - align_y

        if angle:
            text = Command(
                "rotate",
                Raw(f"{-angle:.6f}deg"),
                text,
                origin=Raw("top + left"),
            )
        self.commands.append(
            Command(
                "place",
                Raw("left + top"),
                text,
                dx=Length(place_x),
                dy=Length(place_y),
            )
        )

    def draw_tex(self, gc, x, y, s, prop, angle, *, mtext=None):
        self.draw_text(gc, x, y, s, prop, angle, ismath="TeX", mtext=mtext)

    def draw_image(self, gc, x, y, im, transform=None):
        if self.output_dir is None:
            raise RuntimeError("image output requires RendererTypst(output_dir=...)")

        height_px, width_px = im.shape[:2]
        if height_px == 0 or width_px == 0:
            return

        filename = f"{self.image_prefix}-img{self.image_counter}.png"
        self.image_counter += 1
        image_path = self.output_dir / filename
        Image.fromarray(im).transpose(Image.Transpose.FLIP_TOP_BOTTOM).save(image_path)

        width_pt = width_px / self.dpi * 72.0
        height_pt = height_px / self.dpi * 72.0
        x_pt = x / self.dpi * 72.0
        y_top = self.height_pt - y / self.dpi * 72.0 - height_pt
        image = Command(
            "image",
            Raw(f'"{normalize_string(filename)}"'),
            width=Length(width_pt),
            height=Length(height_pt),
        )
        self.commands.append(
            Command(
                "place",
                Raw("left + top"),
                image,
                dx=Length(x_pt),
                dy=Length(y_top),
            )
        )

    def to_typst(self) -> str:
        """Return the source code of the Typst document."""
        with open(self.config.document_template, encoding="utf-8") as file:
            template = Template(file.read())
        return template.substitute(
            page_width=f"{self.width_pt + 2 * self.config.page_padding:.6f}pt",
            page_height=f"{self.height_pt + 2 * self.config.page_padding:.6f}pt",
            page_margin=f"{self.config.page_padding:.6f}pt",
            canvas_width=f"{self.width_pt:.6f}pt",
            canvas_height=f"{self.height_pt:.6f}pt",
            text_font=_font_code(self.config.font),
            text_top_edge=_edge_code(self.config.text_top_edge),
            par_leading=self.config.par_leading,
            par_spacing=self.config.par_spacing,
            preamble=self.config.preamble,
            definitions="\n".join(self.definitions),
            commands="\n".join(
                command.to_code(indent_depth=2, in_content=True)
                for command in self.commands
            ),
        )


class FigureCanvasTypstNative(FigureCanvasBase):
    filetypes = {
        "typ": "Typst native source",
        "pdf": "Typst compiled PDF",
        "svg": "Typst compiled SVG",
        "png": "Typst compiled PNG",
    }

    def get_default_filetype(self):
        return "pdf"

    def _render_typst(
        self,
        output_dir: Path | None = None,
        image_prefix: str = "figure",
        config: TypstNativeConfig | None = None,
    ) -> str:
        renderer = RendererTypst(
            self.figure,
            output_dir=output_dir,
            image_prefix=image_prefix,
            config=config,
        )
        self.figure.draw(renderer)
        return renderer.to_typst()

    def print_typ(self, filename, **kwargs):
        config = _config_from_kwargs(kwargs)
        _reject_unknown_kwargs(kwargs)

        if _is_path_target(filename):
            output_path = Path(filename).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            typst_source = self._render_typst(
                output_dir=output_path.parent,
                image_prefix=output_path.stem,
                config=config,
            )
            with cbook.open_file_cm(filename, "w", encoding="utf-8") as file:
                if not cbook.file_requires_unicode(file):
                    file = codecs.getwriter("utf-8")(file)
                file.write(typst_source)
            return

        typst_source = self._render_typst(config=config)
        if cbook.file_requires_unicode(filename):
            filename.write(typst_source)
        else:
            filename.write(typst_source.encode("utf-8"))

    def _print_compiled(
        self,
        filename,
        suffix: Literal["pdf", "svg", "png"],
        *,
        dpi: float | None = None,
        ppi: float | None = None,
        typst_root: str | os.PathLike | None = None,
        font_paths=None,
        typst_font_paths=None,
        ignore_system_fonts: bool | None = None,
        typst_ignore_system_fonts: bool | None = None,
        sys_inputs: dict[str, str] | None = None,
        typst_sys_inputs: dict[str, str] | None = None,
        pdf_standards=None,
        typst_pdf_standards=None,
        package_path: str | os.PathLike | None = None,
        typst_package_path: str | os.PathLike | None = None,
        **kwargs,
    ) -> None:
        config = _config_from_kwargs(kwargs)
        _reject_unknown_kwargs(kwargs)

        if _is_path_target(filename):
            output_path = Path(filename).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            typst_source = self._render_typst(
                output_dir=output_path.parent,
                image_prefix=output_path.stem,
                config=config,
            )
        else:
            output_path = None
            typst_source = self._render_typst(config=config)

        dpi_value = dpi if dpi is not None else ppi
        dpi_value = dpi_value if dpi_value is not None else self.figure.dpi

        if config.engine in ("auto", "core"):
            measurer = _get_core_measurer()
            if measurer is not None:
                if suffix == "png":
                    compiled = measurer.render_png(typst_source, ppi=float(dpi_value))
                elif suffix == "pdf":
                    compiled = measurer.render_pdf(typst_source)
                else:
                    compiled = measurer.render_svg(typst_source).encode("utf-8")
                self._write_compiled(filename, output_path, compiled)
                return
            if config.engine == "core":
                raise RuntimeError(_CORE_MISSING)

        # -- deprecated `query` engine: shell out to typst-py ---------------
        _warn_legacy_engine()
        root_override = typst_root if typst_root is not None else mpl.rcParams["typst.root"]
        if root_override is not None:
            root = Path(root_override).resolve()
        elif output_path is not None:
            root = output_path.parent
        else:
            root = PACKAGE_DIR

        compile_args: dict[str, Any] = {"format": suffix, "root": str(root)}
        if suffix == "png":
            compile_args["ppi"] = dpi_value
        optional = {
            "font_paths": _first(typst_font_paths, font_paths, mpl.rcParams["typst.font_paths"]),
            "ignore_system_fonts": _first(
                typst_ignore_system_fonts,
                ignore_system_fonts,
                mpl.rcParams["typst.ignore_system_fonts"],
            ),
            "sys_inputs": _first(typst_sys_inputs, sys_inputs, mpl.rcParams["typst.sys_inputs"]),
            "pdf_standards": _first(
                typst_pdf_standards, pdf_standards, mpl.rcParams["typst.pdf_standards"]
            ),
            "package_path": _first(
                typst_package_path, package_path, mpl.rcParams["typst.package_path"]
            ),
        }
        compile_args.update({k: v for k, v in optional.items() if v is not None})

        self._write_compiled(
            filename, output_path, typst.compile(typst_source.encode("utf-8"), **compile_args)
        )

    @staticmethod
    def _write_compiled(filename, output_path: Path | None, data) -> None:
        """Write compiled bytes to a path or a file-like object."""
        if output_path is None:
            filename.write(data)
        else:
            with cbook.open_file_cm(filename, "wb") as target:
                target.write(data)

    def print_pdf(self, filename, **kwargs):
        self._print_compiled(filename, "pdf", **kwargs)

    def print_svg(self, filename, **kwargs):
        self._print_compiled(filename, "svg", **kwargs)

    def print_png(self, filename, **kwargs):
        self._print_compiled(filename, "png", **kwargs)


RendererTypstNative = RendererTypst
FigureCanvas = FigureCanvasTypstNative


@_Backend.export
class _BackendTypstNative(_Backend):
    FigureCanvas = FigureCanvasTypstNative

