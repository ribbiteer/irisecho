# SPDX-License-Identifier: AGPL-3.0-or-later
"""ComfyUI graphs, written as plain Python.

Each builder returns (graph, save_node_id) in ComfyUI's API format:
{node_id: {"class_type": ..., "inputs": {...}}}, links as [node_id, output].
Settings follow each model's published recipe. Distilled models (Z-Image,
schnell, FLUX dev at guidance distillation) run at cfg 1, so they get no
negative prompt at all.
"""

from __future__ import annotations

import math

ASPECTS = {
    "1:1": (1, 1),
    "4:3": (4, 3),
    "3:4": (3, 4),
    "16:9": (16, 9),
    "9:16": (9, 16),
    "3:2": (3, 2),
    "2:3": (2, 3),
}

# Which stage each node class belongs to, for progress messages.
STAGES = {
    "load": {
        "NunchakuZImageDiTLoader",
        "NunchakuFluxDiTLoader",
        "NunchakuQwenImageDiTLoader",
        "UNETLoader",
        "UnetLoaderGGUF",
        "CLIPLoader",
        "CLIPVisionLoader",
        "DualCLIPLoader",
        "DualCLIPLoaderGGUF",
        "VAELoader",
    },
    "sample": {"SamplerCustomAdvanced", "KSampler", "KSamplerAdvanced"},
    "decode": {"VAEDecode", "VAEDecodeTiled"},
}


def size_for(aspect: str, budget: int) -> tuple[int, int]:
    """Width and height for an aspect ratio at a fixed pixel budget, multiples of 16."""
    rw, rh = ASPECTS.get(aspect, (1, 1))
    w = math.sqrt(budget * budget * rw / rh)
    h = w * rh / rw
    return max(16, round(w / 16) * 16), max(16, round(h / 16) * 16)


def size_on_grid(aspect: str, budget: int, step: int) -> tuple[int, int]:
    """The size on a step-pixel grid that best keeps both the aspect ratio and the
    area (rounding each side alone can change the shape a lot)."""
    rw, rh = ASPECTS.get(aspect, (1, 1))
    target, area = rw / rh, budget * budget
    best = None
    for w in range(step, 4 * budget, step):
        h = max(step, round(w / target / step) * step)
        if abs(w * h - area) > area / 5:
            continue
        score = abs(w / h - target) / target + abs(w * h - area) / area / 2
        if best is None or score < best[0]:
            best = (score, (w, h))
    return best[1] if best else size_for(aspect, budget)


def stage_labels(graph: dict) -> dict[str, str]:
    out = {}
    for nid, node in graph.items():
        for stage, classes in STAGES.items():
            if node["class_type"] in classes:
                out[nid] = stage
    return out


def _v(params: dict, name: str, default):
    """A job's advanced control value, or the recipe's own when the job sets none.

    Every control falls back to exactly what the graph used before the control
    existed (tests/test_graph_parity.py holds the builders to that).
    """
    value = params.get(name)
    return default if value is None else value


def _budget(params: dict, default: int) -> int:
    """The pixel budget (side of the square of the same area) for a megapixels control."""
    mp = params.get("megapixels")
    return default if mp is None else round(1024 * math.sqrt(float(mp)))


def _add_loras(g: dict, model: list, loras, node: str, strength: str, prefix: str = "lora") -> list:
    """Chain the job's add-ons onto a model; returns the last model output."""
    for i, entry in enumerate(loras or (), 1):
        g[f"{prefix}{i}"] = {
            "class_type": node,
            "inputs": {
                "model": model,
                "lora_name": entry["file"],
                strength: float(entry["strength"]),
            },
        }
        model = [f"{prefix}{i}", 0]
    return model


def _init_latent(g: dict, init: str, budget: int, vae) -> list:
    """Image to image: the picture at the job's pixel budget, encoded as the starting latent."""
    g["init"] = {"class_type": "LoadImage", "inputs": {"image": init}}
    g["init_scale"] = {
        "class_type": "ImageScaleToTotalPixels",
        "inputs": {
            "image": ["init", 0],
            "upscale_method": "lanczos",
            "megapixels": round(budget * budget / (1024 * 1024), 2),
            "resolution_steps": 16,
        },
    }
    g["latent"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["init_scale", 0], "vae": vae}}
    return ["latent", 0]


def _denoise(params: dict) -> float:
    denoise = float(params["denoise"]) if params.get("denoise") is not None else 0.2
    if not 0.05 <= denoise <= 1.0:
        raise ValueError("Set denoise between 0.05 and 1.")
    return denoise


def _custom_sampler(
    g: dict,
    *,
    model,
    conditioning,
    latent,
    seed,
    steps,
    sampler,
    guider=None,
    denoise=1.0,
    scheduler="simple",
) -> str:
    """Add the RandomNoise → SamplerCustomAdvanced chain; returns the sampler node id."""
    g["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    g["sampler"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}}
    g["sigmas"] = {
        "class_type": "BasicScheduler",
        "inputs": {"model": model, "scheduler": scheduler, "steps": steps, "denoise": denoise},
    }
    if guider is None:
        g["guider"] = {
            "class_type": "BasicGuider",
            "inputs": {"model": model, "conditioning": conditioning},
        }
    else:
        g["guider"] = guider
    g["sample"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["noise", 0],
            "guider": ["guider", 0],
            "sampler": ["sampler", 0],
            "sigmas": ["sigmas", 0],
            "latent_image": latent,
        },
    }
    return "sample"


def _finish(g: dict, vae, prefix: str) -> str:
    g["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": vae}}
    g["save"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["decode", 0], "filename_prefix": prefix},
    }
    return "save"


def z_image(
    *,
    prompt,
    seed,
    aspect,
    names,
    settings,
    hw,
    prefix,
    native=False,
    params=None,
    images=None,
    **_,
):
    params = params or {}
    budget = _budget(params, 1024)
    w, h = size_for(aspect, budget)
    g: dict = {}
    if native:
        g["unet"] = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": names["zimage-bf16"], "weight_dtype": "default"},
        }
    else:
        dit = names.get("zimage-int4") or names.get("zimage-fp4")
        g["unet"] = {"class_type": "NunchakuZImageDiTLoader", "inputs": {"model_name": dit}}
    g["clip"] = {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": names["qwen3-4b"], "type": "lumina2", "device": "default"},
    }
    g["vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": names["flux-ae"]}}
    # Nunchaku's Z-Image patcher takes ComfyUI's own LoRA loader (plain LoRAs only).
    model = _add_loras(g, ["unet", 0], params.get("loras"), "LoraLoaderModelOnly", "strength_model")
    g["shift"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": model, "shift": float(_v(params, "shift", 3.0))},
    }
    g["text"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}}
    init = (images or {}).get("image1")
    if init:
        latent = _init_latent(g, init, budget, ["vae", 0])
    else:
        g["latent"] = {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        }
        latent = ["latent", 0]
    _custom_sampler(
        g,
        model=["shift", 0],
        conditioning=["text", 0],
        latent=latent,
        seed=seed,
        steps=int(_v(params, "steps", settings.get("steps", 9))),
        sampler=_v(params, "sampler", "res_multistep"),
        scheduler=_v(params, "scheduler", "simple"),
        denoise=_denoise(params) if init else 1.0,
    )
    return g, _finish(g, ["vae", 0], prefix)


def z_image_native(**kw):
    return z_image(native=True, **kw)


GUIDE_TYPES = ("canny", "depth", "pose", "as_is")


def z_image_control(*, prompt, seed, aspect, names, settings, hw, prefix, images, params=None, **_):
    """Z-Image Turbo steered by a picture through the Fun ControlNet (Union).

    The guide picture is scaled to the job's size and centre-cropped to its shape, so
    the result has the chosen aspect whatever the guide's. It becomes an edge map
    (ComfyUI's Canny, thresholds 0.1 and 0.32 as in Nunchaku's example), a depth map
    (Depth Anything 3 small), or a pose drawing (SDPose, whole body, as in ComfyUI's
    blueprint); "as_is" uses the picture itself as the map.
    """
    params = params or {}
    guide = (images or {}).get("guide")
    if not guide:
        raise ValueError("Add the guide picture.")
    kind = params.get("guide_type") or "canny"
    if kind not in GUIDE_TYPES:
        raise ValueError(f"Choose a guide type: {', '.join(GUIDE_TYPES)}.")
    w, h = size_for(aspect, _budget(params, 1024))
    dit = names.get("zimage-int4") or names.get("zimage-fp4")
    g: dict = {
        "unet": {"class_type": "NunchakuZImageDiTLoader", "inputs": {"model_name": dit}},
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": names["qwen3-4b"], "type": "lumina2", "device": "default"},
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["flux-ae"]}},
        "patch_file": {
            "class_type": "ModelPatchLoader",
            "inputs": {"name": names["zimage-fun-union"]},
        },
        "guide": {"class_type": "LoadImage", "inputs": {"image": guide}},
        "guide_fit": {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["guide", 0],
                "upscale_method": "lanczos",
                "width": w,
                "height": h,
                "crop": "center",
            },
        },
    }
    control = ["guide_fit", 0]
    if kind == "canny":
        g["map"] = {
            "class_type": "Canny",
            "inputs": {"image": control, "low_threshold": 0.1, "high_threshold": 0.32},
        }
        control = ["map", 0]
    elif kind == "depth":
        g["depth_model"] = {
            "class_type": "LoadDA3Model",
            "inputs": {"model_name": names["da3-small"], "weight_dtype": "default"},
        }
        g["depth_run"] = {
            "class_type": "DA3Inference",
            "inputs": {
                "da3_model": ["depth_model", 0],
                "image": control,
                "resolution": 504,
                "resize_method": "upper_bound_resize",
                "mode": "mono",
            },
        }
        g["map"] = {
            "class_type": "DA3Render",
            "inputs": {
                "da3_geometry": ["depth_run", 0],
                "output": "depth",
                "output.normalization": "v2_style",
                "output.apply_sky_clip": False,
            },
        }
        control = ["map", 0]
    elif kind == "pose":
        g["pose_model"] = {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": names["sdpose-wholebody"]},
        }
        g["pose_run"] = {
            "class_type": "SDPoseKeypointExtractor",
            "inputs": {
                "model": ["pose_model", 0],
                "vae": ["pose_model", 2],
                "image": control,
                "batch_size": 16,
            },
        }
        g["map"] = {
            "class_type": "SDPoseDrawKeypoints",
            "inputs": {
                "keypoints": ["pose_run", 0],
                "draw_body": True,
                "draw_hands": True,
                "draw_face": True,
                "draw_feet": True,
                "stick_width": 4,
                "face_point_size": 2,
                "score_threshold": 0.5,
                "draw_head": True,
            },
        }
        control = ["map", 0]
    model = _add_loras(g, ["unet", 0], params.get("loras"), "LoraLoaderModelOnly", "strength_model")
    g["control"] = {
        "class_type": "QwenImageDiffsynthControlnet",
        "inputs": {
            "model": model,
            "model_patch": ["patch_file", 0],
            "vae": ["vae", 0],
            "image": control,
            "strength": float(_v(params, "guide_strength", 0.75)),
        },
    }
    g["shift"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": ["control", 0], "shift": float(_v(params, "shift", 3.0))},
    }
    g["text"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}}
    g["latent"] = {
        "class_type": "EmptySD3LatentImage",
        "inputs": {"width": w, "height": h, "batch_size": 1},
    }
    _custom_sampler(
        g,
        model=["shift", 0],
        conditioning=["text", 0],
        latent=["latent", 0],
        seed=seed,
        steps=int(_v(params, "steps", settings.get("steps", 9))),
        sampler=_v(params, "sampler", "res_multistep"),
        scheduler=_v(params, "scheduler", "simple"),
    )
    return g, _finish(g, ["vae", 0], prefix)


