#!/usr/bin/env python3
"""Live2D Studio 构建与发版入口。

版本唯一来源是 git tag（vX.Y.Z）。正式发版由本脚本写入版本文件、提交、打标签并推送，
Windows 打包与 Release 发布由 .github/workflows/release.yml 完成。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
INIT_FILE = ROOT / "src" / "live2d_studio" / "__init__.py"
DIST_DIR = ROOT / "dist"


def log(message: str) -> None:
    print(message, flush=True)


def die(message: str) -> None:
    print(f"错误: {message}", file=sys.stderr)
    raise SystemExit(1)


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True
    )
    if proc.returncode != 0:
        if check:
            detail = proc.stderr.strip() or proc.stdout.strip()
            die(f"git {' '.join(args)} 失败: {detail or proc.returncode}")
        return ""
    return proc.stdout.strip()


def run(cmd: list[str], env: dict[str, str] | None = None) -> None:
    log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    if proc.returncode != 0:
        die(f"命令失败（退出码 {proc.returncode}）: {' '.join(cmd)}")


def ask_yes(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [Y/n]: ").strip().lower()
    except EOFError:
        die("无法读取确认输入，请使用交互式终端或加 --yes")
    return answer in ("", "y", "yes")


def require_git() -> None:
    if shutil.which("git") is None or not git("rev-parse", "--git-dir", check=False):
        die("当前目录不是 git 仓库或未安装 git")


def last_tag() -> str:
    return git("describe", "--tags", "--match", "v*", "--abbrev=0", check=False)


def dev_version() -> str:
    exact = git(
        "describe", "--tags", "--exact-match", "--match", "v*", "HEAD", check=False
    )
    if exact:
        return exact[1:]
    tag = last_tag()
    return f"{tag[1:]}-dev" if tag else "0.0.0-dev"


def check_release_env() -> None:
    branch = git("branch", "--show-current") or "detached"
    if branch != "main":
        die(f"只能在 main 分支发版，当前为 {branch}")
    if git("status", "--porcelain", "--untracked-files=no"):
        die("工作区有未提交改动，请先提交或暂存")
    if git("remote", check=False):
        git("fetch", "--tags", "--quiet")


def compute_release_version() -> tuple[str, str, str, str, int]:
    tag = last_tag()
    if not tag:
        if not git("log", "--format=%s", "-n", "1", check=False):
            die("仓库没有任何提交")
        return "1.0.0", "", "HEAD", "首个版本", 0

    range_spec = f"{tag}..HEAD"
    subjects = git("log", "--format=%s", range_spec, check=False)
    if not subjects:
        die(f"自 {tag} 以来没有新提交，无需发版")

    base = tag[1:].split("-", 1)[0]
    parts = (base.split(".") + ["0", "0"])[:3]
    major, minor, patch = (int(part) if part else 0 for part in parts)

    body = git("log", "--format=%s%n%b", range_spec)
    if re.search(r"BREAKING CHANGE|^[A-Za-z0-9_()\-]+!:", body, re.MULTILINE):
        bump = "major"
        major += 1
        minor = 0
        patch = 0
    elif re.search(r"^feat", subjects, re.MULTILINE):
        bump = "minor"
        minor += 1
        patch = 0
    else:
        bump = "patch"
        patch += 1

    count = int(git("rev-list", "--count", range_spec))
    return f"{major}.{minor}.{patch}", tag, range_spec, bump, count


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text("utf-8")
    new_text, count = re.subn(
        pattern, replacement, text, count=1, flags=re.MULTILINE
    )
    if count != 1:
        die(f"未能在 {path.relative_to(ROOT)} 中找到版本字段")
    path.write_text(new_text, encoding="utf-8")


def write_version(version: str) -> None:
    replace_once(PYPROJECT, r'^version = "[^"]*"', f'version = "{version}"')
    replace_once(INIT_FILE, r'^__version__ = "[^"]*"', f'__version__ = "{version}"')


def has_staged_changes() -> bool:
    proc = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    return proc.returncode != 0


def sync_lock() -> None:
    if shutil.which("uv") is None:
        die("未找到 uv，无法同步 uv.lock")
    run(["uv", "lock"])


def numeric_version(version: str) -> str:
    # Windows 版本资源要求 4 段纯数字
    parts = re.findall(r"\d+", version)[:4]
    parts += ["0"] * (4 - len(parts))
    return ".".join(parts)


def report_size(path: Path, label: str) -> None:
    if not path.exists():
        log(f"{label} 产物目录不存在：{path}")
        return
    total = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    log(f"{label} 产物：{path}（{total / 1024 / 1024:.1f} MiB）")


def build_local(version: str, target: str) -> None:
    env = os.environ | {"LIVE2D_STUDIO_VERSION": version}

    if target in ("pyinstaller", "both"):
        run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "live2d-studio.spec",
            ],
            env=env,
        )
        report_size(DIST_DIR / "Live2DStudio", "PyInstaller")

    if target in ("nuitka", "both"):
        if importlib.util.find_spec("nuitka") is None:
            die("未安装 Nuitka，先执行 uv pip install nuitka")
        output_name = "Live2D Studio.exe" if sys.platform == "win32" else "Live2D Studio"
        cmd = [
            sys.executable,
            "-m",
            "nuitka",
            "--standalone",
            "--enable-plugin=pyside6",
            "--output-dir=dist/nuitka",
            f"--output-filename={output_name}",
            "--include-package-data=live2d",
            "--include-data-files=src/live2d_studio/assets/live2d-studio.ico=live2d_studio/assets/live2d-studio.ico",
            "--noinclude-qt-translations",
            "--assume-yes-for-downloads",
            "--remove-output",
        ]
        excluded = ("numpy", "PIL", "scipy", "pandas", "tkinter", "unittest", "pytest")
        for name in excluded:
            cmd.append(f"--nofollow-import-to={name}")
        if sys.platform == "win32":
            cmd += [
                "--windows-console-mode=disable",
                "--windows-icon-from-ico=assets/live2d-studio.ico",
                "--company-name=Mrkinte",
                "--product-name=Live2D Studio",
                f"--file-version={numeric_version(version)}",
                f"--product-version={numeric_version(version)}",
            ]
        cmd.append("run_studio.py")
        run(cmd, env=env)
        outputs = sorted((DIST_DIR / "nuitka").glob("*.dist"))
        report_size(outputs[0] if outputs else DIST_DIR / "nuitka", "Nuitka")


def do_release(args: argparse.Namespace) -> int:
    check_release_env()
    version, base_tag, range_spec, bump, count = compute_release_version()

    log("发布模式：正式版")
    log(f"上一版本：{base_tag or '（无，首个版本）'}")
    if base_tag:
        log(f"提交范围：{range_spec}（{count} 个提交）")
    log(f"变更类型：{bump}")
    log(f"新版本：  v{version}")

    if args.preview:
        log("预览模式，未创建标签。")
        return 0

    tag = f"v{version}"
    if git("rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False):
        die(f"标签 {tag} 已存在")
    if not args.yes and not ask_yes(f"确认创建并推送标签 {tag}?"):
        die("已取消发版")

    write_version(version)
    sync_lock()
    git("add", "pyproject.toml", "uv.lock", "src/live2d_studio/__init__.py")
    if has_staged_changes():
        git("commit", "-m", f"chore(release): {tag}")
    else:
        log("版本文件无变化，直接在当前提交打标签")
    git("tag", "-a", tag, "-m", tag)
    git("push", "origin", "HEAD")
    git("push", "origin", tag)

    log(f"标签 {tag} 已推送，构建与发布由 GitHub Actions 执行")
    return 0


def do_dev(args: argparse.Namespace) -> int:
    version = dev_version()

    log("构建模式：开发版")
    log(f"当前分支：{git('branch', '--show-current') or 'detached'}")
    log(f"最近标签：{last_tag() or '（无）'}")
    if git(
        "describe", "--tags", "--exact-match", "--match", "v*", "HEAD", check=False
    ):
        log("当前提交正好在标签上，产物版本为正式版本。")
    if git("status", "--porcelain", "--untracked-files=no"):
        log("工作区有未提交改动（不会体现在版本号里）。")
    log(f"构建版本：{version}")

    if args.preview:
        log("预览模式，未执行构建。")
        return 0

    if not args.yes and not ask_yes(f"确认使用版本 {version} 继续构建?"):
        die("已取消构建")
    if args.build == "none":
        log("未指定 --build，跳过本地打包。")
        return 0

    build_local(version, args.build)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="script/release.py",
        description="Live2D Studio 构建与发版入口",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dev", action="store_true", help="构建开发版（默认）")
    mode.add_argument("--release", action="store_true", help="发布正式版")
    parser.add_argument(
        "--preview",
        "--dry-run",
        dest="preview",
        action="store_true",
        help="只预览版本号，不构建、不改文件",
    )
    parser.add_argument(
        "--build",
        choices=("none", "pyinstaller", "nuitka", "both"),
        default="none",
        help="开发版本地打包目标，默认 none",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.release and args.build != "none":
        die("正式发版由 GitHub Actions 打包，--build 仅用于 --dev")
    require_git()
    return do_release(args) if args.release else do_dev(args)


if __name__ == "__main__":
    raise SystemExit(main())
