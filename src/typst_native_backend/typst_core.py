from typing import Any, Callable, Union

def normalize_string(s: str) -> str:
    # pls note that the order of the replacements matters, e.g., "\\" should be replaced before "\n", etc.
    rep_dict = {
        "\\": "\\\\",
        '"': '\\"',
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    for old, new in rep_dict.items():
        s = s.replace(old, new)
    return s


def normalize_content(s: str) -> str:
    # pls note that the order of the replacements matters, e.g., "\\" should be replaced before "\n", etc.
    rep_dict = {
        "\\": "\\\\",
        "[": "\\[",
        "]": "\\]",
        "#": "\\#",
        "$": "\\$",
        "*": "\\*",
        "_": "\\_",
        "\n": "#linebreak()",
    }
    # Backslash must be escaped first to avoid double-escaping generated slashes.
    for key, rep in rep_dict.items():
        s = s.replace(key, rep)
    return s


def format_typst_value(
    arg: Any,
    *,
    float_fmt: str = "{num:.6f}",
    in_content: bool = False,
) -> str:
    if in_content:
        if isinstance(arg, str):
            return normalize_content(arg)
        if isinstance(arg, (Content, Raw)):
            return arg.to_code()
        if isinstance(arg, Command):
            return arg.to_code(in_content=True)
        return f"#{format_typst_value(arg, float_fmt=float_fmt)}"

    if isinstance(arg, float):
        return float_fmt.format(num=arg)
    if isinstance(arg, int):
        return str(arg)
    if isinstance(arg, str):
        return normalize_string(arg)
    if isinstance(arg, Pos):
        return format_typst_value(arg.pos(), float_fmt=float_fmt)
    if isinstance(arg, (Raw, Length, Content, Command)):
        return arg.to_code()
    if isinstance(arg, (list, tuple)):
        string = (
            ", ".join(format_typst_value(item, float_fmt=float_fmt) for item in arg)
            + ","
        )  # always add a trailing comma as typst won't treat `(sth)` as an array
        return f"({string})"
    if isinstance(arg, dict):
        if len(arg) == 0:
            return "(:)"
        string = ", ".join(
            f"{key}: {format_typst_value(value, float_fmt=float_fmt)}"
            for key, value in arg.items()
        )
        return f"({string})"
    raise TypeError(f"Unsupported argument type: {type(arg)}")


class Length:
    def __init__(
        self,
        val: int | float | "Length",
        *,
        unit: str = "pt",
        fmt: str = "{value:.6f}{unit}",
    ):
        if isinstance(val, Length):
            val = val.val
        self.val = val
        self.unit = unit
        self.fmt = fmt

    def to_code(self) -> str:
        return self.fmt.format(value=self.val, unit=self.unit)


class Pos:
    def __init__(
        self,
        x: int | float | Length,
        y: int | float | Length,
        *,
        unit: str | None = None,
        num_fmt: str | None = None,
        transform: Callable[[int | float, int | float], tuple[int | float, int | float]]
        | None = None,
    ):
        self.x: int | float = x if isinstance(x, (int, float)) else x.val
        self.y: int | float = y if isinstance(y, (int, float)) else y.val
        self.unit = unit
        self.num_fmt = num_fmt
        self.transform = transform

    def pos(self) -> tuple[Length, Length]:
        x, y = self.x, self.y
        if self.transform is not None:
            x, y = self.transform(self.x, self.y)
        kwargs = {}
        if self.unit:
            kwargs["unit"] = self.unit
        if self.num_fmt:
            kwargs["fmt"] = self.num_fmt
        return Length(x, **kwargs), Length(y, **kwargs)


class Content:
    """Not Graceful yet"""

    def __init__(self, *content: "TypstType"):
        self.content = content

    def inner_code(self) -> str:
        return "".join(
            format_typst_value(item, in_content=True) for item in self.content
        )

    def to_code(self, in_content: bool = False) -> str:
        if in_content:
            return self.inner_code()
        return f'[{self.inner_code()}]'


class Raw:
    """For usage like auto, left, top, etc., which are not string literals but should be passed to Typst as is."""

    def __init__(self, code: str):
        self.code = code

    def to_code(self) -> str:
        return self.code


TypstType = Union[
    int, float, str, "Command", Content, list, tuple, dict, Pos, Raw, Length
]


class Command:
    """Abstraction of a Typst function call, used as a command to draw content in Typst."""

    def __init__(
        self,
        name: str,
        *args: TypstType,
        float_fmt: str = "{num:.6f}",
        pos_fmt: str = "({x:.6f}, {y:.6f})",
        **kwargs: TypstType,
    ):
        self.name = name
        self.args: list[TypstType] = []
        self.kwargs: dict[str, TypstType] = {}
        self.float_fmt = float_fmt
        self.pos_fmt = pos_fmt
        for arg in args:
            self.add_arg(arg)
        for key, value in kwargs.items():
            self.add_arg(value, name=key)

    def add_arg(
        self,
        arg: TypstType,
        *,
        name: str | None = None,
    ):
        if name is None:
            self.args.append(arg)
        else:
            self.kwargs[name] = arg

    def arg_fmt(
        self,
        arg: TypstType,
    ) -> str:
        return format_typst_value(arg, float_fmt=self.float_fmt)

    def to_code(self, indent_depth: int = 0, in_content: bool = False) -> str:
        args: list[str] = [
            f"{key}: {self.arg_fmt(value)}" for key, value in self.kwargs.items()
        ]
        args.extend(self.arg_fmt(arg) for arg in self.args)

        return (
            "  " * indent_depth
            + "#" * int(in_content)
            + f"{self.name}({', '.join(args)})"
        )


class Rgb(Command):
    """Typst rgb color command with normalized numeric channels."""

    def __init__(
        self,
        *args: int | float,
    ):
        channels = []
        if len(args) < 3 or len(args) > 4:
            raise ValueError("rgb color must have 3 or 4 channels")
        for value in args:
            if isinstance(value, int):
                if value < 0 or value > 255:
                    raise ValueError("integer color channel must be in 0..255")
                channels.append(value)
            elif isinstance(value, float):
                if value < 0 or value > 1:
                    raise ValueError("float color channel must be in 0..1")
                channels.append(round(float(value) * 255))
            else:
                raise TypeError("color channel must be int or float")
        if len(channels) == 4 and channels[-1] == 255:
            channels.pop()
        hex_color = "".join(f"{channel:02X}" for channel in channels)
        super().__init__("rgb", Raw(f'"#{hex_color}"'))