# The largest Flux size measured on a 12 GB card: 1920 x 1088 peaked at 11.7 GB.
FLUX_MAX_AREA = 1920 * 1088


def _flux_size(params: dict) -> tuple[int, int] | None:
    """An explicit width and height for a Flux image, or None to use the default size."""
    if params.get("width") is None and params.get("height") is None:
        return None
    try:
        w, h = int(params.get("width") or 0) // 16 * 16, int(params.get("height") or 0) // 16 * 16
    except (TypeError, ValueError):
        w = h = 0
    if min(w, h) < 256 or w * h > FLUX_MAX_AREA:
        raise ValueError("Give a width and height of 256 or more, at most 1920 x 1088 in area.")
    return w, h


def flux(*, prompt, seed, aspect, names, settings, hw, prefix, images, params, **_):
    init = images.get("image1")
    explicit = _flux_size(params)
    if explicit:
        w, h = explicit
    elif init:
        # The picture keeps its own aspect at about one megapixel; only the area
        # reaches the shift below, so the square of the same area stands in.
        w = h = _budget(params, 1024)
    else:
        w, h = size_for(aspect, _budget(params, 1024))
    denoise = _denoise(params) if init else 1.0
    dit = next(v for k, v in names.items() if k.endswith(("-int4", "-fp4")))
    dtype = _dit_dtype(hw)
    g: dict = {
        "unet": {
            "class_type": "NunchakuFluxDiTLoader",
            "inputs": {
                "model_path": dit,
                "cache_threshold": 0,
                "attention": "nunchaku-fp16",
                "cpu_offload": "auto",
                "device_id": 0,
                "data_type": dtype,
                "i2f_mode": "enabled",
            },
        },
        "clip": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": names["clip-l"],
                "clip_name2": names["t5xxl-fp8"],
                "type": "flux",
                "device": "default",
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["flux-ae"]}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        },
    }
    model = _add_loras(
        g, ["unet", 0], params.get("loras"), "NunchakuFluxLoraLoader", "lora_strength"
    )
    conditioning = ["text", 0]
    if settings.get("guidance") is not None:
        g["guidance"] = {
            "class_type": "FluxGuidance",
            "inputs": {
                "conditioning": ["text", 0],
                "guidance": float(_v(params, "guidance", settings["guidance"])),
            },
        }
        conditioning = ["guidance", 0]
    if settings.get("shift"):
        g["shift"] = {
            "class_type": "ModelSamplingFlux",
            "inputs": {
                "model": model,
                "max_shift": float(_v(params, "max_shift", 1.15)),
                "base_shift": 0.5,
                "width": w,
                "height": h,
            },
        }
        model = ["shift", 0]
    if init:
        # Image to image: the picture is resized, encoded and re-noised to `denoise`.
        # A low denoise over the whole frame at a larger size redraws fine detail
        # (skin, hair) and keeps the people and the composition.
        g["init"] = {"class_type": "LoadImage", "inputs": {"image": init}}
        if explicit:
            g["init_scale"] = {
                "class_type": "ImageScale",
                "inputs": {
                    "image": ["init", 0],
                    "upscale_method": "lanczos",
                    "width": w,
                    "height": h,
                    "crop": "disabled",
                },
            }
        else:
            g["init_scale"] = {
                "class_type": "ImageScaleToTotalPixels",
                "inputs": {
                    "image": ["init", 0],
                    "upscale_method": "lanczos",
                    "megapixels": float(_v(params, "megapixels", 1.0)),
                    "resolution_steps": 16,
                },
            }
        g["latent"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["init_scale", 0], "vae": ["vae", 0]},
        }
    _custom_sampler(
        g,
        model=model,
        conditioning=conditioning,
        latent=["latent", 0],
        seed=seed,
        steps=int(_v(params, "steps", settings.get("steps", 4))),
        sampler=_v(params, "sampler", "euler"),
        scheduler=_v(params, "scheduler", "simple"),
        denoise=denoise,
    )
    return g, _finish(g, ["vae", 0], prefix)


# Qwen-Image's trained sizes (the official ComfyUI template's table).
QWEN_SIZES = {
    "1:1": (1328, 1328),
    "16:9": (1664, 928),
    "9:16": (928, 1664),
    "4:3": (1472, 1104),
    "3:4": (1104, 1472),
    "3:2": (1584, 1056),
    "2:3": (1056, 1584),
}


def qwen_image(*, prompt, seed, aspect, names, settings, hw, prefix, params=None, images=None, **_):
    params = params or {}
    budget = _budget(params, 1328)
    if params.get("trained_sizes") and aspect in QWEN_SIZES:
        w, h = QWEN_SIZES[aspect]
    else:
        w, h = size_for(aspect, budget)
    dit, _dtype = _nunchaku_dit(names, hw)
    g: dict = {
        "unet": {
            "class_type": "NunchakuQwenImageDiTLoader",
            "inputs": {
                "model_name": dit,
                "cpu_offload": "auto",
                "num_blocks_on_gpu": 1,
                "use_pin_memory": "disable",
            },
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": names["qwen25vl-fp8"],
                "type": "qwen_image",
                "device": "default",
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["qwen-image-vae"]}},
        "shift": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {
                "model": ["unet", 0],
                "shift": float(_v(params, "shift", settings.get("shift", 3.1))),
            },
        },
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        },
    }
    init = (images or {}).get("image1")
    latent = _init_latent(g, init, budget, ["vae", 0]) if init else ["latent", 0]
    cfg = float(settings.get("cfg", 2.5))
    # The distilled files run at cfg 1: no negative pass, so each step costs half.
    guider = (
        None
        if cfg <= 1.0
        else {
            "class_type": "CFGGuider",
            "inputs": {
                "model": ["shift", 0],
                "positive": ["text", 0],
                "negative": ["negative", 0],
                "cfg": cfg,
            },
        }
    )
    _custom_sampler(
        g,
        model=["shift", 0],
        conditioning=["text", 0],
        latent=latent,
        seed=seed,
        steps=int(_v(params, "steps", settings.get("steps", 20))),
        sampler=_v(params, "sampler", "euler"),
        scheduler=_v(params, "scheduler", "simple"),
        guider=guider,
        denoise=_denoise(params) if init else 1.0,
    )
    return g, _finish(g, ["vae", 0], prefix)


def krea2(*, prompt, seed, aspect, names, settings, hw, prefix, params=None, images=None, **_):
    """Krea 2 Turbo: ComfyUI's own 4-bit weights, eight steps at cfg 1, a zeroed negative."""
    params = params or {}
    budget = _budget(params, 1024)
    w, h = size_for(aspect, budget)
    g: dict = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": names["krea2-turbo-w4a4"], "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": names["krea2-text-encoder"],
                "type": "krea2",
                "device": "default",
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["qwen-image-vae"]}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["text", 0]}},
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["unet", 0],
                "positive": ["text", 0],
                "negative": ["negative", 0],
                "latent_image": ["latent", 0],
                "seed": seed,
                "steps": int(_v(params, "steps", settings.get("steps", 8))),
                "cfg": float(settings.get("cfg", 1.0)),
                "sampler_name": _v(params, "sampler", "euler"),
                "scheduler": _v(params, "scheduler", "simple"),
                "denoise": 1.0,
            },
        },
    }
    init = (images or {}).get("image1")
    if init:
        g["sample"]["inputs"]["latent_image"] = _init_latent(g, init, budget, ["vae", 0])
        g["sample"]["inputs"]["denoise"] = _denoise(params)
    return g, _finish(g, ["vae", 0], prefix)


def _dit_dtype(hw) -> str:
    """bfloat16 unless the card the engines run on is a Turing (RTX 20), which lacks it."""
    gpu = getattr(hw, "gpu", None)
    cap = gpu.compute_cap if gpu else max((g.compute_cap for g in hw.gpus), default=8.0)
    return "bfloat16" if cap >= 8.0 else "float16"


