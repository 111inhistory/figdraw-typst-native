# figdraw-typst-native

`figdraw-typst-native` 是一个 Matplotlib 后端，用于直接生成原生 Typst
绘图代码，并可通过 `typst-py` 编译为 PDF、SVG 或 PNG。

## 安装

本地路径依赖：

```powershell
uv add "E:\LanguageSpecific\Typst\figdraw-typst-native"
```

GitHub 依赖：

```powershell
uv add "git+https://github.com/<user>/<repo>.git@v0.1.0"
```

wheel 依赖：

```powershell
uv add ".\dist\figdraw_typst_native-0.1.0-py3-none-any.whl"
```

## 使用

启用后端：

```python
import typst_native_backend

typst_native_backend.use()
```

也可以直接使用 Matplotlib 的 backend 字符串：

```python
import matplotlib

matplotlib.use("module://typst_native_backend.backend")
```

最小示例：

```python
from pathlib import Path

import matplotlib.pyplot as plt
import typst_native_backend

typst_native_backend.use()

fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [0, 1, 0])
ax.set_xlabel("x")
ax.set_ylabel("y")

out = Path("outputs")
out.mkdir(exist_ok=True)
fig.savefig(out / "figure.typ")
fig.savefig(out / "figure.pdf")
fig.savefig(out / "figure.png", dpi=300)
```

## 导出参数

PNG 使用 Matplotlib 习惯的 `dpi`：

```python
fig.savefig("figure.png", dpi=300)
```

Typst 编译参数可以通过 `savefig` 传入：

```python
fig.savefig(
    "figure.pdf",
    typst_font_paths=["fonts"],
    typst_package_path="typst-packages",
    typst_sys_inputs={"key": "value"},
)
```

支持的编译参数包括：

- `typst_root`
- `typst_font_paths`
- `typst_ignore_system_fonts`
- `typst_sys_inputs`
- `typst_pdf_standards`
- `typst_package_path`

## 模板配置

全局配置使用 Matplotlib 的 `rcParams`：

```python
import matplotlib as mpl
import typst_native_backend

mpl.rcParams["typst.font"] = ("Times New Roman", "SimSun")
mpl.rcParams["typst.page_padding"] = 12
mpl.rcParams["typst.preamble"] = "#set text(fill: black)"
```

`typst_native_backend` 被导入时会注册这些 `rcParams` key。若直接写
`mpl.rcParams["typst.font"]`，需要先导入一次后端包。

恢复默认配置可以使用兼容函数：

```python
typst_native_backend.reset_config()
```

单次导出覆盖：

```python
fig.savefig(
    "figure.pdf",
    typst_font=("Arial", "SimSun"),
    typst_page_padding=18,
    typst_preamble="#set text(fill: red)",
)
```

可配置项：

模板和排版：

- `typst.document_template`
- `typst.text_measure_template`
- `typst.font`
- `typst.text_top_edge`
- `typst.page_padding`
- `typst.par_leading`
- `typst.par_spacing`
- `typst.preamble`
- `typst.measure_preamble`

Typst 编译：

- `typst.root`
- `typst.font_paths`
- `typst.ignore_system_fonts`
- `typst.sys_inputs`
- `typst.pdf_standards`
- `typst.package_path`

单次 `savefig` 覆盖参数仍保留 `typst_` 前缀，例如：

- `typst_font`
- `typst_page_padding`
- `typst_preamble`
- `typst_font_paths`
- `typst_package_path`

## 开发

安装依赖：

```powershell
uv sync
```

基础检查：

```powershell
uv run python -m py_compile `
  src\typst_native_backend\backend.py `
  src\typst_native_backend\typst_core.py `
  src\typst_native_backend\__init__.py
```

构建 wheel：

```powershell
uv build --wheel
```
