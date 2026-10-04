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
        "DualCLIPLoader",
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


def stage_labels(graph: dict) -> dict[str, str]:
    out = {}
    for nid, node in graph.items():
        for stage, classes in STAGES.items():
            if node["class_type"] in classes:
                out[nid] = stage
    return out


def _custom_sampler(
    g: dict, *, model, conditioning, latent, seed, steps, sampler, guider=None, denoise=1.0
) -> str:
    """Add the RandomNoise → SamplerCustomAdvanced chain; returns the sampler node id."""
    g["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}}
    g["sampler"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}}
    g["sigmas"] = {
        "class_type": "BasicScheduler",
        "inputs": {"model": model, "scheduler": "simple", "steps": steps, "denoise": denoise},
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


def z_image(*, prompt, seed, aspect, names, settings, hw, prefix, native=False, **_):
    w, h = size_for(aspect, 1024)
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
    g["shift"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": ["unet", 0], "shift": 3.0},
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
        steps=int(settings.get("steps", 9)),
        sampler="res_multistep",
    )
    return g, _finish(g, ["vae", 0], prefix)


def z_image_native(**kw):
    return z_image(native=True, **kw)


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
        w, h = 1024, 1024
    else:
        w, h = size_for(aspect, 1024)
    denoise = 1.0
    if init:
        denoise = float(params["denoise"]) if params.get("denoise") is not None else 0.2
        if not 0.05 <= denoise <= 1.0:
            raise ValueError("Set denoise between 0.05 and 1.")
    dit = next(v for k, v in names.items() if k.endswith(("-int4", "-fp4")))
    # Turing cards (RTX 20) have no bfloat16.
    dtype = (
        "bfloat16" if max((gpu.compute_cap for gpu in hw.gpus), default=8.0) >= 8.0 else "float16"
    )
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
    model = ["unet", 0]
    conditioning = ["text", 0]
    if settings.get("guidance") is not None:
        g["guidance"] = {
            "class_type": "FluxGuidance",
            "inputs": {"conditioning": ["text", 0], "guidance": float(settings["guidance"])},
        }
        conditioning = ["guidance", 0]
    if settings.get("shift"):
        g["shift"] = {
            "class_type": "ModelSamplingFlux",
            "inputs": {
                "model": ["unet", 0],
                "max_shift": 1.15,
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
                    "megapixels": 1.0,
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
        steps=int(settings.get("steps", 4)),
        sampler="euler",
        denoise=denoise,
    )
    return g, _finish(g, ["vae", 0], prefix)


def qwen_image(*, prompt, seed, aspect, names, settings, hw, prefix, **_):
    w, h = size_for(aspect, 1328)
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
            "inputs": {"model": ["unet", 0], "shift": float(settings.get("shift", 3.1))},
        },
        "text": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": prompt}},
        "negative": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["clip", 0], "text": ""}},
        "latent": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": w, "height": h, "batch_size": 1},
        },
    }
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
        latent=["latent", 0],
        seed=seed,
        steps=int(settings.get("steps", 20)),
        sampler="euler",
        guider=guider,
    )
    return g, _finish(g, ["vae", 0], prefix)


def _nunchaku_dit(names: dict, hw) -> tuple[str, str]:
    dit = next(v for k, v in names.items() if k.endswith(("-int4", "-fp4")))
    # Turing cards (RTX 20) have no bfloat16.
    dtype = "bfloat16" if max((g.compute_cap for g in hw.gpus), default=8.0) >= 8.0 else "float16"
    return dit, dtype


def qwen_edit(*, prompt, seed, names, settings, hw, prefix, images, **_):
    """Instruction edit; up to three pictures, called picture 1-3 in the prompt."""
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
            "inputs": {"model": ["unet", 0], "shift": 3.0},
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
                "megapixels": 1.0,
                "resolution_steps": 1,
            },
        }
        refs[f"image{slot}"] = [f"scale{slot}", 0]
    if "image1" not in refs:
        raise ValueError("Add the picture to edit.")
    common = {"clip": ["clip", 0], "vae": ["vae", 0], **refs}
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
    g["sample"] = {
        "class_type": "KSampler",
        "inputs": {
            "model": ["norm", 0],
            "positive": ["text", 0],
            "negative": ["negative", 0],
            "latent_image": ["encode", 0],
            "seed": seed,
            "steps": int(settings.get("steps", 4)),
            "cfg": float(settings.get("cfg", 1.0)),
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
        },
    }
    return g, _finish(g, ["vae", 0], prefix)


