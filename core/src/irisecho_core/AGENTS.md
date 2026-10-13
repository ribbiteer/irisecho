# Using IrisEcho from a script or a coding agent

IrisEcho makes images, image edits, video, speech, cloned voices, music, sound
effects and 3D models on this computer's own GPU. Its command line and local API cover
what the window does, so a local agent (Claude Code, Codex, a shell script, a
build step) can ask for an asset and get a file back.

This copy of the guide was written by the IrisEcho installed here:

- command line: `$cli`
- data folder: `$data`
- print this guide again: `$cli agents`

The examples below write `irisecho` for the command line above.

## The short version

```sh
irisecho models                       # what exists here and what is ready
irisecho image "a lighthouse at blue hour" --aspect 16:9 --count 2 --out assets/
irisecho say "Welcome back." --voice bm_george --out vo/
irisecho music "synthwave, driving, analog synth" --bpm 110 --seconds 30 --loop --out music/
irisecho 3d figurine.png --format stl --height 80 --out prints/
```

Each finished file is printed as `label: path`. Add `--json` to a generating
command for one JSON object per job on stdout (progress goes to stderr):

```json
{"id": "3f9c2a71b0de", "status": "done", "model": "z-image-turbo", "outputs": ["...\\lighthouse-3f9c2a.png"], "seed": 123, "error": null}
```

The exit code is 0 when every job finished, 1 when any failed, 2 for a bad
command line.

## One queue, one GPU

If the IrisEcho window is open, the command line hands its work to that running
app: jobs join the same queue, appear in the window, and share the one model
held on the GPU. If it is not open, the command starts the engines itself and
stops them when it finishes. You do not choose; both behave the same.

Jobs run one at a time. Switching between engines (say, from pictures to
speech) unloads one model and loads another, which costs 10 to 40 seconds each
way, so batch by kind: all the pictures, then all the voice lines. Give several
lines to one command (`--count`, `--file`) rather than starting the command
many times.

The last model used stays on the GPU. Before running another program that needs
the graphics card, free it with `irisecho api POST system/unload` and check
`vram` in `irisecho api GET system`; the next IrisEcho job loads its model again.

## Setting up

Nothing is downloaded until a model is asked for.

```sh
irisecho setup z-image-turbo          # install its engine and download its files
irisecho link "D:\ComfyUI\models"     # use model files that already exist; nothing is copied
```

`link` finds files that are byte-for-byte the models IrisEcho lists, then says
which models are complete. A complete model may still need its engine: finish
with `irisecho setup MODEL`. The generating commands also set a model up on
first use.

Some models have terms a person has to agree to. **Do not pass
`--accept-license`, and do not add a Hugging Face token, unless the person you
are working for told you to for that model.** Without it the command stops and
prints the license for them to read. Non-commercial models (`irisecho models`
marks them) must not be used for commercial work.

## Commands

| Command | Makes | Main options |
|---|---|---|
| `image PROMPT` | pictures | `--model` (default `z-image-turbo`), `--aspect 1:1\|4:3\|3:4\|16:9\|9:16\|3:2\|2:3`, `--count`, `--seed`; FLUX models also `--from picture.png`, `--denoise`, `--size 1920x1088`; `--guide picture.png` and `--guide-type canny\|depth\|pose\|as_is` with `z-image-control` |
| `say TEXT` | narration | `--voice` (default `af_heart`), `--speed`, `--file cues.txt` |
| `clone TEXT --ref clip.wav` | speech in a sampled voice | `--model chatterbox-turbo\|chatterbox`, `--takes`, `--file cues.txt`; the clip is 10 to 15 s of one voice (WAV, MP3, FLAC or OGG; Turbo needs more than 5 s) |
| `music TAGS` | music | `--seconds`, `--bpm`, `--loop`, `--lyrics` or `--lyrics-file`, `--takes`, `--no-thinking`, `--file cues.txt` |
| `sfx PROMPT` | sound effects | `--seconds` (0.5 to 30), `--takes` (default 3), `--raw`, `--file cues.txt`, `--accept-license` |
| `write DRAFT` | a better prompt, printed on stdout | `--for MODEL`, `--image picture.png` |
| `3d PICTURE` | a 3D model | `--back picture.png`, `--model auto\|pixal3d\|pixal3d-single\|trellis2`, `--detail standard\|high`, `--faces 500000\|100000\|20000`, `--no-textures`, `--keep-openings`, `--format glb\|stl\|3mf\|obj\|ply`, `--height MM` |

All generating commands take `--set NAME=VALUE` (advanced controls, below), `--out FOLDER` (copy results there; with a cue
sheet, files are named by cue id) and `--json`. `irisecho COMMAND --help` lists
everything.

Cue sheets are text files, one job per line, `#` for comments:

- `say`, `clone` and `sfx`: `id|text`
- `music`: `id|seconds|bpm|tags` (bpm may be empty)

Only clone a voice the person has the right to use. Cloned speech carries an
inaudible watermark. Long text is spoken sentence by sentence and joined, so a
whole paragraph comes out in one file.

