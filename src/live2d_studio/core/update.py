"""GitHub Release 元数据与版本比较（无 Qt 逻辑）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

REPO = "mrkinte/Live2D-Studio"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


class UpdateError(RuntimeError):
    """Release 响应缺少必要字段或格式不正确。"""


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    name: str
    page_url: str
    notes: str
    published_at: str


def parse_version(text: str) -> tuple[int, ...]:
    """把 ``v1.2.3`` / ``1.2.3-dev`` 解析为可比较的数字元组。"""
    base = str(text).strip().lstrip("vV").split("-", 1)[0]
    numbers = tuple(int(part) for part in re.findall(r"\d+", base))
    return numbers or (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def release_from_json(payload: Any) -> ReleaseInfo:
    """从 GitHub ``/releases/latest`` 的 JSON 中提取版本信息。"""
    if not isinstance(payload, dict):
        raise UpdateError("GitHub 返回的数据格式不正确")
    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("GitHub 没有返回最新版本号")

    version = tag[1:] if tag[:1] in ("v", "V") else tag
    return ReleaseInfo(
        tag=tag,
        version=version,
        name=str(payload.get("name") or tag),
        page_url=str(payload.get("html_url") or RELEASES_PAGE),
        notes=str(payload.get("body") or ""),
        published_at=str(payload.get("published_at") or ""),
    )
