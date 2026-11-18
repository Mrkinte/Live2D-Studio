"""A read-only structure snapshot of a parsed MOC3, sourced from py-moc3."""

from __future__ import annotations

from dataclasses import dataclass

from moc3 import CountIdx, SECTION_LAYOUT
from moc3._core import ADDITIONAL_V303

from .moc3_document import Moc3Document

_SECTION_GROUPS = {
    "part": "Part",
    "deformer": "变形器",
    "warp_deformer": "弯曲变形器",
    "rotation_deformer": "旋转变形器",
    "art_mesh": "美术网格",
    "parameter": "参数",
    "part_keyform": "图层关键帧",
    "warp_deformer_keyform": "弯曲变形器关键帧",
    "rotation_deformer_keyform": "旋转变形器关键帧",
    "art_mesh_keyform": "美术网格关键帧",
    "keyform_position": "关键帧位置",
    "keyform_binding_index": "关键帧绑定索引",
    "keyform_binding_band": "关键帧绑定带",
    "keyform_binding": "关键帧绑定",
    "keys": "关键帧",
    "uv": "UV",
    "position_index": "顶点索引",
    "drawable_mask": "遮罩",
    "draw_order_group": "绘制顺序组",
    "draw_order_group_object": "绘制顺序对象",
    "glue": "胶水",
    "glue_info": "胶水信息",
    "glue_keyform": "胶水关键帧",
    "additional": "附加",
}

_SECTION_TYPES = {
    "runtime": "运行时数据",
    "str64": "字符串",
    "i32": "int32",
    "f32": "float32",
    "i16": "int16",
    "u8": "uint8",
    "bool": "bool",
}

# 按展示顺序排列，分组只用于表格前缀
_COUNT_ROWS = (
    ("结构", CountIdx.PARTS, "图层 Part"),
    ("结构", CountIdx.ART_MESHES, "美术网格"),
    ("结构", CountIdx.PARAMETERS, "参数"),
    ("变形器", CountIdx.DEFORMERS, "变形器总数"),
    ("变形器", CountIdx.WARP_DEFORMERS, "弯曲变形器"),
    ("变形器", CountIdx.ROTATION_DEFORMERS, "旋转变形器"),
    ("关键帧", CountIdx.PART_KEYFORMS, "图层关键帧"),
    ("关键帧", CountIdx.WARP_DEFORMER_KEYFORMS, "弯曲变形器关键帧"),
    ("关键帧", CountIdx.ROTATION_DEFORMER_KEYFORMS, "旋转变形器关键帧"),
    ("关键帧", CountIdx.ART_MESH_KEYFORMS, "美术网格关键帧"),
    ("关键帧绑定", CountIdx.KEYS, "关键帧"),
    ("关键帧绑定", CountIdx.KEYFORM_BINDINGS, "绑定"),
    ("关键帧绑定", CountIdx.KEYFORM_BINDING_BANDS, "绑定带"),
    ("关键帧绑定", CountIdx.KEYFORM_BINDING_INDICES, "绑定索引"),
    ("几何", CountIdx.KEYFORM_POSITIONS, "关键帧位置"),
    ("几何", CountIdx.UVS, "UV 坐标"),
    ("几何", CountIdx.POSITION_INDICES, "顶点索引"),
    ("绘制", CountIdx.DRAWABLE_MASKS, "遮罩"),
    ("绘制", CountIdx.DRAW_ORDER_GROUPS, "绘制顺序组"),
    ("绘制", CountIdx.DRAW_ORDER_GROUP_OBJECTS, "绘制顺序对象"),
    ("胶水", CountIdx.GLUES, "胶水"),
    ("胶水", CountIdx.GLUE_INFOS, "胶水信息"),
    ("胶水", CountIdx.GLUE_KEYFORMS, "胶水关键帧"),
)


@dataclass(frozen=True)
class ModelInfo:
    overview: tuple[tuple[str, str], ...]
    counts: tuple[tuple[str, str, int], ...]
    sections: tuple[tuple[str, str, str, int], ...]


def read_model_info(document: Moc3Document) -> ModelInfo:
    moc = document.moc
    header = document.header
    canvas = moc.canvas

    overview = (
        ("文件", document.path.name),
        ("路径", str(document.path)),
        ("文件大小", f"{header.size:,} 字节"),
        ("SHA-256", header.sha256),
        ("MOC3 版本", f"{header.version_name}（版本号 {header.version}）"),
        ("字节序", "小端（LE）" if header.endian == 0 else "大端（BE）"),
        (
            "model3.json",
            document.model3_path.name if document.model3_path else "—",
        ),
        (
            "画布尺寸",
            f"{canvas.canvas_width:g} × {canvas.canvas_height:g}",
        ),
        ("像素 / 单位", f"{canvas.pixels_per_unit:g}"),
        ("画布原点", f"({canvas.origin_x:g}, {canvas.origin_y:g})"),
        ("画布标记", str(canvas.canvas_flag)),
    )

    counts = tuple(
        (group, label, int(moc.counts[index]))
        for group, index, label in _COUNT_ROWS
    )

    layout = list(SECTION_LAYOUT)
    if header.version >= 3:
        layout.append(ADDITIONAL_V303)

    sections = []
    for entry in layout:
        if entry.count_idx >= 0:
            count = int(moc.counts[entry.count_idx])
        else:
            count = len(moc.get(entry.name))
        sections.append(
            (
                _SECTION_GROUPS.get(entry.group, entry.group),
                entry.name,
                _SECTION_TYPES.get(entry.elem_type, entry.elem_type),
                count,
            )
        )

    return ModelInfo(overview=overview, counts=counts, sections=tuple(sections))