def kontext(*, prompt, seed, names, settings, hw, prefix, images, **_):
    if not images.get("image1"):
        raise ValueError("Add the picture to edit.")
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
                "guidance": float(settings.get("guidance", 2.5)),
            },
        },
        "reference": {
            "class_type": "ReferenceLatent",
            "inputs": {"conditioning": ["guidance", 0], "latent": ["encode", 0]},
        },
    }
    _custom_sampler(
        g,
        model=["unet", 0],
        conditioning=["reference", 0],
        latent=["encode", 0],
        seed=seed,
        steps=int(settings.get("steps", 8)),
        sampler="euler",
    )
    return g, _finish(g, ["vae", 0], prefix)


def seedvr2(*, seed, names, prefix, images, params, **_):
    """Restoration upscale; `retain` upscales internally, then returns the original size."""
    if not images.get("image1"):
        raise ValueError("Add the picture to upscale.")
    scale = int(params.get("scale") or 2)
    if scale not in (2, 3, 4):
        raise ValueError("Choose 2x, 3x or 4x.")
    tiled = {"tile_size": 512, "overlap": 128, "temporal_size": 4096, "temporal_overlap": 8}
    g: dict = {
        "load": {"class_type": "LoadImage", "inputs": {"image": images["image1"]}},
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": names["seedvr2-3b"], "weight_dtype": "default"},
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
                "color_correction_method": "lab",
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


# Pixel budgets: sqrt(480 x 832) and sqrt(576 x 1024).
VIDEO_TIERS = {"standard": 632, "large": 768}


def wan22(*, prompt, seed, aspect, names, settings, prefix, images, params, **_):
    """Wan 2.2 14B: two experts with the Lightning LoRA, 4 steps split 2 + 2."""
    mode = settings.get("mode", "i2v")
    start, end = images.get("start"), images.get("end")
    if mode == "i2v" and not (start or end):
        raise ValueError("Add a first frame, a last frame, or both.")
    seconds = max(2, min(5, int(params.get("seconds") or 3)))
    frames = round((seconds * 16 - 1) / 4) * 4 + 1  # Wan needs 4n + 1 frames
    w, h = size_for(aspect, VIDEO_TIERS.get(params.get("size", "standard"), 632))
    g: dict = {
        "unet_high": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": names[f"wan-{mode}-high"]},
        },
        "unet_low": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {"unet_name": names[f"wan-{mode}-low"]},
        },
        "lora_high": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["unet_high", 0],
                "lora_name": names[f"wan-{mode}-lora-high"],
                "strength_model": 1.0,
            },
        },
        "lora_low": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "model": ["unet_low", 0],
                "lora_name": names[f"wan-{mode}-lora-low"],
                "strength_model": 1.0,
            },
        },
        "shift_high": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["lora_high", 0], "shift": 5.0},
        },
        "shift_low": {
            "class_type": "ModelSamplingSD3",
            "inputs": {"model": ["lora_low", 0], "shift": 5.0},
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
    frames_out, fps = ["decode", 0], 16
    if params.get("smooth"):
        g["interp_model"] = {
            "class_type": "FrameInterpolationModelLoader",
            "inputs": {"model_name": names["film"]},
        }
        g["interp"] = {
            "class_type": "FrameInterpolate",
            "inputs": {
                "interp_model": ["interp_model", 0],
                "images": ["decode", 0],
                "multiplier": 2,
            },
        }
        frames_out, fps = ["interp", 0], 32
    g["video"] = {"class_type": "CreateVideo", "inputs": {"images": frames_out, "fps": fps}}
    g["save"] = {
        "class_type": "SaveVideo",
        "inputs": {
            "video": ["video", 0],
            "filename_prefix": prefix,
            "format": "auto",
            "codec": "auto",
        },
    }
    return g, "save"


BUILDERS = {
    "z-image-nunchaku": z_image,
    "z-image-native": z_image_native,
    "flux-nunchaku": flux,
    "qwen-image-nunchaku": qwen_image,
    "qwen-edit": qwen_edit,
    "kontext": kontext,
    "seedvr2": seedvr2,
    "wan22": wan22,
}

# The uploaded pictures each workflow reads, by parameter name.
IMAGE_INPUTS = {
    "flux-nunchaku": ("image1",),
    "qwen-edit": ("image1", "image2", "image3"),
    "kontext": ("image1",),
    "seedvr2": ("image1",),
    "wan22": ("start", "end"),
}
PROMPT_OPTIONAL = {"seedvr2"}


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
