# SPDX-License-Identifier: AGPL-3.0-or-later
"""3D models after they are made: what they measure, whether they print, and exports.

Generated models are GLB files, Y up, about one unit tall. Printing formats
(STL, 3MF) are turned Z up, scaled to a size in millimetres and set on the
plate; OBJ and PLY keep the model's own frame for 3D programs.

Runs in the core (no engine needed), on a worker thread: loading a
500,000-face model takes a few seconds.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import manifold3d
import numpy as np
import trimesh

FORMATS = {
    "glb": "GLB, with textures (Blender, game engines, the web)",
    "stl": "STL for printing, in millimetres",
    "3mf": "3MF for printing, in millimetres",
    "obj": "OBJ with its textures, as a zip",
    "ply": "PLY with vertex colours",
}
PRINT_FORMATS = {"stl", "3mf"}
DEFAULT_HEIGHT_MM = 100.0
# Mean wall thickness (2 x volume / area) as a share of the model's size. A
# surface folded over itself (outside plus an inside-out copy) measures 0.002-0.003;
# real objects 0.04-0.19, thin blades included; a cup with 2 mm walls about 0.02.
SKIN = 0.01
Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])


def load(path: Path) -> trimesh.Trimesh:
    """The model as one mesh, textures kept where the file has them."""
    scene = trimesh.load(str(path), force="scene")
    meshes = [g for g in scene.dump() if isinstance(g, trimesh.Trimesh)]
    if not meshes:
        raise ValueError("The file holds no mesh.")
    return meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)


def geometry(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Shape only, with the seams where texture islands meet welded shut."""
    geo = trimesh.Trimesh(mesh.vertices, mesh.faces, process=True)
    geo.merge_vertices(merge_tex=True, merge_norm=True)
    return geo


def solid(mesh: trimesh.Trimesh) -> manifold3d.Manifold:
    """The mesh as a closed solid; raises if it is not one."""
    geo = geometry(mesh)
    m = manifold3d.Manifold(
        manifold3d.Mesh(
            vert_properties=np.asarray(geo.vertices, np.float32),
            tri_verts=np.asarray(geo.faces, np.uint32),
        )
    )
    status = m.status()
    if status != manifold3d.Error.NoError:
        raise ValueError(str(status).split(".")[-1])
    return m


def inspect(path: Path) -> dict:
    """Size, faces and a printability verdict with plain reasons."""
    mesh = load(path)
    extents = [float(x) for x in mesh.bounding_box.extents]
    info: dict = {
        "faces": int(len(mesh.faces)),
        "extents": [round(x, 4) for x in extents],  # x, y (up), z in model units
        "textured": bool(getattr(mesh.visual, "uv", None) is not None),
    }
    reasons = []
    try:
        m = solid(mesh)
    except ValueError as e:
        info |= {"watertight": False, "printable": False}
        info["reasons"] = [f"The surface is not closed ({e})."]
        return info
    parts = m.decompose()
    volumes = [p.volume() for p in parts]
    # A part inside another part's outline with its faces turned inward is a hidden
    # cavity: the model would print hollow.
    cavities = sum(1 for v in volumes if v < 0)
    loose = sum(1 for v in volumes if 0 <= v < 0.001 * max(volumes, default=1))
    size = max(extents)
    thickness = 2 * abs(m.volume()) / max(m.surface_area(), 1e-12) / max(size, 1e-12)
    info |= {
        "watertight": True,
        "volume": round(float(m.volume()), 6),
        "parts": len(parts),
        "cavities": cavities,
        "thickness": round(float(thickness), 4),
    }
    if thickness < SKIN:
        reasons.append("It is only a thin skin, hollow inside, and would print as an empty shell.")
    elif cavities:
        reasons.append("It has a hidden hollow inside and would print as a shell.")
    if loose:
        reasons.append(f"{loose} tiny loose piece{'s' if loose > 1 else ''} float apart from it.")
    if m.volume() <= 0:
        reasons.append("Its faces point inward.")
    info["printable"] = not reasons
    info["reasons"] = reasons
    return info


def _for_print(mesh: trimesh.Trimesh, height_mm: float) -> trimesh.Trimesh:
    """Z up, scaled to height_mm, centred on the plate, without loose specks."""
    m = solid(mesh)
    parts = m.decompose()
    if len(parts) > 1:
        biggest = max(abs(p.volume()) for p in parts)
        m = manifold3d.Manifold.batch_boolean(
            [p for p in parts if abs(p.volume()) >= 0.001 * biggest], manifold3d.OpType.Add
        )
    out = m.to_mesh()
    geo = trimesh.Trimesh(out.vert_properties[:, :3], out.tri_verts, process=False)
    geo.apply_transform(Y_UP_TO_Z_UP)
    lo, hi = geo.bounds
    geo.apply_scale(height_mm / max(hi[2] - lo[2], 1e-9))
    lo, hi = geo.bounds
    geo.apply_translation([-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]])
    return geo


_3MF_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="model" '
    'ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
    "</Types>"
)
_3MF_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
    'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
    "</Relationships>"
)


def three_mf(geo: trimesh.Trimesh) -> bytes:
    """A minimal 3MF package (core spec): one object, millimetres."""
    verts = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in geo.vertices)
    tris = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in geo.faces)
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        '<resources><object id="1" type="model"><mesh>'
        f"<vertices>{verts}</vertices><triangles>{tris}</triangles>"
        '</mesh></object></resources><build><item objectid="1"/></build></model>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _3MF_TYPES)
        z.writestr("_rels/.rels", _3MF_RELS)
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


def export(src: Path, fmt: str, height_mm: float | None = None) -> tuple[bytes, str]:
    """The model in another format: (file bytes, file extension)."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt}.")
    if fmt == "glb":
        return src.read_bytes(), "glb"
    mesh = load(src)
    if fmt in PRINT_FORMATS:
        geo = _for_print(mesh, height_mm or DEFAULT_HEIGHT_MM)
        if fmt == "3mf":
            return three_mf(geo), "3mf"
        return geo.export(file_type="stl"), "stl"
    if fmt == "ply":
        if getattr(mesh.visual, "kind", None) == "texture":
            mesh = mesh.copy()
            mesh.visual = mesh.visual.to_color()
        return mesh.export(file_type="ply"), "ply"
    # OBJ: the mesh, its material and texture files, zipped together.
    files = trimesh.exchange.obj.export_obj(
        mesh, include_texture=True, return_texture=True, mtl_name="model.mtl"
    )
    text, extra = files if isinstance(files, tuple) else (files, {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("model.obj", text)
        for name, data in extra.items():
            z.writestr(name, data)
    return buf.getvalue(), "zip"
