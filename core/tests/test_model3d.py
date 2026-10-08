# SPDX-License-Identifier: AGPL-3.0-or-later
"""3D: the graphs follow the upstream pipelines, the ComfyUI patch applies strictly,
and finished models are measured and exported correctly."""

import io
import struct
import zipfile
from pathlib import Path

import manifold3d
import numpy as np
import pytest
import trimesh

from irisecho_core import mesh3d, registry
from irisecho_core.engines.comfy import graphs, sourcepatch

PATCHES = Path(graphs.__file__).parent / "patches"


def names_for(model_id: str) -> dict:
    reg = registry.default()
    return {s.id: f"{s.id}.safetensors" for s in reg.expand(reg.models[model_id].variants[0].files)}


def build(model_id: str, **params):
    builder = graphs.BUILDERS[registry.default().models[model_id].variants[0].workflow]
    images = {"front": "front.png", "back": "back.png"}
    return builder(seed=7, names=names_for(model_id), prefix="x", images=images, params=params)


def links(graph: dict):
    for nid, node in graph.items():
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                yield nid, value[0]


@pytest.mark.parametrize("model_id", ["trellis2", "pixal3d"])
def test_3d_graphs_are_complete(model_id):
    for textures in (True, False):
        g, save = build(model_id, textures=textures)
        assert g[save]["class_type"] == "SaveGLB"
        assert g["thumb"]["class_type"] == "SaveImage"
        for nid, source in links(g):
            assert source in g, f"{nid} reads from missing node {source}"
        assert ("sample_texture" in g) == textures


@pytest.mark.parametrize("model_id", ["trellis2", "pixal3d"])
def test_3d_graphs_follow_the_upstream_samplers(model_id):
    g, _ = build(model_id)
    for name in ("sample_structure", "sample_shape", "sample_detail", "sample_texture"):
        assert g[name]["inputs"]["steps"] == 12
        assert g[name]["inputs"]["sampler_name"] == "euler"
    assert g["sample_texture"]["inputs"]["cfg"] == 1.0
    # guidance only above sigma 0.6, as upstream's guidance_interval [0.6, 1.0]
    assert g["ss_window"]["inputs"]["start_percent"] == 0.667
    assert g["shape_window"]["inputs"]["start_percent"] == 0.667
    assert g["ss_rescale"]["inputs"]["multiplier"] == 0.7
    assert g["shape_rescale"]["inputs"]["multiplier"] == 0.5
    assert g["ss_shift"]["inputs"]["shift"] == 5.0


def test_3d_finishing_leaves_one_solid():
    g, _ = build("trellis2")
    remesh = g["remesh"]["inputs"]
    assert remesh["sign_mode"] == "solid" and remesh["smooth_iters"] == 3
    assert remesh["sign_mode.fill"] == graphs.FILL["close"]
    assert build("trellis2", openings="keep")[0]["remesh"]["inputs"]["sign_mode.fill"] == 1000.0


def test_3d_detail_sets_the_voxel_resolution():
    assert build("trellis2")[0]["upsample"]["inputs"]["target_resolution"] == 1024
    assert build("trellis2", detail="high")[0]["upsample"]["inputs"]["target_resolution"] == 1536
    # the engine steps down when High does not fit
    g = build("trellis2", detail="high", resolution=1280)[0]
    assert g["upsample"]["inputs"]["target_resolution"] == 1280


def test_3d_triangle_counts():
    assert build("trellis2")[0]["decimate"]["inputs"]["target_face_count"] == 500_000
    assert build("trellis2", faces=20_000)[0]["decimate"]["inputs"]["target_face_count"] == 20_000
    with pytest.raises(ValueError):
        build("trellis2", faces=12_345)


def test_trellis2_crops_tight_and_pixal3d_uses_the_rig():
    assert build("trellis2")[0]["crop"]["inputs"]["pad_factor"] == 1.0
    g, _ = build("pixal3d")
    assert g["cond"]["class_type"] == "Pixal3DMultiViewConditioning"
    assert g["cond"]["inputs"]["fov"] == 20.0
    assert g["frame"]["class_type"] == "IrisEchoFrameViews"


def test_pixal3d_needs_both_views():
    builder = graphs.BUILDERS["pixal3d-mv"]
    with pytest.raises(ValueError, match="front and a back"):
        builder(
            seed=1, names=names_for("pixal3d"), prefix="x", images={"front": "f.png"}, params={}
        )


def test_views_back_checks_the_mirror_and_frames_both():
    names = names_for("object-views")
    g = graphs.views_back(names=names, image="f.png", attempt=1, seed=3, prefix="x")
    assert g["q_text"]["inputs"]["prompt"] == graphs.BACK_PROMPTS[1]
    assert g["check"]["class_type"] == "IrisEchoViewCheck"
    for nid, source in links(g):
        assert source in g, f"{nid} reads from missing node {source}"
    level = graphs.views_level(names=names, image="f.png", seed=3, prefix="x")
    assert level["same"]["class_type"] == "IrisEchoSimilarity"