Speech is trimmed and brought to -19 LUFS with peaks under -1 dBFS (`--raw`
keeps the model's own output). Music plans tempo, key and form with ACE-Step's
song planner first, as ACE-Step itself does; `--no-thinking` skips it for faster
drafts. `--loop` (with `--bpm`) cuts whole bars from the first downbeat it hears
and blends the seam. Sound effects keep their own loudness, so a pin drop stays
quieter than an explosion; only the silence the model pads with is trimmed.

### 3D models

`irisecho 3d picture.png` makes a 3D model of the object in the picture. Give
it one whole object on a plain background; a picture made for the purpose
(`irisecho image` with "a ... figurine, the whole object visible and centered,
plain light grey background, soft even lighting, straight-on front view at eye
level") works best. With the default `--model auto`, IrisEcho checks whether
the picture was taken at eye level. If it was, the View Maker (Qwen Edit) draws
the object from behind, checks that the outline matches, and Pixal3D builds from
both views: the most complete shapes. If not, TRELLIS.2 builds from the picture
alone: it keeps the object's details and stands it upright, and guesses the
back. `--model trellis2` skips the View Maker (and its 21 GB download).

A build takes two to four minutes on a 12 GB card (`--detail high` about a
minute more; when High does not fit in graphics memory IrisEcho steps down by
itself). Models are solid and closed, so they print without repairs;
`--keep-openings` leaves cups and vases open at the top. `--faces 20000` makes
a light model for games and the web whose surface detail lives in its normal
map (100000 for 3D programs; keep the default 500000 for printing). `--format stl` or
`3mf` writes a print file standing upright on the plate, `--height` millimetres
tall (default 100); `obj` (a zip with its texture), `ply` and `glb` keep the
model's own frame for 3D programs. With `--json` the object includes
`printable` and, when it is false, `reasons`.

## Everything else: the local API

Edits, upscaling, video and library housekeeping have no command of their own
yet. Reach them through the running app's API. `irisecho api`
finds the app and signs the request; the window must be open (or
`irisecho serve` running).

```sh
irisecho api GET models
irisecho api POST jobs '{"model": "sa3-sfx", "params": {"prompt": "a heavy oak door closing", "duration": 2}}' --wait
irisecho api POST jobs - --wait < job.json           # "-" reads the body from stdin
```

`irisecho api` takes the path without its `/api/` prefix (`models` is
`/api/models`). `--wait` follows a submitted job until it ends and prints the
finished job; the exit code is 0 only if it finished. A job's
`outputs[n].path` is the file on disk. Quoting JSON on a command line differs
between shells, so a body file and `-` is the dependable way.

| Call | Does |
|---|---|
| `GET /api/models` | every model: `id`, `kind`, `ready`, `needs`, license, voices under `options` |
| `POST /api/models/{id}/prepare` | install and download what a model needs |
| `POST /api/jobs` with `{"model", "params"}` | queue a job; 409 with `needs` if the model is not ready |
| `GET /api/jobs/{id}` | `status` (`queued`, `running`, `done`, `failed`, `cancelled`, `interrupted`), `progress`, `message`, `error`, `outputs` |
| `GET /api/jobs?q=fox&kind=image&limit=20` | search what has been made |
| `POST /api/jobs/{id}/cancel` | stop a queued or running job |
| `POST /api/uploads` (multipart `file`) | add a picture or sound for a job to use; returns its `id` |
| `GET /api/system` | GPU, memory in use right now (`vram`: `used_mb` and `free_mb` per card, the whole card), what is loaded, queue length |
| `POST /api/system/unload` | free the GPU for another program; 409 while a job is running |
| `GET /api/jobs/{id}/outputs/{n}/export?format=stl&height_mm=80` | a 3D model in `glb`, `stl`, `3mf` (millimetres, upright on the plate), `obj` (zip) or `ply` |
| `GET /api/jobs/{id}/outputs/{n}/preview` | the still picture of a 3D model |

Job `params` by kind of model:

| Kind | Models | Params |
|---|---|---|
| image | `z-image-turbo`, `flux-schnell`, `qwen-image-fast`, `qwen-image`, `flux-dev`, `flux-krea`, `krea-2`, `z-image-control` | `prompt`, `aspect`, `seed`; `image1` and `denoise` (0.05 to 1, default 0.2) to start from a picture; `z-image-control` needs a `guide` picture and takes `guide_type` (`canny`, `depth`, `pose`, or `as_is` for a ready-made map) |
| edit | `qwen-edit-fast`, `qwen-edit`, `flux-kontext` | `prompt`, `image1` (and `image2`, `image3` for the Qwen models), `seed`; `box` or `mask` to change only an area (Qwen models) |
| upscale | `seedvr2`, `seedvr2-7b` | `image1`, `scale` (2, 3 or 4), `retain` (true keeps the original size), `seed` |
| video | `ltx25`, `wan22-t2v`, `wan22-i2v`, `wan22-i2v-1022`, `hunyuan15-i2v`, `wan22-camera` | `prompt`, `aspect`, `seconds` (2 to 5), `size` (`standard`, `large`, or `720p` on a 12 GB card; `hunyuan15-i2v` has no `large`), `smooth`, and a `start` and/or `end` frame: required for the `i2v` models (`hunyuan15-i2v` takes `start` only), optional for `ltx25`; `wan22-camera` needs `start` and takes `camera` (`static`, `pan_up`, `pan_down`, `pan_left`, `pan_right`, `zoom_in`, `zoom_out`, `clockwise`, `anticlockwise`) |
| voice | `kokoro` | `text`, `voice`, `speed`, `clean` |
| clone | `chatterbox-turbo`, `chatterbox` | `text`, `ref`, `consent` (must be `true`), `takes`, `seed`, `clean` (false keeps the model's own output); `exaggeration` and `cfg` on `chatterbox` |
| music | `ace-step` | `prompt` (style tags), `duration`, `bpm`, `lyrics`, `takes`, `loop`, `thinking` (default `true`), `seed`; from a recording: `source` (an upload id of a WAV, MP3, FLAC or OGG) and `task` `cover`, `repaint` (with `region` `[start_s, end_s]`) or `extend` (with `extend_by` seconds) |
| sfx | `sa3-sfx` | `prompt`, `duration` (0.5 to 30), `takes`, `trim` (default true; false is the command line's `--raw`), `seed`; `init_audio` (an upload id) for a variation of a sound, plus `region` `[start_s, end_s]` to make only that part again |
| video-upscale | `seedvr2-video` | `video` (an upload id of an MP4 clip), `short_side` (720 to 1080, default 1080), `seed` |
| model3d | `trellis2`, `pixal3d`, `pixal3d-single` | `front` (and `back` for `pixal3d`; `pixal3d-single` builds from `front` alone), `detail` (`standard` or `high`), `faces` (500000, 100000 or 20000), `textures` (default true), `openings` (`close` or `keep`), `seed` |
| views3d | `object-views` | `image1`, `level` (`auto`, `best` or `keep`), `seed` |

`image1`, `image2`, `image3`, `mask`, `guide`, `start`, `end`, `ref`, `source`, `init_audio`, `video`, `front` and `back`
are upload ids.
An agent on this computer can skip the upload call: copy the file into the
`uploads` folder inside the data folder under a new name of lowercase hex digits
plus its extension (`3fa2b1c4.png`) and pass that name.

`seedvr2-video` is experimental and never part of a default workflow. On
generated clips it made faces look plastic, and on a close-up of objects it added
grit and flicker. Compare its result with a plain resize before using it. A 4 s
720p clip takes about 7 minutes and nearly all of a 12 GB card.

`ltx25` (LTX-2.5) makes the clip's sound with it, from text alone or from a
first and/or last frame, and is the fastest video model (a 4 s clip in one to
two minutes, 720p included). Write it a long prompt, 120 to 200 words in one
paragraph: what the frame shows, every action in order, the shot and camera
move, and what is heard, with spoken words in quotes. Short motion-only prompts
like Wan's drop actions and get directions wrong. `irisecho write --for ltx25`
writes that kind of prompt. `hunyuan15-i2v` gives the most natural faces and
hands from a first frame but is silent and about twice as slow; its `720p` adds
an upscaling pass that takes about 11 minutes for 4 s.

For camera moves from a still, use `wan22-i2v-1022` with an `end` frame where
the move should finish: for a push-in a centre crop of the start frame, for a
pan an offset crop of a wider picture. The camera words name the direction and
the end frame sets the distance ("slowly" is not followed). The last few frames
snap to the end frame, so trim about 8. For a still camera on a face, prefer
`wan22-i2v` at `large`: at `720p` both models invented expressions and skin
texture on still faces.

The FLUX image models (`flux-schnell`, `flux-dev`, `flux-krea`) also take an
optional `image1` to redraw, with `denoise` (0.05 to 1, default 0.2) for how
much changes, and `width` and `height` (multiples of 16, at most 1920 x 1088 in
area; without them the picture keeps its own aspect at about one megapixel).
The tested use is `flux-krea` redrawing a whole picture larger at 0.2, with the
prompt that made it: people and composition stay, skin and hair gain detail.
Props can change, so check them; a crop of a face redrawn this way does not
keep the person.

A finished `model3d` job has one output: the GLB (`type` `model3d`) with
`preview` (a picture of it) and `mesh`: `faces`, `extents` (model units, Y up),
`watertight`, `thickness` and `printable` with `reasons`. An `object-views`
job with `level` `auto` returns either a `front` and a `back` (each output has
a `role`; the back has `matched`, true when its outline mirrors the front's),
or, for a picture taken from above, only an eye-level version (`role`
`level`): build from the original with `trellis2`, or run the views again on
that version with `level` `keep`. `best` goes on with the eye-level version by
itself. The views are framed for Pixal3D: pass them on as `front` and `back`.

To call the API without `irisecho api`: read `port` and `token` from
`server.json` in the data folder, send the cookie `irisecho_session=<token>`
to `http://127.0.0.1:<port>`, and add the header `X-IrisEcho: 1` to anything
that is not a GET. The token changes every time the app starts. It opens this
computer's IrisEcho to whoever holds it, so keep it out of logs, prompts and
commits.

## Advanced controls

Every model works well with no settings beyond its params above. Many also take
advanced controls: steps, guidance, size, add-ons (LoRAs), sampler and more,
depending on what the model can use. Leave a control out and the model runs
exactly as it is tuned to; set it and that one value changes. Distilled models
(most of them) have no guidance or negative prompt, because those would do
nothing.

- Command line: `--set NAME=VALUE` on any generating command (repeat it), for
  example `irisecho image "a fox" --model flux-dev --set steps=30 --set guidance=2.5`.
  A value that reads as JSON is taken as JSON (`--set loras='[{"file": "style.safetensors", "strength": 0.8}]'`).
  `irisecho models --controls MODEL` lists a model's controls with their ranges.
- API: put the control in `params`. `GET /api/models` lists each model's
  `controls` (name, type, range or options, `default`, help). A value out of
  range is refused with a 400 and a plain message. A param the model does not
  use is ignored and named in the response's `warnings`.
- Add-ons (LoRAs): `.safetensors` files in the `loras` folder of the models
  folder (or of a linked models folder). `GET /api/loras?family=flux` lists them
  with the model family each fits (`flux`, `zimage`, `wan14b`, ...) and any
  trigger words. Up to 3 per job, strength -2 to 2. Wan takes pairs:
  `{"high": FILE, "low": FILE, "strength": 1}`. A file made for another family
  is refused. Qwen-Image and Qwen Edit take no add-ons.
- Changing only an area (`qwen-edit`, `qwen-edit-fast`): `box` `[left, top,
  right, bottom]` as fractions of Picture 1, or `mask` (an upload id of a picture,
  white where it may change). Outside the area the result is Picture 1's own
  pixels.
- Presets: `GET /api/presets`, `PUT /api/presets` with `{"model", "name",
  "values"}`, `DELETE /api/presets?model=M&name=N`. They are kept in
  `presets.json` in the data folder.
- Settings in pictures: with the "Keep prompts out of image files" setting off,
  each PNG carries the prompt and settings (A1111's `parameters` text, which
  Civitai and Forge read, plus IrisEcho's own). `GET /api/uploads/{id}/settings`
  reads them back from an uploaded picture.
- To compare one setting, run the same job with the same `seed` and only that
  control changed.

<!-- controls:start -->
<!-- Generated by scripts/gen_models_md.py from the registry. Do not edit. -->

| Model | Param | Values | Recommended | What it does |
|---|---|---|---|---|
| `qwen-edit-fast` | `steps` | 2 to 6 | 4 | Steps. More is slower and sometimes finer. This model is tuned for 4. |
| `qwen-edit-fast` | `megapixels` | 0.5 to 2.0 | 1.0 | Working size. The size the picture is edited at. The result comes out at this size, in the picture's own shape. |
| `qwen-edit-fast` | `keep_size` | bool | false | Keep the exact framing. Edits at a size the model reads without rescaling, so the unchanged parts line up with the original instead of shifting a few pixels. |
| `qwen-edit-fast` | `feather` | 0 to 31 (with `box`) | 12 | Soft edge. How gradually the changed area blends into the rest when you change only an area. 0 is a hard edge. |
| `qwen-edit-fast` | `shift` | 2 to 5 | 3.0 | Shift. Higher favours overall shapes, lower favours fine detail. |
| `qwen-edit-fast` | `sampler` | euler, res_multistep, dpmpp_2m | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with. |
| `qwen-edit-fast` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `qwen-edit` | `steps` | 6 to 10 | 8 | Steps. More is slower and sometimes finer. This model is tuned for 8. |
| `qwen-edit` | `megapixels` | 0.5 to 2.0 | 1.0 | Working size. The size the picture is edited at. The result comes out at this size, in the picture's own shape. |
| `qwen-edit` | `keep_size` | bool | false | Keep the exact framing. Edits at a size the model reads without rescaling, so the unchanged parts line up with the original instead of shifting a few pixels. |
| `qwen-edit` | `feather` | 0 to 31 (with `box`) | 12 | Soft edge. How gradually the changed area blends into the rest when you change only an area. 0 is a hard edge. |
| `qwen-edit` | `shift` | 2 to 5 | 3.0 | Shift. Higher favours overall shapes, lower favours fine detail. |
| `qwen-edit` | `sampler` | euler, res_multistep, dpmpp_2m | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with. |
| `qwen-edit` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `flux-kontext` | `guidance` | 1.5 to 4 | 2.5 | Follow the prompt. Higher sticks closer to your words and adds contrast; lower looks more natural. |
| `flux-kontext` | `steps` | 12 to 40 | 20 | Steps. More is slower and sometimes finer. 20 is recommended. |
| `flux-kontext` | `loras` | up to 3 {file, strength}, strength -2 to 2 | none | Add-ons (LoRA). Styles, characters or concepts trained for FLUX.1. Put .safetensors files in the loras folder of your models folder. |
| `flux-kontext` | `sampler` | euler, dpmpp_2m, deis, heun | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with; heun takes twice as long. |
| `flux-kontext` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `seedvr2` | `color` | lab, wavelet, adain, none | lab | Colour matching. How the colours of the result are matched to your picture. "True to the original" is safest; "Keep the new detail" lets the restored texture win where they disagree; "No correction" shows the raw result. |
| `seedvr2` | `tile` | 512, 768, 1024 | 512 | Tile size. The size of the pieces a big picture is worked in. Bigger pieces mean fewer visible seams but need more graphics memory; go back to 512 if it runs out. |
| `seedvr2-7b` | `color` | lab, wavelet, adain, none | lab | Colour matching. How the colours of the result are matched to your picture. "True to the original" is safest; "Keep the new detail" lets the restored texture win where they disagree; "No correction" shows the raw result. |
| `seedvr2-7b` | `tile` | 512, 768, 1024 | 512 | Tile size. The size of the pieces a big picture is worked in. Bigger pieces mean fewer visible seams but need more graphics memory; go back to 512 if it runs out. |
| `seedvr2-video` | `color` | lab, wavelet, adain, none | lab | Colour matching. How the colours of the result are matched to your picture. "True to the original" is safest; "Keep the new detail" lets the restored texture win where they disagree; "No correction" shows the raw result. |
| `pixal3d` | `triangles` | 5000 to 1000000 | 500000 | Exact triangle count. Overrides the triangle choice above. Game models are usually 5,000 to 50,000; 3D printing 200,000 or more. Very low counts can fail to export. |
| `pixal3d` | `texture_size` | 1024, 2048 | 2048 | Texture size. The width of the painted surface in pixels. 1024 makes a smaller file; 2048 keeps finer print. |
| `pixal3d` | `smoothing` | 0 to 8 | 3 | Surface smoothing. Rounds off the bumps of the raw shape. Lower keeps sharp edges and any streaks; higher gives a softer, more organic surface. |
| `pixal3d` | `crease` | 180, 60, 30 | 180 | Shading of edges. Where the shading shows a hard edge. Smooth suits creatures and figures; the sharp settings suit boxes, gears and other hard-surface things, especially at low triangle counts. |
| `pixal3d` | `keep_small` | 0, 0.01, 0.05 | 0.01 | Drop small loose parts. Separate bits much smaller than the main model are thrown away as specks. Keep every loose part if your object has floating pieces such as a hovering sword or detached buttons. |
| `pixal3d` | `steps` | 8 to 30 | 12 | Steps. How many refinement steps each of the three stages takes. More is slower (a few seconds per step) and not always better. 12 is what the model was tuned with. |
| `pixal3d` | `guidance` | 3 to 10 | 7.5 | Follow the picture. How strictly the shape follows your picture. Higher is more literal; lower is looser and can look less over-baked. The painting stage is not affected. |
| `trellis2` | `triangles` | 5000 to 1000000 | 500000 | Exact triangle count. Overrides the triangle choice above. Game models are usually 5,000 to 50,000; 3D printing 200,000 or more. Very low counts can fail to export. |
| `trellis2` | `texture_size` | 1024, 2048 | 2048 | Texture size. The width of the painted surface in pixels. 1024 makes a smaller file; 2048 keeps finer print. |
| `trellis2` | `smoothing` | 0 to 8 | 3 | Surface smoothing. Rounds off the bumps of the raw shape. Lower keeps sharp edges and any streaks; higher gives a softer, more organic surface. |
| `trellis2` | `crease` | 180, 60, 30 | 180 | Shading of edges. Where the shading shows a hard edge. Smooth suits creatures and figures; the sharp settings suit boxes, gears and other hard-surface things, especially at low triangle counts. |
| `trellis2` | `keep_small` | 0, 0.01, 0.05 | 0.01 | Drop small loose parts. Separate bits much smaller than the main model are thrown away as specks. Keep every loose part if your object has floating pieces such as a hovering sword or detached buttons. |
| `trellis2` | `steps` | 8 to 30 | 12 | Steps. How many refinement steps each of the three stages takes. More is slower (a few seconds per step) and not always better. 12 is what the model was tuned with. |
| `trellis2` | `guidance` | 3 to 10 | 7.5 | Follow the picture. How strictly the shape follows your picture. Higher is more literal; lower is looser and can look less over-baked. The painting stage is not affected. |
| `pixal3d-single` | `triangles` | 5000 to 1000000 | 500000 | Exact triangle count. Overrides the triangle choice above. Game models are usually 5,000 to 50,000; 3D printing 200,000 or more. Very low counts can fail to export. |
| `pixal3d-single` | `texture_size` | 1024, 2048 | 2048 | Texture size. The width of the painted surface in pixels. 1024 makes a smaller file; 2048 keeps finer print. |
| `pixal3d-single` | `smoothing` | 0 to 8 | 3 | Surface smoothing. Rounds off the bumps of the raw shape. Lower keeps sharp edges and any streaks; higher gives a softer, more organic surface. |
| `pixal3d-single` | `crease` | 180, 60, 30 | 180 | Shading of edges. Where the shading shows a hard edge. Smooth suits creatures and figures; the sharp settings suit boxes, gears and other hard-surface things, especially at low triangle counts. |
| `pixal3d-single` | `keep_small` | 0, 0.01, 0.05 | 0.01 | Drop small loose parts. Separate bits much smaller than the main model are thrown away as specks. Keep every loose part if your object has floating pieces such as a hovering sword or detached buttons. |
| `pixal3d-single` | `steps` | 8 to 30 | 12 | Steps. How many refinement steps each of the three stages takes. More is slower (a few seconds per step) and not always better. 12 is what the model was tuned with. |
| `pixal3d-single` | `guidance` | 3 to 10 | 7.5 | Follow the picture. How strictly the shape follows your picture. Higher is more literal; lower is looser and can look less over-baked. The painting stage is not affected. |
| `object-views` | `back_words` | text | none | Describe the back. Words about what the back of your object looks like, for example "a zipper down the middle and two pockets". Added to the instruction that draws the back view. |
| `ltx25` | `motion` | 0 to 50 | 18 | Allow more motion. Higher lets the clip move further away from your picture; lower keeps it closer to the picture and calmer. |
| `ltx25` | `start_strength` | 0.5 to 1.0 (with `start`) | 0.7 | Hold the first picture. How tightly the first frame is held. Lower gives the clip more freedom to change (looser likeness, more movement); 1 pins it. |
| `ltx25` | `end_strength` | 0.3 to 1.0 (with `end`) | 0.7 | Hold the last picture. How tightly the last frame is held. Lower lets the clip drift away from it; higher makes it land exactly there. |
| `ltx25` | `sound` | bool | true | Make sound. Switch off for a silent clip. |
| `ltx25` | `sampler` | euler_ancestral, euler | euler_ancestral | Sampler. The method that removes noise at each step. euler_ancestral is what this model was tuned with; euler is steadier between seeds. |
| `ltx25` | `interpolation` | 2, 4 (with `smooth`) | 2 | Smoothing amount. How many frames the Smooth option adds between each pair. 4x is silkier and takes longer, and the file gets larger. |
| `wan22-i2v` | `loras` | up to 3 {high, low, strength}, strength -2 to 2 | none | Add-ons (LoRA). Camera, style or motion add-ons made for Wan 2.2 14B. Each needs a high-noise and a low-noise file (put them in the loras folder of your models folder). |
| `wan22-i2v` | `interpolation` | 2, 4 (with `smooth`) | 2 | Smoothing amount. How many frames the Smooth option adds between each pair. 4x is silkier and takes longer, and the file gets larger. |
| `wan22-i2v-1022` | `distill_high` | 0.5 to 1.4 | 1.0 | Speed-up strength, high noise. Strength of the four-step speed-up on the first half of the clip's making, which sets the motion. Lowering it (0.6 to 0.8) is the usual fix when the motion looks slow. |
| `wan22-i2v-1022` | `distill_low` | 0.5 to 1.4 | 1.0 | Speed-up strength, low noise. Strength of the four-step speed-up on the second half, which sets the fine detail. Change it only if the details look off. |
| `wan22-i2v-1022` | `loras` | up to 3 {high, low, strength}, strength -2 to 2 | none | Add-ons (LoRA). Camera, style or motion add-ons made for Wan 2.2 14B. Each needs a high-noise and a low-noise file (put them in the loras folder of your models folder). |
| `wan22-i2v-1022` | `interpolation` | 2, 4 (with `smooth`) | 2 | Smoothing amount. How many frames the Smooth option adds between each pair. 4x is silkier and takes longer, and the file gets larger. |
| `wan22-t2v` | `distill_high` | 0.5 to 1.4 | 1.0 | Speed-up strength, high noise. Strength of the four-step speed-up on the first half of the clip's making, which sets the motion. Lowering it (0.6 to 0.8) is the usual fix when the motion looks slow. |
| `wan22-t2v` | `distill_low` | 0.5 to 1.4 | 1.0 | Speed-up strength, low noise. Strength of the four-step speed-up on the second half, which sets the fine detail. Change it only if the details look off. |
| `wan22-t2v` | `loras` | up to 3 {high, low, strength}, strength -2 to 2 | none | Add-ons (LoRA). Camera, style or motion add-ons made for Wan 2.2 14B. Each needs a high-noise and a low-noise file (put them in the loras folder of your models folder). |
| `wan22-t2v` | `interpolation` | 2, 4 (with `smooth`) | 2 | Smoothing amount. How many frames the Smooth option adds between each pair. 4x is silkier and takes longer, and the file gets larger. |
| `wan22-camera` | `camera_speed` | 0.2 to 3.0 | 1.0 | Camera speed. How far the camera travels over the clip. 1 is the usual; lower is a gentle drift, higher a bigger move that can smear the scene. Does nothing for a static camera. |
| `wan22-camera` | `loras` | up to 3 {high, low, strength}, strength -2 to 2 | none | Add-ons (LoRA). Camera, style or motion add-ons made for Wan 2.2 14B. Each needs a high-noise and a low-noise file (put them in the loras folder of your models folder). |
| `wan22-camera` | `interpolation` | 2, 4 (with `smooth`) | 2 | Smoothing amount. How many frames the Smooth option adds between each pair. 4x is silkier and takes longer, and the file gets larger. |
| `hunyuan15-i2v` | `steps` | 8, 12 | 8 | Steps. 12 is the other value the makers recommend; it takes about half as long again for a possible gain in finish. |
| `hunyuan15-i2v` | `sr_noise` | 0.4 to 0.9 | 0.7 | 720p re-imagining. Only used by the 720p size. Lower stays closer to the first pass; higher lets the sharpening pass invent more. |
| `hunyuan15-i2v` | `interpolation` | 2, 4 (with `smooth`) | 2 | Smoothing amount. How many frames the Smooth option adds between each pair. 4x is silkier and takes longer, and the file gets larger. |
| `ace-step` | `key` | none, C major, C minor, C# major, C# minor, D major, D minor, D# major, D# minor, E major, E minor, F major, F minor, F# major, F# minor, G major, G minor, G# major, G# minor, A major, A minor, A# major, A# minor, B major, B minor | none | Key. The musical key. Any lets the song planner choose. Useful when the music must fit with other tracks. |
| `ace-step` | `time_signature` | none, 2, 3, 4, 6 | none | Time signature. The beat pattern: 4/4 for most pop and dance, 3/4 for waltzes, 6/8 for a rolling feel. Any lets the song planner choose. |
| `ace-step` | `vocal_language` | unknown, en, zh, ja, ko, es, fr, de, it, pt, ru (with `lyrics`) | unknown | Language of the lyrics. Set it when the singing comes out in the wrong language; otherwise the language is detected from the lyrics. |
| `ace-step` | `raw` | bool | false | Raw output. Skips ACE-Step's final level-setting (it raises or lowers each song to peak at -1 dB). Loops are still cut as asked, and a song that would clip is turned down just enough. |
| `ace-step` | `format` | wav16, wav24, flac | wav16 | File format. 16-bit WAV is the usual. 24-bit WAV keeps more detail for editing; FLAC is lossless and about half the size. |
| `ace-step` | `steps` | 1 to 20 | 8 | Steps. More is slower. This model is tuned for 8; fewer is a quick draft. |
| `ace-step` | `shift` | 1 to 5 | 3.0 | Shift. How the steps are spread from noise to finished music. This model is tuned for 3. |
| `ace-step` | `lm_temperature` | 0 to 2 (with `thinking`) | 0.85 | Planner variety. How adventurous the song planner is. Lower plans more predictably, higher more surprisingly. Only used while planning is on. |
| `ace-step` | `lm_top_p` | 0 to 1 (with `thinking`) | 0.9 | Planner focus. Lower makes the song planner stick to its most likely choices. Only used while planning is on. |
| `ace-step` | `cover_strength` | 0 to 1 (with `source`) | 1.0 | How closely to follow the recording. Only for a cover. 1 keeps the recording's tune and structure; lower, such as 0.2, gives more room for a new style. |
| `ace-step` | `cover_noise` | 0 to 1 (with `source`) | 0.0 | Keep the melody. Only for a cover. 0 starts from nothing but your style tags; raise it a little (0.1 to 0.25) to bring the recording's melody back. Higher keeps the tune but resists a change of style. |
| `sa3-sfx` | `format` | wav16, wav24, flac | wav16 | File format. 16-bit WAV is the usual. 24-bit WAV keeps more detail for editing; FLAC is lossless and about half the size. |
| `sa3-sfx` | `steps` | 1 to 100 | 8 | Steps. More is slower, and this model gains little past 8; fewer is quicker but rougher. |
| `sa3-sfx` | `cfg` | 0 to 25 | 1.0 | Prompt strength. How strictly to follow the description. 1 is what this model is tuned for; raise it (try 3 to 7) to use the Leave out box. |
| `sa3-sfx` | `negative` | text | none | Leave out. Things the sound should not have, such as music or speech. Only works when Prompt strength is above 1. |
| `sa3-sfx` | `init_strength` | 0 to 1 (with `init_audio`) | 0.5 | How much to change. Only used with a starting sound. 0 stays closest to it (0.1 is a near copy), 1 ignores it and makes a new sound from the description. |
| `sa3-sfx` | `track_type` | bool | true | Sound-effect tag. Starts the description with the tag the model's guide recommends for sound effects. Turn it off if your description already says what kind of sound it is. |
| `chatterbox-turbo` | `temperature` | 0.05 to 2 | 0.8 | Variation. Lower is steadier and flatter, higher is livelier and riskier. Raise it a little if the voice sounds monotone. |
| `chatterbox-turbo` | `repetition_penalty` | 1 to 2 | 1.2 | Repeat guard. Raise it if words loop or stutter. |
| `chatterbox-turbo` | `format` | wav16, wav24, flac | wav16 | File format. 16-bit WAV is the usual. 24-bit WAV keeps more detail for editing; FLAC is lossless and about half the size. |
| `chatterbox-turbo` | `top_p` | 0 to 1 | 0.95 | Top-p. Only the likeliest speech sounds are considered; lower is safer and more uniform. |
| `chatterbox-turbo` | `top_k` | 0 to 1000 | 1000 | Top-k. How many candidates are considered for each sound. 0 turns the limit off. |
| `chatterbox` | `temperature` | 0.05 to 5 | 0.8 | Variation. Lower is steadier and flatter, higher is livelier and riskier. Raise it a little if the voice sounds monotone. |
| `chatterbox` | `repetition_penalty` | 1 to 2 | 1.2 | Repeat guard. Raise it if words loop or stutter. |
| `chatterbox` | `format` | wav16, wav24, flac | wav16 | File format. 16-bit WAV is the usual. 24-bit WAV keeps more detail for editing; FLAC is lossless and about half the size. |
| `chatterbox` | `min_p` | 0 to 1 | 0.05 | Min-p. Drops very unlikely speech sounds. 0.02 to 0.1 works well, and it copes better with a higher Variation. |
| `chatterbox` | `top_p` | 0 to 1 | 1.0 | Top-p. 1 turns it off. Lower keeps only the likeliest speech sounds. |
| `kokoro` | `blend` | text | none | Mix voices. Mix several voices into a new one, replacing the voice chosen above. Write voice names, optionally with weights: Bella:0.6, Sky:0.4. Without weights the voices count equally. Up to 5. |
| `kokoro` | `format` | wav16, wav24, flac | wav16 | File format. 16-bit WAV is the usual. 24-bit WAV keeps more detail for editing; FLAC is lossless and about half the size. |
| `z-image-turbo` | `megapixels` | 0.5 to 2.0 | 1.0 | Size. How many pixels. Bigger takes longer and can repeat things in the picture; about 2 MP is the most a 12 GB card fits. |
| `z-image-turbo` | `steps` | 6 to 12 | 9 | Steps. More is slower and sometimes finer. This model is tuned for 9. |
| `z-image-turbo` | `loras` | up to 3 {file, strength}, strength -2 to 2 | none | Add-ons (LoRA). Styles, characters or concepts trained for Z-Image. Plain LoRAs only (not LoKr or LoHa). |
| `z-image-turbo` | `shift` | 1 to 7 | 3.0 | Shift. Higher favours overall shapes, lower favours fine detail. |
| `z-image-turbo` | `sampler` | res_multistep, euler, dpmpp_2m, er_sde | res_multistep | Sampler. The method that removes noise at each step. res_multistep is what this model was tuned with. |
| `z-image-turbo` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `z-image-control` | `guide_strength` | 0.3 to 1.0 | 0.75 | How closely to follow the guide. Higher follows the guide picture more strictly; lower gives your words more room. The makers suggest 0.65 to 0.8, so 0.75 is the starting point; go lower if the result looks stiff or traced. |
| `z-image-control` | `megapixels` | 0.5 to 2.0 | 1.0 | Size. How many pixels. Bigger takes longer and can repeat things in the picture; about 2 MP is the most a 12 GB card fits. |
| `z-image-control` | `steps` | 6 to 12 | 9 | Steps. More is slower and sometimes finer. This model is tuned for 9. |
| `z-image-control` | `loras` | up to 3 {file, strength}, strength -2 to 2 | none | Add-ons (LoRA). Styles, characters or concepts trained for Z-Image. Plain LoRAs only (not LoKr or LoHa). |
| `z-image-control` | `shift` | 1 to 7 | 3.0 | Shift. Higher favours overall shapes, lower favours fine detail. |
| `z-image-control` | `sampler` | res_multistep, euler, dpmpp_2m, er_sde | res_multistep | Sampler. The method that removes noise at each step. res_multistep is what this model was tuned with. |
| `z-image-control` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `flux-schnell` | `megapixels` | 0.5 to 2.0 | 1.0 | Size. How many pixels. Bigger takes longer and can repeat things in the picture; about 2 MP is the most a 12 GB card fits. |
| `flux-schnell` | `steps` | 1 to 8 | 4 | Steps. More is slower and sometimes finer. This model is tuned for 4. |
| `flux-schnell` | `loras` | up to 3 {file, strength}, strength -2 to 2 | none | Add-ons (LoRA). Styles, characters or concepts trained for FLUX.1. Put .safetensors files in the loras folder of your models folder. |
| `flux-schnell` | `sampler` | euler, dpmpp_2m, deis, heun | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with; heun takes twice as long. |
| `flux-schnell` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `qwen-image-fast` | `steps` | 2 to 6 | 4 | Steps. More is slower and sometimes finer. This model is tuned for 4. |
| `qwen-image-fast` | `megapixels` | 0.5 to 2.0 | 1.7 | Size. How many pixels. Bigger takes longer; about 2 MP is the most a 12 GB card fits. |
| `qwen-image-fast` | `trained_sizes` | bool | false | Use the trained sizes. Qwen-Image's own size for each shape (1664 x 928 for 16:9 and so on), instead of the Size above. |
| `qwen-image-fast` | `shift` | 2 to 5 | 3.0 | Shift. Higher if pictures come out blurry or dark, lower for more fine detail. |
| `qwen-image-fast` | `sampler` | euler, res_multistep, dpmpp_2m | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with. |
| `qwen-image-fast` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `qwen-image` | `steps` | 6 to 10 | 8 | Steps. More is slower and sometimes finer. This model is tuned for 8. |
| `qwen-image` | `megapixels` | 0.5 to 2.0 | 1.7 | Size. How many pixels. Bigger takes longer; about 2 MP is the most a 12 GB card fits. |
| `qwen-image` | `trained_sizes` | bool | false | Use the trained sizes. Qwen-Image's own size for each shape (1664 x 928 for 16:9 and so on), instead of the Size above. |
| `qwen-image` | `shift` | 2 to 5 | 3.0 | Shift. Higher if pictures come out blurry or dark, lower for more fine detail. |
| `qwen-image` | `sampler` | euler, res_multistep, dpmpp_2m | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with. |
| `qwen-image` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `flux-dev` | `guidance` | 1 to 6 | 3.5 | Follow the prompt. Higher sticks closer to your words and adds contrast; lower looks more natural. |
| `flux-dev` | `steps` | 8 to 40 | 20 | Steps. More is slower and sometimes finer. 20 is recommended. |
| `flux-dev` | `megapixels` | 0.5 to 2.0 | 1.0 | Size. How many pixels. Bigger takes longer and can repeat things in the picture; about 2 MP is the most a 12 GB card fits. |
| `flux-dev` | `loras` | up to 3 {file, strength}, strength -2 to 2 | none | Add-ons (LoRA). Styles, characters or concepts trained for FLUX.1. Put .safetensors files in the loras folder of your models folder. |
| `flux-dev` | `max_shift` | 0.5 to 2.0 | 1.15 | Shift. Higher spends more of the steps on overall shapes, lower on fine detail. |
| `flux-dev` | `sampler` | euler, dpmpp_2m, deis, heun | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with; heun takes twice as long. |
| `flux-dev` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `flux-krea` | `guidance` | 1 to 6 | 4.5 | Follow the prompt. Higher sticks closer to your words and adds contrast; lower looks more natural. |
| `flux-krea` | `steps` | 8 to 40 | 28 | Steps. More is slower and sometimes finer. 28 is recommended. |
| `flux-krea` | `megapixels` | 0.5 to 2.0 | 1.0 | Size. How many pixels. Bigger takes longer and can repeat things in the picture; about 2 MP is the most a 12 GB card fits. |
| `flux-krea` | `loras` | up to 3 {file, strength}, strength -2 to 2 | none | Add-ons (LoRA). Styles, characters or concepts trained for FLUX.1. Put .safetensors files in the loras folder of your models folder. |
| `flux-krea` | `max_shift` | 0.5 to 2.0 | 1.15 | Shift. Higher spends more of the steps on overall shapes, lower on fine detail. |
| `flux-krea` | `sampler` | euler, dpmpp_2m, deis, heun | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with; heun takes twice as long. |
| `flux-krea` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |
| `krea-2` | `megapixels` | 0.5 to 2.0 | 1.0 | Size. How many pixels. Bigger takes longer and can repeat things in the picture; about 2 MP is the most a 12 GB card fits. |
| `krea-2` | `steps` | 4 to 12 | 8 | Steps. More is slower and sometimes finer. This model is tuned for 8. |
| `krea-2` | `sampler` | euler, res_multistep, dpmpp_2m | euler | Sampler. The method that removes noise at each step. "euler" is what this model was tuned with. |
| `krea-2` | `scheduler` | simple, beta, sgm_uniform, normal | simple | Scheduler. How the steps are spread from noise to finish. "simple" is what this model was tuned with. |

<!-- controls:end -->

## From another computer on the network

An agent on another computer can use this IrisEcho over the local network the
way a phone does, once the person running it lets it in. The command line and
this guide's paths are for this computer only; from elsewhere, everything goes
through HTTP.

1. The person turns on Settings → Phone and network access, chooses Pair a
   phone, and gives you the pairing link (`http://HOST:7789/pair?code=CODE`).
   The code works once and expires after five minutes.
2. Open that link with a client that keeps cookies. The answer sets the cookie
   `irisecho_device`; send it with every call from then on. It lasts until the
   person revokes the device in Settings.

```sh
curl -c jar.txt -L "http://HOST:7789/pair?code=CODE"
curl -b jar.txt http://HOST:7789/api/models
curl -b jar.txt -H "X-IrisEcho: 1" -H "Content-Type: application/json" -d @job.json http://HOST:7789/api/jobs
curl -b jar.txt -o result.png http://HOST:7789/api/jobs/JOB_ID/outputs/0
```

In Windows PowerShell, `curl` is another command: type `curl.exe`.

- The calls and job `params` are the ones in the tables above, and anything
  that is not a GET still needs the header `X-IrisEcho: 1`.
- A paired device can read models, settings, the system and jobs; queue,
  cancel, delete and favourite jobs; download outputs from
  `/api/jobs/{id}/outputs/{n}`; and upload with `POST /api/uploads`. Nothing
  else: setting up or downloading models, accepting licenses and changing
  settings or folders are for the computer running IrisEcho, and answer 403.
- A job's `outputs[n].path` names a file on the other computer; fetch it from
  `/api/jobs/{id}/outputs/{n}`. Pictures and sounds for a job must be uploaded:
  the `uploads` folder is out of reach.
- 401 with `"pair": true` means the cookie is missing or was revoked: ask the
  person for a new code. After five wrong codes, pairing pauses for a minute.
- The connection is plain HTTP. Anyone who can see the network traffic can see
  the cookie and everything you send, so use it only on a network the person
  trusts, and keep the cookie out of logs, prompts and commits.

## Where things are

Inside the data folder:

- `outputs/YYYY-MM-DD/` everything made, unless the person moved it in Settings
- `uploads/` pictures and sounds given to jobs
- `logs/` `core.log`, `jobs.log` (a traceback for each failed job), one log per engine
- `server.json` port and token of the running app

## When something goes wrong

- *"... is not ready yet"* or HTTP 409: the model needs setting up. `needs`
  says what: `engine`, `download`, `license`, or `unsupported` (this computer
  cannot run it).
- A job that fails carries the reason in `error`; the traceback is in
  `logs/jobs.log`.
- *"did not start"*: an engine's own program failed to launch. Its log in
  `logs/` has the last lines it printed. Every job waiting for that engine is
  failed at once rather than left to wait.
- Image models want plain descriptive sentences. `irisecho write "rough idea"
  --for MODEL` rewrites a draft the way that model likes it.