def _nunchaku_dit(names: dict, hw) -> tuple[str, str]:
    dit = next(v for k, v in names.items() if k.endswith(("-int4", "-fp4")))
    dtype = _dit_dtype(hw)
    return dit, dtype


def qwen_edit(*, prompt, seed, names, settings, hw, prefix, images, params=None, **_):
    """Instruction edit; up to three pictures, called picture 1-3 in the prompt.

    `keep_size` (off unless asked): the text encoder rescales its own reference to
    1024 x 1024 on an 8-pixel grid, which differs slightly from the picture being
    edited, so the result can drift by a few pixels. With keep_size the reference
    latents are the edited pictures themselves, on a 16-pixel grid, and the encoder
    adds none of its own.
    """
    params = params or {}
    # Changing only an area (a mask, or a box the engine drew as one) keeps everything
    # outside it exactly: the original pixels are pasted over the result, which needs
    # the picture and the result on one grid.
    region = images.get("mask")
    keep = bool(params.get("keep_size")) or bool(region)
    dit, _dtype = _nunchaku_dit(names, hw)
    g: dict = {
        "unet": {
            "class_type": "NunchakuQwenImageDiTLoader",
            "inputs": {
                "model_name": dit,
                "cpu_offload": "enable",
                "num_blocks_on_gpu": 20,
                "use_pin_memory": "disable",
            },
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": names["qwen25vl-fp8"],
                "type": "qwen_image",
                "device": "default",
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["qwen-image-vae"]}},
        "shift": {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": ["unet", 0], "shift": float(_v(params, "shift", 3.0))},
        },
        "norm": {"class_type": "CFGNorm", "inputs": {"model": ["shift", 0], "strength": 1.0}},
    }
    refs: dict = {}
    for slot in (1, 2, 3):
        name = images.get(f"image{slot}")
        if not name:
            continue
        g[f"load{slot}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        g[f"scale{slot}"] = {
            "class_type": "ImageScaleToTotalPixels",
            "inputs": {
                "image": [f"load{slot}", 0],
                "upscale_method": "lanczos",
                "megapixels": float(_v(params, "megapixels", 1.0)),
                "resolution_steps": 16 if keep else 1,
            },
        }
        refs[f"image{slot}"] = [f"scale{slot}", 0]
    if "image1" not in refs:
        raise ValueError("Add the picture to edit.")
    common = {"clip": ["clip", 0], **({} if keep else {"vae": ["vae", 0]}), **refs}
    g["text"] = {
        "class_type": "TextEncodeQwenImageEditPlus",
        "inputs": {**common, "prompt": prompt},
    }
    g["negative"] = {
        "class_type": "TextEncodeQwenImageEditPlus",
        "inputs": {**common, "prompt": ""},
    }
    g["encode"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": refs["image1"], "vae": ["vae", 0]},
    }
    positive, negative = ["text", 0], ["negative", 0]
    if keep:
        for slot in (1, 2, 3):
            if f"image{slot}" not in refs:
                continue
            latent = ["encode", 0]
            if slot > 1:
                g[f"encode{slot}"] = {
                    "class_type": "VAEEncode",
                    "inputs": {"pixels": refs[f"image{slot}"], "vae": ["vae", 0]},
                }
                latent = [f"encode{slot}", 0]
            for side, cond in (("pos", positive), ("neg", negative)):
                g[f"ref_{side}{slot}"] = {
                    "class_type": "ReferenceLatent",
                    "inputs": {"conditioning": cond, "latent": latent},
                }
            positive, negative = [f"ref_pos{slot}", 0], [f"ref_neg{slot}", 0]
    # A region edit samples the whole picture (pinning the outside latent at each of
    # Lightning's few steps held the edit back, measured 2026-10-13) and keeps only the
    # area, pasted with a soft edge over Picture 1 on the same grid.
    model, latent = ["norm", 0], ["encode", 0]
    soft = (
        _region_mask(g, region, refs["image1"], int(_v(params, "feather", 12))) if region else None
    )
    g["sample"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": model,
            "positive": positive,
            "negative": negative,
            "latent_image": latent,
            "seed": seed,
            "steps": int(_v(params, "steps", settings.get("steps", 4))),
            "cfg": float(settings.get("cfg", 1.0)),
            "sampler_name": _v(params, "sampler", "euler"),
            "scheduler": _v(params, "scheduler", "simple"),
            "denoise": 1.0,
        },
    }
    save = _finish(g, ["vae", 0], prefix)
    if region:
        g["paste"] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": refs["image1"],
                "source": ["decode", 0],
                "x": 0,
                "y": 0,
                "resize_source": False,
                "mask": soft,
            },
        }
        g["save"]["inputs"]["images"] = ["paste", 0]
    return g, save


def _region_mask(g: dict, mask: str, picture: list, feather: int) -> list:
    """The area to change, at the scaled picture's size, with a soft edge `feather` px wide."""
    g["mask_load"] = {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}}
    g["mask_size"] = {"class_type": "GetImageSize", "inputs": {"image": picture}}
    g["mask_img"] = {"class_type": "MaskToImage", "inputs": {"mask": ["mask_load", 0]}}
    g["mask_fit"] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": ["mask_img", 0],
            "upscale_method": "bilinear",
            "width": ["mask_size", 0],
            "height": ["mask_size", 1],
            "crop": "disabled",
        },
    }
    out = ["mask_fit", 0]
    if feather > 0:
        g["mask_blur"] = {
            "class_type": "ImageBlur",
            "inputs": {
                "image": out,
                "blur_radius": min(31, feather),
                "sigma": min(10.0, max(1.0, feather / 3)),
            },
        }
        out = ["mask_blur", 0]
    g["mask_soft"] = {"class_type": "ImageToMask", "inputs": {"image": out, "channel": "red"}}
    return ["mask_soft", 0]


def kontext(*, prompt, seed, names, settings, hw, prefix, images, params=None, **_):
    params = params or {}
    if not images.get("image1"):
        raise ValueError("Add the picture to edit.")
    if params.get("box") is not None or images.get("mask"):
        raise ValueError(
            "Changing only an area works with Qwen Edit; Kontext edits the whole picture."
        )
    dit, dtype = _nunchaku_dit(names, hw)
    g: dict = {
        "unet": {
            "class_type": "NunchakuFluxDiTLoader",
            "inputs": {
                "model_path": dit,
                "cache_threshold": 0,
                "attention": "nunchaku-fp16",
                "cpu_offload": "auto",
                "device_id": 0,
                "data_type": dtype,
                "i2f_mode": "enabled",
            },
        },
        "clip": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": names["clip-l"],
                "clip_name2": names["t5xxl-fp8"],
                "type": "flux",
                "device": "default",
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["flux-ae"]}},
        "load": {"class_type": "LoadImage", "inputs": {"image": images["image1"]}},
        "scale": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["load", 0]}},
        "encode": {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]},
        },
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "guidance": {
            "class_type": "FluxGuidance",
            "inputs": {
                "conditioning": ["text", 0],
                "guidance": float(_v(params, "guidance", settings.get("guidance", 2.5))),
            },
        },
        "reference": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["guidance", 0], "latent": ["encode", 0]},
        },
    }
    model = _add_loras(
        g, ["unet", 0], params.get("loras"), "NunchakuFluxLoraLoader", "lora_strength"
    )
    _custom_sampler(
        g,
        model=model,
        conditioning=["reference", 0],
        latent=["encode", 0],
        seed=seed,
        steps=int(_v(params, "steps", settings.get("steps", 8))),
        sampler=_v(params, "sampler", "euler"),
        scheduler=_v(params, "scheduler", "simple"),
    )
    return g, _finish(g, ["vae", 0], prefix)


def seedvr2(*, seed, names, prefix, images, params, **_):
    """Restoration upscale; `retain` upscales internally, then returns the original size."""
    if not images.get("image1"):
        raise ValueError("Add the picture to upscale.")
    scale = int(params.get("scale") or 2)
    if scale not in (2, 3, 4):
        raise ValueError("Choose 2x, 3x or 4x.")
    tiled = {
        "tile_size": int(_v(params, "tile", 512)),
        "overlap": 128,
        "temporal_size": 4096,
        "temporal_overlap": 8,
    }
    g: dict = {
        "load": {"class_type": "LoadImage", "inputs": {"image": images["image1"]}},
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {
                # the 3B model, or the 7B one when that is what was downloaded
                "unet_name": names.get("seedvr2-3b") or names["seedvr2-7b"],
                "weight_dtype": "default",
            },
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["seedvr2-vae"]}},
        "resize": {
            "class_type": "ResizeImageMaskNode",
            "inputs": {
                "input": ["load", 0],
                "resize_type": "scale by multiplier",
                "resize_type.multiplier": scale,
                "scale_method": "lanczos",
            },
        },
        "pre": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["resize", 0]}},
        "encode": {
            "class_type": "VAEEncodeTiled",
            "inputs": {"pixels": ["pre", 0], "vae": ["vae", 0], **tiled},
        },
        "cond": {
            "class_type": "SeedVR2Conditioning",
            "inputs": {"model": ["unet", 0], "vae_conditioning": ["encode", 0]},
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["unet", 0],
                "positive": ["cond", 0],
                "negative": ["cond", 1],
                "latent_image": ["encode", 0],
                "seed": seed,
                "steps": 1,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "decode": {
            "class_type": "VAEDecodeTiled",
            "inputs": {"samples": ["sample", 0], "vae": ["vae", 0], **tiled},
        },
        "post": {
            "class_type": "SeedVR2PostProcessing",
            "inputs": {
                "images": ["decode", 0],
                "original_resized_images": ["resize", 0],
                "color_correction_method": _v(params, "color", "lab"),
            },
        },
    }
    final = ["post", 0]
    if params.get("retain"):
        g["fit"] = {
            "class_type": "ResizeImageMaskNode",
            "inputs": {
                "input": ["post", 0],
                "resize_type": "match size",
                "resize_type.match": ["load", 0],
                "resize_type.crop": "disabled",
                "scale_method": "area",
            },
        }
        final = ["fit", 0]
    g["save"] = {"class_type": "SaveImage", "inputs": {"images": final, "filename_prefix": prefix}}
    return g, "save"