# --- the ComfyUI patch -------------------------------------------------------

DIFF = """diff --git a/pkg/a.py b/pkg/a.py
index {old}..{new} 100644
--- a/pkg/a.py
+++ b/pkg/a.py
@@ -1,3 +1,3 @@
 one
-two
+TWO
 three
"""


def make_diff(before: bytes, after: bytes) -> str:
    return DIFF.format(
        old=sourcepatch.blob_sha1(before)[:11], new=sourcepatch.blob_sha1(after)[:11]
    )


def test_patch_applies_once_and_checks_the_result(tmp_path):
    before, after = b"one\ntwo\nthree\n", b"one\nTWO\nthree\n"
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_bytes(before)
    diff = make_diff(before, after)
    assert sourcepatch.state(tmp_path, diff) == "pristine"
    assert sourcepatch.apply(tmp_path, diff) is True
    assert (tmp_path / "pkg" / "a.py").read_bytes() == after
    assert sourcepatch.apply(tmp_path, diff) is False  # already applied


def test_patch_refuses_a_file_it_was_not_made_for(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_bytes(b"one\ntwo\nthree\nfour\n")
    diff = make_diff(b"one\ntwo\nthree\n", b"one\nTWO\nthree\n")
    with pytest.raises(sourcepatch.PatchError):
        sourcepatch.apply(tmp_path, diff)
    assert (tmp_path / "pkg" / "a.py").read_bytes() == b"one\ntwo\nthree\nfour\n"


def test_the_shipped_comfy_patch_parses():
    diff = (PATCHES / "comfyui-pr16805-remesh.diff").read_text(encoding="utf-8")
    files = sourcepatch.parse(diff)
    assert {f.path for f in files} == {
        "comfy/ldm/trellis2/model.py",
        "comfy_extras/mesh3d/postprocess/qem_decimate.py",
        "comfy_extras/mesh3d/postprocess/remesh.py",
        "comfy_extras/nodes_mesh_postprocess.py",
    }
    assert all(f.old_blob and f.new_blob for f in files)


# --- finished models ---------------------------------------------------------


def glb(tmp_path: Path, m: manifold3d.Manifold, name: str) -> Path:
    mesh = m.to_mesh()
    t = trimesh.Trimesh(mesh.vert_properties[:, :3], mesh.tri_verts)
    path = tmp_path / name
    path.write_bytes(t.export(file_type="glb"))
    return path


def test_a_solid_is_printable(tmp_path):
    path = glb(tmp_path, manifold3d.Manifold.cube([0.6, 1.0, 0.4], center=True), "box.glb")
    info = mesh3d.inspect(path)
    assert info["printable"] and info["watertight"] and info["parts"] == 1
    assert info["extents"] == [0.6, 1.0, 0.4]


def test_a_thin_skin_is_not_printable(tmp_path):
    outer = manifold3d.Manifold.cube([1, 1, 1], center=True)
    inner = manifold3d.Manifold.cube([0.996, 0.996, 0.996], center=True)
    info = mesh3d.inspect(glb(tmp_path, outer - inner, "skin.glb"))
    assert not info["printable"]
    assert "thin skin" in " ".join(info["reasons"])


def test_print_exports_are_upright_millimetres(tmp_path):
    # 1 unit tall along Y (glTF up): printed 80 mm tall along Z
    path = glb(tmp_path, manifold3d.Manifold.cube([0.5, 1.0, 0.25], center=True), "box.glb")
    data, ext = mesh3d.export(path, "stl", 80)
    stl = trimesh.load(io.BytesIO(data), file_type="stl")
    assert ext == "stl"
    assert np.allclose(stl.extents, [40, 20, 80], atol=1e-3)
    assert abs(stl.bounds[0][2]) < 1e-6  # on the plate
    data, ext = mesh3d.export(path, "3mf", 80)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert set(z.namelist()) == {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}
        model = z.read("3D/3dmodel.model").decode()
    assert 'unit="millimeter"' in model and model.count("<triangle ") == 12


def test_other_exports(tmp_path):
    path = glb(tmp_path, manifold3d.Manifold.cube([1, 1, 1]), "box.glb")
    assert mesh3d.export(path, "glb")[0] == path.read_bytes()
    data, ext = mesh3d.export(path, "obj")
    assert ext == "zip" and "model.obj" in zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert mesh3d.export(path, "ply")[0].startswith(b"ply")
    with pytest.raises(ValueError):
        mesh3d.export(path, "fbx")


def test_the_command_line_offers_every_format():
    from irisecho_core import cli

    assert cli.MODEL3D_FORMATS == tuple(mesh3d.FORMATS)


def test_glb_header_is_served_as_a_model():
    from irisecho_core import server

    assert server.MEDIA_TYPES[".glb"] == "model/gltf-binary"
    assert struct.pack("<I", 0x46546C67) == b"glTF"
