# figdraw-typst-native

`figdraw-typst-native` 是一个 Matplotlib 后端，可以直接用原生 Typst 排版绘图文字，
并导出为 PDF、SVG 与 PNG。

文本度量与文档导出支持两套引擎：

| 引擎 | 说明 | 依赖 |
| --- | --- | --- |
| `core` | 通过 `mpl-typst_core` 常驻内存直接度量与导出，单次度量约 30 µs，无临时文件 | `mpl-typst-core` |
| `query` | 原有实现：向 Typst 文档注入锚点并 `typst.query` 探测，配合 `typst.compile` 导出 | `typst-py` |

默认 `auto`：已安装 `mpl-typst-core` 时使用 `core`，否则平滑回退到 `query`。
可通过 `mpl.rcParams["typst.engine"] = "core" | "query" | "auto"` 强制指定。

## 安装

本地路径安装：

```powershell
uv add "E:\LanguageSpecific\Typst\figdraw-typst-native"
```

GitHub 安装：

```powershell
uv add "git+ssh://git@github.com:111inhistory/figdraw-typst-native.git"
```

启用原生核心引擎（推荐）：

```powershell
uv add "figdraw-typst-native[core]"
```

wheel 安装：

```powershell
uv add ".\dist\figdraw_typst_native-0.1.0-py3-none-any.whl"
```

## 使用

`use()` 便捷入口：

```python
import typst_native_backend
typst_native_backend.use()
```

也可以直接使用 Matplotlib backend 字符串：

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

Typst 编译参数通过 `savefig` 传入：

```python
fig.savefig(
    "figure.pdf",
    typst_font_paths=["fonts"],
    typst_package_path="typst-packages",
    typst_sys_inputs={"key": "value"},
)
```

支持的编译参数：

- `typst_root`
- `typst_font_paths`
- `typst_ignore_system_fonts`
- `typst_sys_inputs`
- `typst_pdf_standards`
- `typst_package_path`

## 模板与配置

全局配置使用 Matplotlib 的 `rcParams`：

```python
import matplotlib as mpl
import typst_native_backend
mpl.rcParams["typst.font"] = ("Times New Roman", "SimSun")
mpl.rcParams["typst.page_padding"] = 12
mpl.rcParams["typst.preamble"] = "#set text(fill: black)"
mpl.rcParams["typst.engine"] = "core"
mpl.rcParams["typst.text_top_edge"] = "cap-height"  # Typst 默认值
```

`typst.text_top_edge` 接受 Typst 的长度（`1em`、`12pt`、`2mm`）或度量名
（`cap-height`、`ascender`、`x-height`、`baseline`、`bounds`）。度量名会
自动加引号；长度原样输出。`bottom-edge` 未暴露为配置项，保持 Typst 的默认
`baseline`，与渲染模板一致。

> 注意：早先的默认值是 `1em`，那只是为了绕开旧模板把度量名当变量引用的缺陷
> （`top-edge: cap-height` → `unknown variable`）。该缺陷已修复，默认值恢复为
> Typst 原生的 `cap-height`。

`typst_native_backend` 在导入时自动注册这些 `rcParams` key，因此可以直接写
`mpl.rcParams["typst.font"]`，不需要先调用一次后端。

恢复默认配置可以使用便捷函数：

```python
typst_native_backend.reset_config()
```

单次覆盖（优先级最高）：

```python
fig.savefig(
    "figure.pdf",
    typst_font=("Arial", "SimSun"),
    typst_page_padding=18,
    typst_preamble="#set text(fill: red)",
)
```

完整键值：

模块与排版：

- `typst.document_template`
- `typst.text_measure_template`
- `typst.engine`
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

所有 `savefig` 关键字都带有 `typst_` 前缀，例如：

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

语法检查：

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

## 许可证

MIT License。