def seedvr2_video(*, seed, names, prefix, images, params, **_):
    """Restoration upscale of a whole clip with ComfyUI's SeedVR2 video nodes.

    Opt-in only: on generated clips it made faces look plastic, and on a macro shot
    it added grit and flicker, so a plain resize is often the better master.
    """
    if not images.get("video"):
        raise ValueError("Add the clip to upscale.")
    short_side = int(params.get("short_side") or 1080)
    if not 720 <= short_side <= 1080:
        raise ValueError("Set short_side between 720 and 1080.")
    tiled = {"tile_size": 512, "overlap": 128, "temporal_size": 32, "temporal_overlap": 8}
    g: dict = {
        "load": {"class_type": "LoadVideo", "inputs": {"file": images["video"]}},
        "parts": {"class_type": "GetVideoComponents", "inputs": {"video": ["load", 0]}},
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": names["seedvr2-3b"], "weight_dtype": "default"},
        },
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["seedvr2-vae"]}},
        "resize": {
            "class_type": "ResizeImageMaskNode",
            "inputs": {
                "input": ["parts", 0],
                "resize_type": "scale shorter dimension",
                "resize_type.shorter_size": short_side,
                "scale_method": "lanczos",
            },
        },
        # Video encoders want even sides; already-even frames pass through untouched.
        "even": {
            "class_type": "ResizeImageMaskNode",
            "inputs": {
                "input": ["resize", 0],
                "resize_type": "scale to multiple",
                "resize_type.multiple": 2,
                "scale_method": "lanczos",
            },
        },
        "pre": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["even", 0]}},
        "encode": {
            "class_type": "VAEEncodeTiled",
            "inputs": {"pixels": ["pre", 0], "vae": ["vae", 0], **tiled},
        },
        # The largest 4n + 1 chunk that fits in free memory; neighbouring chunks
        # overlap by 2 latent frames and are crossfaded.
        "chunk": {
            "class_type": "SeedVR2TemporalChunk",
            "inputs": {"latent": ["encode", 0], "temporal_overlap": 2, "chunking_mode": "auto"},
        },
        "cond": {
            "class_type": "SeedVR2Conditioning",
            "inputs": {"model": ["unet", 0], "vae_conditioning": ["chunk", 0]},
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["unet", 0],
                "positive": ["cond", 0],
                "negative": ["cond", 1],
                "latent_image": ["chunk", 0],
                "seed": seed,
                "steps": 1,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "merge": {
            "class_type": "SeedVR2TemporalMerge",
            "inputs": {"latents": ["sample", 0], "temporal_overlap": ["chunk", 1]},
        },
        "decode": {
            "class_type": "VAEDecodeTiled",
            "inputs": {"samples": ["merge", 0], "vae": ["vae", 0], **tiled},
        },
        "post": {
            "class_type": "SeedVR2PostProcessing",
            "inputs": {
                "images": ["decode", 0],
                "original_resized_images": ["even", 0],
                "color_correction_method": _v(params, "color", "lab"),
            },
        },
        "video": {
            "class_type": "CreateVideo",
            "inputs": {"images": ["post", 0], "fps": ["parts", 2], "audio": ["parts", 1]},
        },
        "save": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["video", 0],
                "filename_prefix": prefix,
                "format": "auto",
                "codec": "auto",
            },
        },
    }
    return g, "save"


# Pixel budgets: sqrt(480 x 832), sqrt(576 x 1024) and sqrt(720 x 1280).
VIDEO_TIERS = {"standard": 632, "large": 768, "720p": 960}
# 720p peaks at about 11.7 GB of a 12 GB card.
VIDEO_720P_MIN_MB = 12000


def wan22(*, prompt, seed, aspect, names, settings, hw, prefix, images, params, **_):
    """Wan 2.2 14B: two experts with a four-step distill LoRA, 4 steps split 2 + 2."""
    mode = settings.get("mode", "i2v")
    start, end = images.get("start"), images.get("end")
    if mode == "i2v" and not (start or end):
        raise ValueError("Add a first frame, a last frame, or both.")
    seconds = max(2, min(5, int(params.get("seconds") or 3)))
    frames = round((seconds * 16 - 1) / 4) * 4 + 1  # Wan needs 4n + 1 frames
    size = params.get("size") or "standard"
    if size not in VIDEO_TIERS:
        raise ValueError("Choose a size: standard, large or 720p.")
    if size == "720p" and hw.vram_mb < VIDEO_720P_MIN_MB:
        raise ValueError("The 720p size needs a graphics card with 12 GB of memory.")
    w, h = size_for(aspect, VIDEO_TIERS[size])
    # lightx2v's distill: a LoRA pair (Seko-V1 by default, or a dated later pair),
    # or experts with the distill merged in, which need no LoRA.
    distill = settings.get("distill")
    experts = settings.get("experts") or f"wan-{mode}"

    def expert(part: str) -> dict:
        name = names[f"{experts}-{part}"]
        if name.endswith(".gguf"):
            return {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": name}}
        return {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": name, "weight_dtype": "default"},
        }

    g: dict = {"unet_high": expert("high"), "unet_low": expert("low")}
    model_high, model_low = ["unet_high", 0], ["unet_low", 0]
    if distill != "merged":
        lora = f"wan-{mode}-lora-{distill}" if distill else f"wan-{mode}-lora"
        for part in ("high", "low"):
            g[f"lora_{part}"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": [f"unet_{part}", 0],
                    "lora_name": names[f"{lora}-{part}"],
                    "strength_model": float(_v(params, f"distill_{part}", 1.0)),
                },
            }
        model_high, model_low = ["lora_high", 0], ["lora_low", 0]
    # The person's own add-ons: the high-noise file on the high expert, the low on the low.
    for i, entry in enumerate(params.get("loras") or (), 1):
        for part in ("high", "low"):
            if not entry.get(part):
                continue
            g[f"xlora_{part}{i}"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": model_high if part == "high" else model_low,
                    "lora_name": entry[part],
                    "strength_model": float(entry["strength"]),
                },
            }
            if part == "high":
                model_high = [f"xlora_{part}{i}", 0]
            else:
                model_low = [f"xlora_{part}{i}", 0]
    g |= {
        "shift_high": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": model_high, "shift": 5.0},
        },
        "shift_low": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": model_low, "shift": 5.0},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": names["wan-umt5"], "type": "wan", "device": "default"},
        },
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["wan-vae"]}},
        "frames": {
            "class_type": "WanFirstLastFrameToVideo",
            "inputs": {
                "positive": ["text", 0],
                "negative": ["negative", 0],
                "vae": ["vae", 0],
                "width": w,
                "height": h,
                "length": frames,
                "batch_size": 1,
            },
        },
    }
    if start:
        g["start"] = {"class_type": "LoadImage", "inputs": {"image": start}}
        g["frames"]["inputs"]["start_image"] = ["start", 0]
    if end:
        g["end"] = {"class_type": "LoadImage", "inputs": {"image": end}}
        g["frames"]["inputs"]["end_image"] = ["end", 0]
    common = {
        "steps": 4,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "noise_seed": seed,
        "positive": ["frames", 0],
        "negative": ["frames", 1],
    }
    g["sample_high"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            **common,
            "model": ["shift_high", 0],
            "latent_image": ["frames", 2],
            "add_noise": "enable",
            "start_at_step": 0,
            "end_at_step": 2,
            "return_with_leftover_noise": "enable",
        },
    }
    g["sample"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            **common,
            "model": ["shift_low", 0],
            "latent_image": ["sample_high", 0],
            "add_noise": "disable",
            "start_at_step": 2,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
        },
    }
    g["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
    }
    _save_video(g, ["decode", 0], 16, params, names, prefix)
    return g, "save"


# The camera moves of WanCameraEmbedding, by the ids the interface and the API use.
CAMERA_MOVES = {
    "static": "Static",
    "pan_up": "Pan Up",
    "pan_down": "Pan Down",
    "pan_left": "Pan Left",
    "pan_right": "Pan Right",
    "zoom_in": "Zoom In",
    "zoom_out": "Zoom Out",
    "anticlockwise": "Anti Clockwise (ACW)",
    "clockwise": "ClockWise (CW)",
}


