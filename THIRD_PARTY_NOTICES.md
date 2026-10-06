# Third-party notices

IrisEcho's own code is AGPL-3.0-or-later. It is built with, or installs on the
user's machine, the components below, each under its own license. Engines are
not bundled: IrisEcho downloads them from their publishers at the pinned
versions shown when a model that needs them is first set up.

## Engines (installed on demand, each in its own environment)

| Component | Version | License | Upstream |
|---|---|---|---|
| ComfyUI | v0.38.0 (`6b747c04`) | GPL-3.0 | https://github.com/Comfy-Org/ComfyUI |
| ComfyUI-nunchaku | `c71cc259`, one file patched at install for ComfyUI v0.38 | Apache-2.0 | https://github.com/nunchaku-ai/ComfyUI-nunchaku |
| nunchaku | 1.2.1 | Apache-2.0 | https://github.com/nunchux-ai/nunchaku |
| ComfyUI-GGUF | `6ea2651e` | Apache-2.0 | https://github.com/city96/ComfyUI-GGUF |
| PyTorch | 2.11 / 2.7 | BSD-3-Clause | https://pytorch.org |
| torchvision (prompt writer) | 0.26 | BSD-3-Clause | https://github.com/pytorch/vision |
| Transformers (prompt writer) | 5.2.0 | Apache-2.0 | https://github.com/huggingface/transformers |
| Accelerate (prompt writer) | 1.x | Apache-2.0 | https://github.com/huggingface/accelerate |
| Pillow (prompt writer) | 12.x | MIT-CMU | https://github.com/python-pillow/Pillow |
| Kokoro | 0.9.4 | Apache-2.0 | https://github.com/hexgrad/kokoro |
| misaki | 0.9.4 | Apache-2.0 | https://github.com/hexgrad/misaki |
| spaCy `en_core_web_sm` | 3.8.0 | MIT | https://github.com/explosion/spacy-models |
| chatterbox-tts | 0.1.7 | MIT | https://github.com/resemble-ai/chatterbox |
| resemble-perth | 1.x | MIT | https://github.com/resemble-ai/Perth |
| ACE-Step 1.5 | `6d467e4b` | MIT | https://github.com/ace-step/ACE-Step-1.5 |
| Stable Audio 3 | `3a82c807` | MIT | https://github.com/Stability-AI/stable-audio-3 |
| FFmpeg (through PyAV, used by ComfyUI) | bundled with PyAV | LGPL-2.1+ | https://ffmpeg.org |

## The core (a Python package installed by the desktop app)

| Component | License | Upstream |
|---|---|---|
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| HTTPX | BSD-3-Clause | https://github.com/encode/httpx |
| socksio (httpx SOCKS extra) | MIT | https://github.com/sethmlarson/socksio |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| python-multipart | Apache-2.0 | https://github.com/Kludex/python-multipart |
| PyYAML | MIT | https://github.com/yaml/pyyaml |
| keyring | MIT | https://github.com/jaraco/keyring |
| uv | MIT or Apache-2.0 | https://github.com/astral-sh/uv |
| segno (QR codes for pairing) | BSD-3-Clause | https://github.com/heuer/segno |

## The desktop app and interface

| Component | License | Upstream |
|---|---|---|
| Tauri | MIT or Apache-2.0 | https://github.com/tauri-apps/tauri |
| Svelte | MIT | https://github.com/sveltejs/svelte |
| Vite | MIT | https://github.com/vitejs/vite |
| Lucide icons | ISC | https://github.com/lucide-icons/lucide |
| Bricolage Grotesque | SIL OFL 1.1 | https://github.com/ateliertriay/bricolage |
| JetBrains Mono | SIL OFL 1.1 | https://github.com/JetBrains/JetBrainsMono |

The two font licenses ship with the interface, next to the fonts.

Model weights are listed separately, with their licenses, in [MODELS.md](MODELS.md).
