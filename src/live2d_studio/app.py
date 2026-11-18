"""Application entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import live2d
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from . import ICON_PATH
from .config import Config
from .ui.main_window import MainWindow
from .ui.theme import apply_studio_theme


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="live2d-studio",
        description="按 Part 编辑 Live2D MOC3 可见性，并合并/绑定动作",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL3_JSON",
        help="启动时打开 .model3.json",
    )
    parser.add_argument(
        "--hide-part",
        type=int,
        action="append",
        default=[],
        metavar="INDEX",
        help="仅用于预览/冒烟测试：启动时临时隐藏指定 Part（不写文件）",
    )
    parser.add_argument(
        "--screenshot",
        metavar="PNG",
        help="与 --smoke 一起使用：退出前保存窗口截图",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="启动、加载模型、截图后自动退出，用于自动化冒烟测试",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="--smoke 的等待秒数，默认 5 秒",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示 live2d-py 的详细加载日志",
    )
    return parser


def _configure_surface() -> None:
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(4)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.smoke and not args.model:
        parser.error("--smoke 需要同时提供 --model")

    _configure_surface()
    try:
        live2d.init()
        if not args.verbose:
            try:
                live2d.enableLog(False)
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - depends on the native build
        print(f"live2d.init() 失败：{exc}", file=sys.stderr)
        return 2

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Live2D Studio")
    app.setOrganizationName("live2d-studio")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    config = Config()
    apply_studio_theme(str(config.get("appearance.theme", "dark")))
    icon = QIcon(str(ICON_PATH))
    app.setWindowIcon(icon)

    window = MainWindow(config)
    # 标题栏图标监听 windowIconChanged，构造后单独设置一次才会显示
    window.setWindowIcon(icon)
    window.show()

    exit_code = 0
    if args.model:
        ok = window.load_model(Path(args.model))
        if not ok:
            exit_code = 2
        elif args.hide_part:
            count = window.document.part_count if window.document is not None else 0
            preview_hidden = {i for i in args.hide_part if 0 <= i < count}
            window.canvas.model_loaded.connect(
                lambda: window.canvas.set_hidden_parts(preview_hidden)
            )
            window.canvas.set_hidden_parts(preview_hidden)

    if args.smoke:
        def finish() -> None:
            if args.screenshot:
                target = Path(args.screenshot).expanduser().resolve()
                target.parent.mkdir(parents=True, exist_ok=True)
                saved = window.grab().save(str(target))
                print(f"screenshot: {target} ({'ok' if saved else 'failed'})")
            window.close()
            app.quit()

        QTimer.singleShot(max(500, int(args.timeout * 1000)), finish)

    try:
        app.exec()
    finally:
        window.shutdown()
        try:
            live2d.dispose()
        except Exception:
            pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