def wan22_camera(*, prompt, seed, aspect, names, settings, hw, prefix, images, params, **_):
    """Wan 2.2 Fun Camera 14B: a first frame and a camera move, with the four-step
    LoRA pair of ComfyUI's template (4 steps split 2 + 2, cfg 1, shift 8)."""
    start = images.get("start")
    if not start:
        raise ValueError("Add a first frame.")
    move = params.get("camera") or "static"
    if move not in CAMERA_MOVES:
        raise ValueError(f"Choose a camera move: {', '.join(CAMERA_MOVES)}.")
    seconds = max(2, min(5, int(params.get("seconds") or 3)))
    frames = round((seconds * 16 - 1) / 4) * 4 + 1  # Wan needs 4n + 1 frames
    size = params.get("size") or "standard"
    if size not in VIDEO_TIERS:
        raise ValueError("Choose a size: standard, large or 720p.")
    if size == "720p" and hw.vram_mb < VIDEO_720P_MIN_MB:
        raise ValueError("The 720p size needs a graphics card with 12 GB of memory.")
    w, h = size_for(aspect, VIDEO_TIERS[size])
    g: dict = {}
    model = {}
    for part in ("high", "low"):
        g[f"unet_{part}"] = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": names[f"wan-fun-camera-{part}"], "weight_dtype": "default"},
        }
        g[f"lora_{part}"] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": [f"unet_{part}", 0],
                "lora_name": names[f"wan-i2v-lora-v1-{part}"],
                "strength_model": 1.0,
            },
        }
        model[part] = [f"lora_{part}", 0]
    # The person's own add-ons: the high-noise file on the high expert, the low on the low.
    for i, entry in enumerate(params.get("loras") or (), 1):
        for part in ("high", "low"):
            if not entry.get(part):
                continue
            g[f"xlora_{part}{i}"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": model[part],
                    "lora_name": entry[part],
                    "strength_model": float(entry["strength"]),
                },
            }
            model[part] = [f"xlora_{part}{i}", 0]
    g |= {
        "shift_high": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": model["high"], "shift": 8.0},
        },
        "shift_low": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": model["low"], "shift": 8.0},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": names["wan-umt5"], "type": "wan", "device": "default"},
        },
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["wan-vae"]}},
        "start": {"class_type": "LoadImage", "inputs": {"image": start}},
        # The embedding must be made at the size the clip is made at.
        "camera": {
            "class_type": "WanCameraEmbedding",
            "inputs": {
                "camera_pose": CAMERA_MOVES[move],
                "width": w,
                "height": h,
                "length": frames,
                "speed": float(_v(params, "camera_speed", 1.0)),
            },
        },
        "frames": {
            "class_type": "WanCameraImageToVideo",
            "inputs": {
                "positive": ["text", 0],
                "negative": ["negative", 0],
                "vae": ["vae", 0],
                "width": w,
                "height": h,
                "length": frames,
                "batch_size": 1,
                "start_image": ["start", 0],
                "camera_conditions": ["camera", 0],
            },
        },
    }
    common = {
        "steps": 4,
        "cfg": 1.0,
        "sampler_name": "euler",
        "scheduler": "simple",
        "noise_seed": seed,
        "positive": ["frames", 0],
        "negative": ["frames", 1],
    }
    g["sample_high"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            **common,
            "model": ["shift_high", 0],
            "latent_image": ["frames", 2],
            "add_noise": "enable",
            "start_at_step": 0,
            "end_at_step": 2,
            "return_with_leftover_noise": "enable",
        },
    }
    g["sample"] = {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            **common,
            "model": ["shift_low", 0],
            "latent_image": ["sample_high", 0],
            "add_noise": "disable",
            "start_at_step": 2,
            "end_at_step": 10000,
            "return_with_leftover_noise": "disable",
        },
    }
    g["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
    }
    _save_video(g, ["decode", 0], 16, params, names, prefix)
    return g, "save"


def hunyuan15(*, prompt, seed, aspect, names, settings, hw, prefix, images, params, **_):
    """HunyuanVideo 1.5: the 480p step-distilled image-to-video model, 8 steps at
    cfg 1 and shift 7 (Tencent's table), at 24 fps. The 720p size adds Tencent's
    480p-to-720p super-resolution model: shift 2, 6 steps."""
    start = images.get("start")
    if not start:
        raise ValueError("Add a first frame.")
    seconds = max(2, min(5, int(params.get("seconds") or 3)))
    frames = round(seconds * 24 / 4) * 4 + 1
    size = params.get("size") or "standard"
    if size not in ("standard", "720p"):
        raise ValueError("Choose a size: standard or 720p.")
    if size == "720p" and hw.vram_mb < VIDEO_720P_MIN_MB:
        raise ValueError("The 720p size needs a graphics card with 12 GB of memory.")
    w, h = size_for(aspect, VIDEO_TIERS["standard"])
    g: dict = {
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": names["hy15-i2v-480-distill"], "weight_dtype": "default"},
        },
        "shift": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["unet", 0], "shift": 7.0}},
        "clip": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": names["qwen25vl-fp8"],
                "clip_name2": names["hy15-byt5"],
                "type": "hunyuan_video_15",
                "device": "default",
            },
        },
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": names["hy15-vae"]}},
        "start": {"class_type": "LoadImage", "inputs": {"image": start}},
        "vision": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": names["sigclip-384"]}},
        "seen": {
            "class_type": "CLIPVisionEncode",
            "inputs": {"clip_vision": ["vision", 0], "image": ["start", 0], "crop": "center"},
        },
        "frames": {
            "class_type": "HunyuanVideo15ImageToVideo",
            "inputs": {
                "positive": ["text", 0],
                "negative": ["negative", 0],
                "vae": ["vae", 0],
                "width": w,
                "height": h,
                "length": frames,
                "batch_size": 1,
                "start_image": ["start", 0],
                "clip_vision_output": ["seen", 0],
            },
        },
        "sample": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["shift", 0],
                "seed": seed,
                "steps": int(_v(params, "steps", 8)),
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["frames", 0],
                "negative": ["frames", 1],
                "latent_image": ["frames", 2],
                "denoise": 1.0,
            },
        },
    }
    latent = ["sample", 0]
    if size == "720p":
        w2, h2 = size_for(aspect, VIDEO_TIERS["720p"])
        g |= {
            "sr_unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": names["hy15-sr-720"], "weight_dtype": "default"},
            },
            "sr_shift": {
                "class_type": "ModelSamplingSD3",
                "inputs": {"model": ["sr_unet", 0], "shift": 2.0},
            },
            "upsampler": {
                "class_type": "LatentUpscaleModelLoader",
                "inputs": {"model_name": names["hy15-upsampler-720"]},
            },
            "upscaled": {
                "class_type": "HunyuanVideo15LatentUpscaleWithModel",
                "inputs": {
                    "model": ["upsampler", 0],
                    "samples": ["sample", 0],
                    "upscale_method": "bilinear",
                    "width": w2,
                    "height": h2,
                    "crop": "disabled",
                },
            },
            "sr": {
                "class_type": "HunyuanVideo15SuperResolution",
                "inputs": {
                    "positive": ["text", 0],
                    "negative": ["negative", 0],
                    "vae": ["vae", 0],
                    "latent": ["upscaled", 0],
                    "start_image": ["start", 0],
                    "clip_vision_output": ["seen", 0],
                    "noise_augmentation": float(_v(params, "sr_noise", 0.7)),
                },
            },
            "sample_sr": {
                "class_type": "KSampler",
                "inputs": {
                    "model": ["sr_shift", 0],
                    "seed": seed,
                    "steps": 6,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "simple",
                    "positive": ["sr", 0],
                    "negative": ["sr", 1],
                    "latent_image": ["sr", 2],
                    "denoise": 1.0,
                },
            },
        }
        latent = ["sample_sr", 0]
    g["decode"] = {
        "class_type": "VAEDecodeTiled",
        "inputs": {
            "samples": latent,
            "vae": ["vae", 0],
            "tile_size": 512,
            "overlap": 64,
            "temporal_size": 64,
            "temporal_overlap": 8,
        },
    }
    _save_video(g, ["decode", 0], 24, params, names, prefix)
    return g, "save"


LTX_STAGE1_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
LTX_STAGE2_SIGMAS = "0.85, 0.7250, 0.4219, 0.0"


