from __future__ import annotations

import codecs
import functools
import json
import math
import os
import re
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


def _is_path_target(filename) -> bool:
    return isinstance(filename, str | os.PathLike)


PACKAGE_DIR = Path(__file__).resolve().parent
DOCUMENT_TEMPLATE = PACKAGE_DIR / "templates" / "native_document.typ"
TEXT_MEASURE_TEMPLATE = PACKAGE_DIR / "templates" / "text_measure.typ"
PAGE_PADDING_PT = 12.0


@dataclass(frozen=True)
class TypstNativeConfig:
    document_template: str | os.PathLike = DOCUMENT_TEMPLATE
    text_measure_template: str | os.PathLike = TEXT_MEASURE_TEMPLATE
    font: str | Sequence[str] | Raw = ("Times New Roman", "SimSun")
    text_top_edge: str = "1em"
    page_padding: float = PAGE_PADDING_PT
    par_leading: str = "0pt"
    par_spacing: str = "0pt"
    preamble: str = ""
    measure_preamble: str = ""


_CONFIG = TypstNativeConfig()
_RC_DEFAULTS: dict[str, Any] = {
    "typst.document_template": str(DOCUMENT_TEMPLATE),
    "typst.text_measure_template": str(TEXT_MEASURE_TEMPLATE),
    "typst.font": ("Times New Roman", "SimSun"),
    "typst.text_top_edge": "1em",
    "typst.page_padding": PAGE_PADDING_PT,
    "typst.par_leading": "0pt",
    "typst.par_spacing": "0pt",
    "typst.preamble": "",
    "typst.measure_preamble": "",
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
}


def register_rcparams() -> None:
    for key, default in _RC_DEFAULTS.items():
        mpl.rcParams.validate.setdefault(key, lambda value: value)
        mpl.rcParamsDefault.setdefault(key, default)
        mpl.rcParamsOrig.setdefault(key, default)
        if key not in mpl.rcParams:
            mpl.rcParams[key] = default


register_rcparams()


def configure(**kwargs) -> None:
    global _CONFIG

    register_rcparams()
    updates: dict[str, Any] = {}
    for key, value in kwargs.items():
        rc_key = _CONFIG_FIELDS.get(key, key)
        if not rc_key.startswith("typst."):
            raise KeyError(f"unsupported Typst native config key: {key}")
        mpl.rcParams[rc_key] = value
        if key in _CONFIG_FIELDS:
            updates[key] = value
    _CONFIG = replace(_CONFIG, **updates) if updates else get_config()


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
    )