def ltx(*, prompt, seed, aspect, names, settings, hw, prefix, images, params, **_):
    """LTX-2.x distilled: picture and sound in one pass, as in ComfyUI's LTX-2.5
    templates. Eight steps at half size, a 2x latent upscale, three refining
    steps, 24 fps. A first frame is written into the latent; a last frame is a
    guide that is cropped off again before the upscale."""
    start, end = images.get("start"), images.get("end")
    seconds = max(2, min(int(settings.get("max_seconds", 5)), int(params.get("seconds") or 3)))
    frames = seconds * 24 + 1  # LTX needs 8n + 1 frames
    size = params.get("size") or "standard"
    if size not in VIDEO_TIERS:
        raise ValueError("Choose a size: standard, large or 720p.")
    if size == "720p" and hw.vram_mb < VIDEO_720P_MIN_MB:
        raise ValueError("The 720p size needs a graphics card with 12 GB of memory.")
    # Half size must be a multiple of 32, so the clip is a multiple of 64.
    w, h = size_on_grid(aspect, VIDEO_TIERS[size], 64)
    part = {role: names[fid] for role, fid in settings["parts"].items()}
    if settings.get("gguf"):
        unet = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": part["unet"]}}
    else:
        unet = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": part["unet"], "weight_dtype": "default"},
        }
    if "connectors" in part:  # a GGUF Gemma plus LTX's own connector weights
        clip = {
            "class_type": "DualCLIPLoaderGGUF",
            "inputs": {
                "clip_name1": part["text_encoder"],
                "clip_name2": part["connectors"],
                "type": "ltxv",
            },
        }
    else:
        clip = {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": part["text_encoder"], "type": "ltxv", "device": "default"},
        }
    sampler = _v(params, "sampler", settings.get("sampler", "euler"))
    g: dict = {
        "unet": unet,
        "clip": clip,
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": part["vae"]}},
        "audio_vae": {"class_type": "VAELoader", "inputs": {"vae_name": part["audio_vae"]}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
        "cond": {
            "class_type": "LTXVConditioning",
            "inputs": {"positive": ["text", 0], "negative": ["negative", 0], "frame_rate": 24.0},
        },
        "empty": {
            "class_type": "EmptyLTXVLatentVideo",
            "inputs": {"width": w // 2, "height": h // 2, "length": frames, "batch_size": 1},
        },
        "empty_audio": {
            "class_type": "LTXVEmptyLatentAudio",
            "inputs": {
                "audio_vae": ["audio_vae", 0],
                "frames_number": frames,
                "frame_rate": 24,
                "batch_size": 1,
            },
        },
        "sampler1": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}},
        "sampler2": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}},
        "sigmas1": {"class_type": "ManualSigmas", "inputs": {"sigmas": LTX_STAGE1_SIGMAS}},
        "sigmas2": {"class_type": "ManualSigmas", "inputs": {"sigmas": LTX_STAGE2_SIGMAS}},
        "noise1": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "noise2": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed + 1}},
        "upscaler": {
            "class_type": "LatentUpscaleModelLoader",
            "inputs": {"model_name": part["upscaler"]},
        },
    }

    def frame(key: str, image: str) -> list:
        """A picture cropped to the clip's shape and given LTX's compression."""
        g[f"{key}_load"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        g[f"{key}_fit"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": [f"{key}_load", 0],
                "upscale_method": "lanczos",
                "width": w,
                "height": h,
                "crop": "center",
            },
        }
        g[key] = {
            "class_type": "LTXVPreprocess",
            "inputs": {
                "image": [f"{key}_fit", 0],
                "img_compression": int(_v(params, "motion", 18)),
            },
        }
        return [key, 0]

    positive, negative, latent = ["cond", 0], ["cond", 1], ["empty", 0]
    first = frame("first", start) if start else None
    if first:
        g["first_in"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["vae", 0],
                "image": first,
                "latent": latent,
                "strength": float(_v(params, "start_strength", 0.7)),
                "bypass": False,
            },
        }
        latent = ["first_in", 0]
    if end:
        g["last_in"] = {
            "class_type": "LTXVAddGuide",
            "inputs": {
                "positive": positive,
                "negative": negative,
                "vae": ["vae", 0],
                "latent": latent,
                "image": frame("last", end),
                "frame_idx": -1,
                "strength": float(_v(params, "end_strength", 0.7)),
            },
        }
        positive, negative, latent = ["last_in", 0], ["last_in", 1], ["last_in", 2]
    g["av1"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": latent, "audio_latent": ["empty_audio", 0]},
    }
    g["guider1"] = {
        "class_type": "CFGGuider",
        "inputs": {"model": ["unet", 0], "positive": positive, "negative": negative, "cfg": 1.0},
    }
    g["sample1"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["noise1", 0],
            "guider": ["guider1", 0],
            "sampler": ["sampler1", 0],
            "sigmas": ["sigmas1", 0],
            "latent_image": ["av1", 0],
        },
    }
    g["split1"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sample1", 0]}}
    video = ["split1", 0]
    if end:
        g["crop1"] = {
            "class_type": "LTXVCropGuides",
            "inputs": {"positive": positive, "negative": negative, "latent": video},
        }
        positive, negative, video = ["crop1", 0], ["crop1", 1], ["crop1", 2]
    g["upscaled"] = {
        "class_type": "LTXVLatentUpsampler",
        "inputs": {"samples": video, "upscale_model": ["upscaler", 0], "vae": ["vae", 0]},
    }
    video = ["upscaled", 0]
    if first:
        g["first_in2"] = {
            "class_type": "LTXVImgToVideoInplace",
            "inputs": {
                "vae": ["vae", 0],
                "image": first,
                "latent": video,
                "strength": 1.0,
                "bypass": False,
            },
        }
        video = ["first_in2", 0]
    g["av2"] = {
        "class_type": "LTXVConcatAVLatent",
        "inputs": {"video_latent": video, "audio_latent": ["split1", 1]},
    }
    g["guider2"] = {
        "class_type": "CFGGuider",
        "inputs": {"model": ["unet", 0], "positive": positive, "negative": negative, "cfg": 1.0},
    }
    g["sample"] = {
        "class_type": "SamplerCustomAdvanced",
        "inputs": {
            "noise": ["noise2", 0],
            "guider": ["guider2", 0],
            "sampler": ["sampler2", 0],
            "sigmas": ["sigmas2", 0],
            "latent_image": ["av2", 0],
        },
    }
    g["split2"] = {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["sample", 0]}}
    g["decode"] = {
        "class_type": "VAEDecodeTiled",
        "inputs": {
            "samples": ["split2", 0],
            "vae": ["vae", 0],
            "tile_size": 512,
            "overlap": 64,
            "temporal_size": 64,
            "temporal_overlap": 16,
        },
    }
    audio = None
    if _v(params, "sound", True):
        g["sound"] = {
            "class_type": "LTXVAudioVAEDecode",
            "inputs": {"samples": ["split2", 1], "audio_vae": ["audio_vae", 0]},
        }
        audio = ["sound", 0]
    _save_video(g, ["decode", 0], 24, params, names, prefix, audio=audio)
    return g, "save"


def _save_video(g: dict, frames, fps: int, params: dict, names: dict, prefix: str, audio=None):
    """Optional FILM interpolation (2x unless asked for more), then the clip, with its sound."""
    if params.get("smooth"):
        times = int(_v(params, "interpolation", 2))
        g["interp_model"] = {
            "class_type": "FrameInterpolationModelLoader",
            "inputs": {"model_name": names["film"]},
        }
        g["interp"] = {
            "class_type": "FrameInterpolate",
            "inputs": {
                "interp_model": ["interp_model", 0],
                "images": frames,
                "multiplier": times,
            },
        }
        frames, fps = ["interp", 0], fps * times
    g["video"] = {"class_type": "CreateVideo", "inputs": {"images": frames, "fps": fps}}
    if audio:
        g["video"]["inputs"]["audio"] = audio
    g["save"] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["video", 0],
            "filename_prefix": prefix,
            "format": "auto",
            "codec": "auto",
        },
    }


# --- 3D ----------------------------------------------------------------------
#
# TRELLIS.2 and Pixal3D on ComfyUI's own nodes, set up as the upstream pipelines
# run them (pipeline.json of microsoft/TRELLIS.2-4B and TencentARC/Pixal3D),
# not as ComfyUI's template does. All stages use 12 Euler steps; guidance applies
# only above sigma 0.6 (CFGOverride percent 0.667 at the models' shift 3); the
# sparse structure stage samples at shift 5. Finishing uses the solid remesh of
# ComfyUI PR 16805 (patches/), which leaves one closed shell.

DETAIL = {"standard": 1024, "high": 1536}
# Triangles in the finished model. Textures are unwrapped and baked after the
# mesh is simplified, with the normal map taken from the full-detail surface, so
# a light model keeps its fine detail in the normal map.
FACES = (500_000, 100_000, 20_000)
# Remesh "fill": the cost of closing an opening. 20 closes small holes and the
# mouth of a cup or vase; 1000 keeps openings.
FILL = {"close": 20.0, "keep": 1000.0}


def _cutout(g: dict, key: str, image: str, bg_model: str) -> tuple[list, list]:
    """The picture and its object mask (1 = object): its own transparency when it
    has some, otherwise BiRefNet's cut-out."""
    g[f"load_{key}"] = {"class_type": "LoadImage", "inputs": {"image": image}}
    g[f"alpha_{key}"] = {"class_type": "InvertMask", "inputs": {"mask": [f"load_{key}", 1]}}
    g[f"cut_{key}"] = {
        "class_type": "RemoveBackground",
        "inputs": {"bg_removal_model": [bg_model, 0], "image": [f"load_{key}", 0]},
    }
    g[f"mask_{key}"] = {
        "class_type": "IrisEchoChooseMask",
        "inputs": {"alpha": [f"alpha_{key}", 0], "cutout": [f"cut_{key}", 0]},
    }
    return [f"load_{key}", 0], [f"mask_{key}", 0]


def _bg_model(g: dict, names: dict) -> str:
    g["bg_model"] = {
        "class_type": "LoadBackgroundRemovalModel",
        "inputs": {"bg_removal_name": names["birefnet"]},
    }
    return "bg_model"


def _trellis_core(g: dict, *, unet: str, cond, seed: int, names: dict, params: dict, prefix: str):
    """Structure, shape and texture stages, then a solid printable mesh and a thumbnail."""
    # "resolution" is set by the engine when it steps down from High after running out of memory
    res = int(params.get("resolution") or DETAIL.get(params.get("detail", "standard"), 1024))
    textures = params.get("textures", True)
    if params.get("triangles") is not None:  # the advanced count replaces the three presets
        faces = int(params["triangles"])
        if not 5_000 <= faces <= 1_000_000:
            raise ValueError("triangles must be between 5000 and 1000000.")
    else:
        faces = int(params.get("faces") or FACES[0])
        if faces not in FACES:
            raise ValueError(f"faces must be one of {', '.join(map(str, FACES))}.")
    steps = int(_v(params, "steps", 12))
    guidance = float(_v(params, "guidance", 7.5))
    texture_size = int(_v(params, "texture_size", 2048))
    g["shape_vae"] = {
        "class_type": "VAELoader",
        "inputs": {"vae_name": names["trellis2-shape-vae"]},
    }

    def guided(name: str, model, rescale: float, shift: float | None):
        g[f"{name}_window"] = {
            "class_type": "CFGOverride",
            "inputs": {"model": model, "cfg": 1.0, "start_percent": 0.667, "end_percent": 1.0},
        }
        g[f"{name}_rescale"] = {
            "class_type": "RescaleCFG",
            "inputs": {"model": [f"{name}_window", 0], "multiplier": rescale},
        }
        if shift is None:
            return [f"{name}_rescale", 0]
        g[f"{name}_shift"] = {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": [f"{name}_rescale", 0], "shift": shift},
        }
        return [f"{name}_shift", 0]

    def ksampler(name: str, model, pos, neg, latent, cfg: float):
        g[name] = {
            "class_type": "KSampler",
            "inputs": {
                "model": model,
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "positive": pos,
                "negative": neg,
                "latent_image": latent,
                "denoise": 1.0,
            },
        }

    ss_model = guided("ss", [unet, 0], 0.7, 5.0)
    shape_model = guided("shape", [unet, 0], 0.5, None)
    g["empty"] = {"class_type": "EmptyTrellis2LatentStructure", "inputs": {"batch_size": 1}}
    ksampler("sample_structure", ss_model, [cond, 0], [cond, 1], ["empty", 0], guidance)
    g["structure"] = {
        "class_type": "VaeDecodeStructureTrellis2",
        "inputs": {"samples": ["sample_structure", 0], "vae": ["shape_vae", 0], "resolution": "32"},
    }
    g["shape_stage"] = {
        "class_type": "Trellis2ShapeStage",
        "inputs": {"positive": [cond, 0], "negative": [cond, 1], "voxel": ["structure", 0]},
    }
    ksampler(
        "sample_shape",
        shape_model,
        ["shape_stage", 0],
        ["shape_stage", 1],
        ["shape_stage", 2],
        guidance,
    )
    g["upsample"] = {
        "class_type": "Trellis2UpsampleStage",
        "inputs": {
            "positive": ["shape_stage", 0],
            "negative": ["shape_stage", 1],
            "shape_latent": ["sample_shape", 0],
            "vae": ["shape_vae", 0],
            "target_resolution": res,
        },
    }
    ksampler(
        "sample_detail",
        shape_model,
        ["upsample", 0],
        ["upsample", 1],
        ["upsample", 2],
        guidance,
    )
    g["shape"] = {
        "class_type": "VaeDecodeShapeTrellis",
        "inputs": {"samples": ["sample_detail", 0], "vae": ["shape_vae", 0]},
    }
    fill = FILL.get(params.get("openings", "close"), FILL["close"])
    g["remesh"] = {
        "class_type": "RemeshMesh",
        "inputs": {
            "mesh": ["shape", 0],
            "resolution": res,
            "sign_mode": "solid",
            "sign_mode.fill": fill,
            "sign_mode.qef": False,
            "band": 1.0,
            "project_back": 0.0,
            "fix_poles": False,
            "smooth_iters": int(_v(params, "smoothing", 3)),
            "drop_small_components": float(_v(params, "keep_small", 0.01)),
            "precluster_max_verts": 20_000_000,
        },
    }
    g["decimate"] = {
        "class_type": "DecimateMesh",
        "inputs": {
            "mesh": ["remesh", 0],
            "target_face_count": faces,
            "placement_mode": "midpoint",
        },
    }
    g["smooth"] = {
        "class_type": "MeshSmoothNormals",
        "inputs": {"mesh": ["decimate", 0], "crease_angle": float(_v(params, "crease", 180.0))},
    }
    final = ["smooth", 0]
    if textures:
        g["tex_vae"] = {
            "class_type": "VAELoader",
            "inputs": {"vae_name": names["trellis2-texture-vae"]},
        }
        g["texture_stage"] = {
            "class_type": "Trellis2TextureStage",
            "inputs": {
                "positive": ["upsample", 0],
                "negative": ["upsample", 1],
                "shape_latent": ["sample_detail", 0],
            },
        }
        ksampler(
            "sample_texture",
            [unet, 0],
            ["texture_stage", 0],
            ["texture_stage", 1],
            ["texture_stage", 2],
            1.0,
        )
        g["colors"] = {
            "class_type": "VaeDecodeTextureTrellis",
            "inputs": {
                "samples": ["sample_texture", 0],
                "vae": ["tex_vae", 0],
                "shape_subdivides": ["shape", 1],
            },
        }
        g["unwrap"] = {
            "class_type": "UnwrapMesh",
            "inputs": {
                "mesh": final,
                "segmenter": "pec",
                "resolution": texture_size,
                "padding": 1,
                "weld_distance": 0.0002,
            },
        }
        g["bake"] = {
            "class_type": "BakeTextureFromVoxel",
            "inputs": {
                "mesh": ["unwrap", 0],
                "voxel_colors": ["colors", 0],
                "texture_size": texture_size,
                "reference_mesh": ["shape", 0],
            },
        }
        g["bake_normal"] = {
            "class_type": "BakeNormalMapFromMesh",
            "inputs": {
                "low_poly": ["unwrap", 0],
                "high_poly": ["remesh", 0],
                "resolution": texture_size,
                "cage_distance": 0.05,
                "ignore_backfaces": True,
            },
        }
        g["bake_ao"] = {
            "class_type": "BakeAmbientOcclusion",
            "inputs": {
                "low_poly": ["unwrap", 0],
                "high_poly": ["remesh", 0],
                "resolution": 1024,
                "samples": 64,
                "max_distance": 0.71,
                "strength": 1.0,
                "bias": 0.01,
            },
        }
        g["textured"] = {
            "class_type": "ApplyTextureToMesh",
            "inputs": {
                "mesh": ["unwrap", 0],
                "base_color": ["bake", 0],
                "metallic": ["bake", 1],
                "roughness": ["bake", 2],
                "occlusion": ["bake_ao", 0],
                "normal_map": ["bake_normal", 0],
            },
        }
        final = ["textured", 0]
    g["save"] = {"class_type": "SaveGLB", "inputs": {"mesh": final, "filename_prefix": prefix}}
    # Thumbnail: a three-quarter view from a little above. RenderMesh's texture mode
    # is unlit, so it is lit with the shaded clay render of the same view.
    g["camera"] = {
        "class_type": "CreateCameraInfo",
        "inputs": {
            "mode": "orbit",
            "mode.yaw": 35.0,
            "mode.pitch": 20.0,
            "mode.distance": 2.2,
            "target_x": 0.0,
            "target_y": 0.0,
            "target_z": 0.0,
            "roll": 0.0,
            "fov": 35.0,
            "zoom": 1.0,
            "camera_type": "perspective",
        },
    }
    view = {"width": 768, "height": 768, "background": "#000000", "camera_info": ["camera", 0]}
    g["render"] = {"class_type": "RenderMesh", "inputs": {"mesh": final, "mode": "solid", **view}}
    shaded = ["render", 0]
    if textures:
        g["render_color"] = {
            "class_type": "RenderMesh",
            "inputs": {"mesh": final, "mode": "texture", **view},
        }
        g["shade"] = {
            "class_type": "IrisEchoShade",
            "inputs": {"color": ["render_color", 0], "clay": ["render", 0]},
        }
        shaded = ["shade", 0]
    g["thumb_rgba"] = {
        "class_type": "IrisEchoRGBA",
        "inputs": {"image": shaded, "mask": ["render", 1]},
    }
    g["thumb"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["thumb_rgba", 0], "filename_prefix": f"{prefix}-thumb"},
    }
    return "save"