def reset_config() -> None:
    global _CONFIG

    register_rcparams()
    for key, value in _RC_DEFAULTS.items():
        mpl.rcParams[key] = value
    _CONFIG = TypstNativeConfig()


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
    updates: dict[str, Any] = {}
    names = {
        "typst_document_template": "document_template",
        "typst_text_measure_template": "text_measure_template",
        "typst_font": "font",
        "typst_text_top_edge": "text_top_edge",
        "typst_page_padding": "page_padding",
        "typst_par_leading": "par_leading",
        "typst_par_spacing": "par_spacing",
        "typst_preamble": "preamble",
        "typst_measure_preamble": "measure_preamble",
    }
    for key, field in names.items():
        if key in kwargs:
            updates[field] = kwargs.pop(key)
    return replace(config, **updates) if updates else config


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
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        metrics = self._measure_uncached(text, size, ismath, font_names)
        if len(self._cache) >= self.maxsize:
            del self._cache[next(iter(self._cache))]
        self._cache[key] = metrics
        return metrics

    def _measure_uncached(
        self, text: str, size: float, ismath: bool | str, font_names: tuple[str, ...]
    ) -> tuple[float, float, float]:
        text_body = _text_body(text, ismath).to_code(in_content=True)
        source = Template(
            Path(self.config.text_measure_template).read_text(encoding="utf-8")
        ).substitute(
            font_size=Length(size).to_code(),
            text_font=_font_code(self.config.font),
            text_top_edge=self.config.text_top_edge,
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
    return TypstTextMeasurer(config)


def clear_cache() -> None:
    """Clear in-memory text measurement caches."""
    _get_text_measurer.cache_clear()


def save_cache(file_path: str | os.PathLike) -> None:
    """Explicitly save in-memory cache to a user-specified file path."""
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
    """Explicitly load cache from a user-specified file path."""
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

        for vertices, code in path.iter_segments(
            transform,
            simplify=False,
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
            text_top_edge=self.config.text_top_edge,
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
        for key in (
            "bbox_inches_restore",
            "facecolor",
            "edgecolor",
            "orientation",
            "metadata",
        ):
            kwargs.pop(key, None)
        if kwargs:
            names = ", ".join(sorted(kwargs))
            raise TypeError(
                f"unsupported savefig argument(s) for Typst backend: {names}"
            )

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
        bbox_inches_restore=None,
        facecolor=None,
        edgecolor=None,
        orientation=None,
        metadata=None,
        pil_kwargs=None,
        **kwargs,
    ) -> None:
        config = _config_from_kwargs(kwargs)
        if kwargs:
            names = ", ".join(sorted(kwargs))
            raise TypeError(
                f"unsupported savefig argument(s) for Typst backend: {names}"
            )

        rc_root = mpl.rcParams["typst.root"]
        root_override = typst_root if typst_root is not None else rc_root
        if _is_path_target(filename):
            output_path = Path(filename).resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            typst_source = self._render_typst(
                output_dir=output_path.parent,
                image_prefix=output_path.stem,
                config=config,
            )
            root = (
                Path(root_override).resolve() if root_override else output_path.parent
            )
        else:
            output_path = None
            typst_source = self._render_typst(config=config)
            root = Path(root_override).resolve() if root_override else PACKAGE_DIR

        compile_args: dict[str, Any] = {
            "format": suffix,
            "root": str(root),
        }
        if suffix == "png":
            png_dpi = dpi if dpi is not None else ppi
            compile_args["ppi"] = png_dpi if png_dpi is not None else self.figure.dpi
        if typst_font_paths is not None:
            font_paths = typst_font_paths
        if font_paths is None:
            font_paths = mpl.rcParams["typst.font_paths"]
        if font_paths is not None:
            compile_args["font_paths"] = font_paths
        if typst_ignore_system_fonts is not None:
            ignore_system_fonts = typst_ignore_system_fonts
        if ignore_system_fonts is None:
            ignore_system_fonts = mpl.rcParams["typst.ignore_system_fonts"]
        if ignore_system_fonts is not None:
            compile_args["ignore_system_fonts"] = ignore_system_fonts
        if typst_sys_inputs is not None:
            sys_inputs = typst_sys_inputs
        if sys_inputs is None:
            sys_inputs = mpl.rcParams["typst.sys_inputs"]
        if sys_inputs is not None:
            compile_args["sys_inputs"] = sys_inputs
        if typst_pdf_standards is not None:
            pdf_standards = typst_pdf_standards
        if pdf_standards is None:
            pdf_standards = mpl.rcParams["typst.pdf_standards"]
        if pdf_standards is not None:
            compile_args["pdf_standards"] = pdf_standards
        if typst_package_path is not None:
            package_path = typst_package_path
        if package_path is None:
            package_path = mpl.rcParams["typst.package_path"]
        if package_path is not None:
            compile_args["package_path"] = package_path
        try:
            compiled = typst.compile(
                typst_source.encode("utf-8"),
                **compile_args,
            )
        except typst.TypstError as exc:
            raise RuntimeError("typst CLI executable was not found") from exc
        if output_path is None:
            filename.write(compiled)
        else:
            with cbook.open_file_cm(filename, "wb") as target:
                target.write(compiled)

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