def trellis2(*, seed, names, prefix, images, params, **_):
    """One picture, any angle; TRELLIS.2 builds in its own upright frame."""
    if not images.get("front"):
        raise ValueError("Add a picture of the object.")
    g: dict = {}
    bg = _bg_model(g, names)
    image, mask = _cutout(g, "front", images["front"], bg)
    g["crop"] = {
        "class_type": "ImageCropToMask",
        "inputs": {
            "images": image,
            "masks": mask,
            "width": 1024,
            "height": 1024,
            "pad_factor": 1.0,  # TRELLIS.2 crops tight; Pixal3D pads 1.1
            "grow_mask": 0,
            "background": "#000000",
        },
    }
    g["unet"] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": names["trellis2-int8"], "weight_dtype": "default"},
    }
    g["dino"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": names["dinov3-naf"]}}
    g["cond"] = {
        "class_type": "Trellis2Conditioning",
        "inputs": {"clip_vision_model": ["dino", 0], "image": ["crop", 0]},
    }
    save = _trellis_core(
        g, unet="unet", cond="cond", seed=seed, names=names, params=params, prefix=prefix
    )
    return g, save


def pixal3d_mv(*, seed, names, prefix, images, params, **_):
    """A front and a back taken at eye level, framed as Pixal3D's rig expects."""
    if not images.get("front") or not images.get("back"):
        raise ValueError("Pixal3D needs a front and a back picture.")
    g: dict = {}
    bg = _bg_model(g, names)
    front, front_mask = _cutout(g, "front", images["front"], bg)
    back, back_mask = _cutout(g, "back", images["back"], bg)
    g["frame"] = {
        "class_type": "IrisEchoFrameViews",
        "inputs": {"front": front, "front_mask": front_mask, "back": back, "back_mask": back_mask},
    }
    g["unet"] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": names["pixal3d-mv-int8"], "weight_dtype": "default"},
    }
    g["dino"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": names["dinov3-naf"]}}
    g["cond"] = {
        "class_type": "Pixal3DMultiViewConditioning",
        "inputs": {
            "clip_vision_model": ["dino", 0],
            "fov": 20.0,  # the upstream rig
            "front": ["frame", 0],
            "back": ["frame", 1],
        },
    }
    save = _trellis_core(
        g, unet="unet", cond="cond", seed=seed, names=names, params=params, prefix=prefix
    )
    return g, save


def pixal3d_single(*, seed, names, prefix, images, params, **_):
    """Pixal3D from one picture, as ComfyUI's template builds it: the object is cut out
    and padded 1.1, MoGe-2 estimates the camera's field of view from that crop, and
    Pixal3D conditions on both. Finishing is the same as the other 3D builders."""
    if not images.get("front"):
        raise ValueError("Add a picture of the object.")
    g: dict = {}
    bg = _bg_model(g, names)
    image, mask = _cutout(g, "front", images["front"], bg)
    g["crop"] = {
        "class_type": "ImageCropToMask",
        "inputs": {
            "images": image,
            "masks": mask,
            "width": 1024,
            "height": 1024,
            "pad_factor": 1.1,
            "grow_mask": 0,
            "background": "#000000",
        },
    }
    g["moge"] = {
        "class_type": "LoadMoGeModel",
        "inputs": {"model_name": names["moge2-vitl-normal"]},
    }
    g["geometry"] = {
        "class_type": "MoGeInference",
        "inputs": {
            "moge_model": ["moge", 0],
            "image": ["crop", 0],
            "resolution_level": 9,
            "fov_x_degrees": 0.0,  # recover it from the picture
            "batch_size": 4,
            "force_projection": True,
            "apply_mask": True,
            "refine_steps": 3,  # MoGe-3 only; the node's default
        },
    }
    g["fov"] = {
        "class_type": "MoGeGeometryToFOV",
        "inputs": {"moge_geometry": ["geometry", 0], "axis": "horizontal", "unit": "degrees"},
    }
    g["unet"] = {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": names["pixal3d-int8"], "weight_dtype": "default"},
    }
    g["dino"] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": names["dinov3-naf"]}}
    g["cond"] = {
        "class_type": "Pixal3DConditioning",
        "inputs": {
            "clip_vision_model": ["dino", 0],
            "image": ["crop", 0],
            "camera_angle_x": ["fov", 0],
        },
    }
    save = _trellis_core(
        g, unet="unet", cond="cond", seed=seed, names=names, params=params, prefix=prefix
    )
    return g, save


# The views helper runs several of these in a row (ComfyEngine._run_views).
# Asking for a straight-on front view returns a picture that already is one almost
# unchanged, and changes one taken from above. (Asking only to "lower the camera"
# also turned already level objects by 30-40 degrees, so it cannot tell them apart.)
LEVEL_PROMPT = (
    "Show this exact object from directly in front, with the camera at the object's mid-height "
    "looking straight at it: a straight-on front view with no downward or sideways angle. Keep "
    "exactly the same object with the same shape, proportions, colours and details. The whole "
    "object is visible and centered with space around it, on the same plain light grey "
    "background with soft even lighting."
)
# Two wordings: "behind" is read as the object's own back (wrong for a side view),
# "half a circle" sometimes stops at a quarter (wrong for a front view). The
# mirror check picks whichever really is the opposite side.
BACK_PROMPTS = (
    "Turn the camera around to the other side of this object and show it from directly behind, "
    "exactly opposite to this view. Keep exactly the same object with the same colours and "
    "details, the same size and position in the frame, the camera at the same eye level and "
    "distance, the same soft lighting and the same plain light grey background.",
    "Move the camera half a circle (180 degrees) around the object to the opposite side, so it "
    "sees the side that is hidden in this picture. The camera stays at the same eye level and "
    "the same distance. Keep exactly the same object with the same colours and details, the same "
    "size and position in the frame, the same soft lighting and the same plain light grey "
    "background.",
)
MIRROR_OK = 0.85  # correct opposite views scored 0.76-0.99, wrong ones 0.40-0.75
# Similarity of a picture and its front-view edit: level pictures 0.955-0.99 (one seed
# 0.77), pictures from above 0.54-0.91. A miss sends a level picture to TRELLIS.2, the safe side.
LEVEL_SAME = 0.93


def _qwen_edit_nodes(g: dict, names: dict, image, prompt: str, seed: int) -> list:
    dit = names.get("qwen-edit-8step-int4") or names["qwen-edit-8step-fp4"]
    g["q_unet"] = {
        "class_type": "NunchakuQwenImageDiTLoader",
        "inputs": {
            "model_name": dit,
            "cpu_offload": "enable",
            "num_blocks_on_gpu": 20,
            "use_pin_memory": "disable",
        },
    }
    g["q_clip"] = {
        "class_type": "CLIPLoader",
        "inputs": {"clip_name": names["qwen25vl-fp8"], "type": "qwen_image", "device": "default"},
    }
    g["q_vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": names["qwen-image-vae"]}}
    g["q_shift"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": ["q_unet", 0], "shift": 3.0},
    }
    g["q_norm"] = {"class_type": "CFGNorm", "inputs": {"model": ["q_shift", 0], "strength": 1.0}}
    g["q_scale"] = {
        "class_type": "ImageScaleToTotalPixels",
        "inputs": {
            "image": image,
            "upscale_method": "lanczos",
            "megapixels": 1.0,
            "resolution_steps": 1,
        },
    }
    common = {"clip": ["q_clip", 0], "vae": ["q_vae", 0], "image1": ["q_scale", 0]}
    g["q_text"] = {
        "class_type": "TextEncodeQwenImageEditPlus",
        "inputs": {**common, "prompt": prompt},
    }
    g["q_negative"] = {
        "class_type": "TextEncodeQwenImageEditPlus",
        "inputs": {**common, "prompt": ""},
    }
    g["q_encode"] = {
        "class_type": "VAEEncode",
        "inputs": {"pixels": ["q_scale", 0], "vae": ["q_vae", 0]},
    }
    g["sample"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["q_norm", 0],
            "positive": ["q_text", 0],
            "negative": ["q_negative", 0],
            "latent_image": ["q_encode", 0],
            "seed": seed,
            "steps": 8,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
        },
    }
    g["decode"] = {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["sample", 0], "vae": ["q_vae", 0]},
    }
    return ["decode", 0]


def views_level(*, names, image: str, seed: int, prefix: str):
    """An eye-level version of the picture, and how much it differs from the original."""
    g: dict = {"orig": {"class_type": "LoadImage", "inputs": {"image": image}}}
    edited = _qwen_edit_nodes(g, names, ["orig", 0], LEVEL_PROMPT, seed)
    g["same"] = {"class_type": "IrisEchoSimilarity", "inputs": {"a": ["q_scale", 0], "b": edited}}
    g["save"] = {"class_type": "SaveImage", "inputs": {"images": edited, "filename_prefix": prefix}}
    return g


def views_back(
    *, names, image: str, attempt: int, seed: int, prefix: str, back_words: str | None = None
):
    """A back view for the front, the pair framed for Pixal3D, and the mirror check.

    `back_words` (optional) describes what the back looks like; it follows the fixed wording.
    """
    g: dict = {}
    bg = _bg_model(g, names)
    front, front_mask = _cutout(g, "front", image, bg)
    prompt = BACK_PROMPTS[attempt % len(BACK_PROMPTS)]
    if back_words and back_words.strip():
        prompt = f"{prompt} {back_words.strip()}"
    back = _qwen_edit_nodes(g, names, front, prompt, seed)
    g["mask_back"] = {
        "class_type": "RemoveBackground",
        "inputs": {"bg_removal_model": [bg, 0], "image": back},
    }
    g["check"] = {
        "class_type": "IrisEchoViewCheck",
        "inputs": {"front_mask": front_mask, "back_mask": ["mask_back", 0]},
    }
    g["frame"] = {
        "class_type": "IrisEchoFrameViews",
        "inputs": {
            "front": front,
            "front_mask": front_mask,
            "back": back,
            "back_mask": ["mask_back", 0],
        },
    }
    g["save_front"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["frame", 0], "filename_prefix": f"{prefix}-front"},
    }
    g["save_back"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["frame", 1], "filename_prefix": f"{prefix}-back"},
    }
    return g


# Progress for the 3D graphs: node -> (start, end, message), in the order ComfyUI
# runs them (the mesh is finished before the texture is sampled). Measured on a
# 12 GB card at 1024: shape ~40 s, solid remesh ~30-70 s, unwrap ~40 s, texture ~20 s.
MODEL3D_PROGRESS = {
    "sample_structure": (0.03, 0.08, "Finding the shape"),
    "sample_shape": (0.08, 0.18, "Shaping"),
    "sample_detail": (0.18, 0.34, "Adding detail"),
    "shape": (0.34, 0.36, "Adding detail"),
    "remesh": (0.36, 0.58, "Making it solid"),
    "decimate": (0.58, 0.62, "Simplifying"),
    "unwrap": (0.62, 0.74, "Unfolding the surface"),
    "sample_texture": (0.74, 0.84, "Painting"),
    "bake": (0.84, 0.94, "Baking textures"),
    "save": (0.95, 0.96, "Saving"),
    "render": (0.96, 0.99, "Saving"),
}


BUILDERS = {
    "z-image-nunchaku": z_image,
    "z-image-native": z_image_native,
    "z-image-control": z_image_control,
    "flux-nunchaku": flux,
    "qwen-image-nunchaku": qwen_image,
    "krea2": krea2,
    "qwen-edit": qwen_edit,
    "kontext": kontext,
    "seedvr2": seedvr2,
    "seedvr2-video": seedvr2_video,
    "wan22": wan22,
    "wan22-camera": wan22_camera,
    "hunyuan15": hunyuan15,
    "ltx": ltx,
    "trellis2": trellis2,
    "pixal3d-mv": pixal3d_mv,
    "pixal3d-single": pixal3d_single,
}
MODEL3D = {"trellis2", "pixal3d-mv", "pixal3d-single"}

# The uploaded pictures (and clips) each workflow reads, by parameter name.
IMAGE_INPUTS = {
    "z-image-control": ("guide",),
    "flux-nunchaku": ("image1",),
    "qwen-edit": ("image1", "image2", "image3", "mask"),
    "kontext": ("image1",),
    "seedvr2": ("image1",),
    "seedvr2-video": ("video",),
    "wan22": ("start", "end"),
    "wan22-camera": ("start",),
    "hunyuan15": ("start",),
    "ltx": ("start", "end"),
    "trellis2": ("front",),
    "pixal3d-mv": ("front", "back"),
    "pixal3d-single": ("front",),
    "views3d": ("image1",),
}
PROMPT_OPTIONAL = {
    "seedvr2",
    "seedvr2-video",
    "trellis2",
    "pixal3d-mv",
    "pixal3d-single",
    "views3d",
}
# Run with ComfyUI's dynamic VRAM (see ComfyEngine.ensure_running). The 3D
# workflows were measured with it; the views helper shares it so that going from
# views to a build does not restart ComfyUI (Nunchaku's Qwen loader is fine with it).
DYNAMIC_VRAM = {
    "wan22",
    "wan22-camera",
    "hunyuan15",
    "ltx",
    "trellis2",
    "pixal3d-mv",
    "pixal3d-single",
    "views3d",
}


def describe_error(data: dict) -> str:
    errs = data.get("node_errors") or {}
    for nid, e in errs.items():
        for item in e.get("errors") or []:
            return f"{e.get('class_type', nid)}: {item.get('message')} ({item.get('details', '')})"
    err = data.get("error") or {}
    return err.get("message") or str(data)[:300]


def history_error(status: dict) -> str:
    for kind, data in status.get("messages") or []:
        if kind == "execution_error":
            return data.get("exception_message") or "The image engine failed."
    return "The image engine failed."
