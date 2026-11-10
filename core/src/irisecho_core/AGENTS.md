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

Each finished file is printed on stdout as `label: path`. Progress, warnings
and failures go to stderr, so stdout holds only results. Add `--json` to a
generating command for one JSON object per job on stdout instead:

```json
{"id": "3f9c2a71b0de", "label": "", "status": "done", "model": "z-image-turbo", "outputs": ["...\\lighthouse-3f9c2a.png"], "seed": 123, "error": null, "needs": [], "warnings": []}
```

- `label` is the cue id (empty without one). `outputs` are the files, in the
  `--out` folder if you gave one. `error` is the reason a job failed.
- `warnings` names params the model does not use (they have no effect). The same
  lines are printed on stderr as `warning: ...`.
- A job that never ran still gets an object: `id` is null (or the job's id if
  it was queued), `status` is `failed`, `error` says why, and `needs` says what
  is missing if the model is not ready (`license`, `unsupported`, `engine` or
  `download`; empty otherwise). This covers a license stop, a model that cannot
  run on this computer, a blocked download or a failed engine install, and a
  value that was refused. If the command could not start any job, there is one
  object per job it planned.
- `3d` prints one object of the same shape, with `glb`, `printable` and
  `reasons` added.

Exit codes: 0 when everything finished; 1 when it could not be done (a job
failed, a model is not ready, the app did not answer, the connection was lost);
2 when the command line or a value in it is wrong, so that changing the call
fixes it (an unknown model, a number out of range, a file that is not there, a
flag a model does not take). Values are checked before anything is downloaded
or started, so a typo costs nothing.

## One queue, one GPU

If the IrisEcho window is open, the command line hands its work to that running
app: jobs join the same queue, appear in the window, and share the one model
held on the GPU. If it is not open, the command starts the engines itself and
stops them when it finishes. You do not choose; both behave the same.

Only one IrisEcho works on a data folder at a time. A command that finds one
running but gets no answer within 15 seconds (another command is still working
on its own, or the app is starting or stuck) stops with `IrisEcho is already
running on this data folder ... but does not answer` and exit code 1: wait and
run it again, do not look for a way around it. `irisecho serve --port N` fails
when port N is taken instead of picking another.

A command waits for its job as long as the job runs; there is no time limit,
because a video or a long song can take many minutes. Give it a timeout of your
own if you need one. If the connection to the running app drops, the command
tries a few times, then stops with `Lost the connection to IrisEcho` and exit
code 1. A job already queued keeps running in the app; find it with
`irisecho api GET "jobs?limit=5"`.

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
are working for told you to for that model.** Without it the command stops,
prints the license, and says: "This model's license must be accepted by the
person. Ask them; with their OK, re-run with `--accept-license` (or accept it in
the app)." Every command that sets a model up (`setup`, `image`, `say`, `clone`,
`music`, `sfx`, `3d`, `write`) takes `--accept-license` and stops the same way.
Non-commercial models (`irisecho models` marks them) must not be used for
commercial work.

A model that has no command (video, edits, upscaling) answers a job with 409
and `"needs": ["license"]` while its license is not accepted. Do not accept it
yourself: tell the person which model and why, and after their OK run
`irisecho setup MODEL --accept-license` (or they accept it in the app), then
queue the job again. `GET /api/models` gives each model's `license` (`name`,
`url`, `summary`).

If the environment you run in has `HF_TOKEN` set, IrisEcho sends that token to
Hugging Face with every download, ahead of one the person saved in the app. A
command that starts a download says so on stderr ("Using the Hugging Face token
from the HF_TOKEN environment variable."). `GET /api/bootstrap` reports it as
`credentials.huggingface_source`: `environment`, `saved` or null. The token itself
is never printed or returned.

## Commands

| Command | Makes | Main options |
|---|---|---|
| `image PROMPT` | pictures | `--model` (default `z-image-turbo`), `--aspect 1:1\|4:3\|3:4\|16:9\|9:16\|3:2\|2:3`, `--count`, `--seed`; `--from picture.png` (start from a picture) with `--denoise` (0.05 to 1, default 0.2) for the FLUX models and `qwen-image-21` only, and `--size 1920x1088` for the FLUX models only; `--guide picture.png` and `--guide-type canny\|depth\|pose\|as_is` with `z-image-control` |
| `say TEXT` | narration | `--voice` (default `af_heart`), `--speed`, `--raw`, `--file cues.txt` |
| `clone TEXT --ref clip.wav` | speech in a sampled voice | `--model chatterbox-turbo\|chatterbox`, `--takes`, `--seed`, `--exaggeration` and `--cfg` (`chatterbox` only, 0.5 each by default), `--raw`, `--file cues.txt`; the clip is 10 to 15 s of one voice (WAV, MP3, FLAC or OGG; Turbo needs more than 5 s) |
| `music TAGS` | music | `--model ace-step\|ace-step-xl\|ace-step-xl-sft\|yue2\|minimax-music3\|sa3-medium`, `--seconds`, `--bpm`, `--loop`, `--lyrics` or `--lyrics-file`, `--takes`, `--seed`, `--no-thinking`, `--file cues.txt` |
| `sfx PROMPT` | sound effects | `--seconds` (0.5 to 30), `--takes` (default 3), `--seed`, `--raw`, `--file cues.txt` |
| `write DRAFT` | a better prompt, printed on stdout | `--for MODEL`, `--image picture.png` |
| `3d PICTURE` | a 3D model | `--back picture.png`, `--model auto\|pixal3d\|pixal3d-single\|trellis2`, `--detail standard\|high`, `--faces 500000\|100000\|20000`, `--no-textures`, `--keep-openings`, `--format glb\|stl\|3mf\|obj\|ply`, `--height MM`, `--seed`; `--back` goes with `auto` or `pixal3d` (the other two build from one picture and refuse it) |
| `models` | a list of models | `--json`; `--controls [MODEL]` prints a model's controls and limits (every model's without `MODEL`; add `--json` for data) |
| `setup MODEL` | nothing: installs and downloads | `--accept-license` |

The commands that make something (`image`, `say`, `clone`, `music`, `sfx`, `3d`,
`write`) and `setup` all take `--accept-license` (see Setting up). The generating
commands also take `--set NAME=VALUE` (advanced controls, below), `--out FOLDER`
(copy results there; with a cue sheet, files are named by cue id) and `--json`.
`irisecho COMMAND --help` lists everything.

Cue sheets are UTF-8 text files (a byte-order mark at the start, which Windows
PowerShell 5.1 writes, is fine), one job per line, `#` for comments:

- `say`, `clone` and `sfx`: `id|text`
- `music`: `id|seconds|bpm|tags` (bpm may be empty)

Only clone a voice the person has the right to use. Cloned speech carries an
inaudible watermark. Long text is spoken sentence by sentence and joined, so a
whole paragraph comes out in one file.

Speech is trimmed and brought to -19 LUFS with peaks under -1 dBFS (`--raw`
keeps the model's own output). Music plans tempo, key and form with ACE-Step's
song planner first, as ACE-Step itself does; `--no-thinking` skips it for faster
drafts. What the planner wrote comes back in each output as `plan` (its own
`caption`, `bpm`, `keyscale`, `timesignature`, `duration`, `language`): the music is
made from that, so check it when a take ignores part of your tags. The planner
writes nothing when you set the tempo, key, time signature and duration yourself
(ACE-Step then skips planning), so those jobs have no `plan`; it never rewrites
lyrics. Words like "no drums" are a weak way to keep something out: leave the
instrument unmentioned instead. `ace-step-xl` (ACE-Step's 4B renderer and 4B song planner) makes richer, more
detailed music in about twice the time; `ace-step-xl-sft` refines each song in 50
steps with prompt guidance (`cfg`) for the most detail, several times slower. Both
are separate downloads (a 19 GB renderer each, plus an 8 GB planner they share) and run on a 12 GB
card by keeping only the model at work on it, the others waiting in system memory
(about 23 GB of it, so 32 GB of RAM). Several takes of a long song are made in
passes: `ace-step-xl` holds up to 8 minutes of music in a pass (two 4-minute
takes), `ace-step-xl-sft` up to 4 (one 3-minute take, two of 2 minutes). Each
pass plans its own songs, so only takes from the same pass share a `plan`, and a
pass that runs out of graphics memory is made again with half the takes. On a
12 GB card with the model loaded, 60 s of `ace-step` takes about 16 s, four 60 s
takes of `ace-step-xl` about 100 s and four 3-minute takes of `ace-step-xl-sft`
about 9 minutes; the planner is more than half of that for the first two and
about 40% for SFT. An ACE-Step job with the same settings and `seed` gets the same
plan and the same music again, planner on or off (the files match to the ear, not
bit for bit: the renderer itself differs slightly from run to run).

Three more music models run in the image engine (ComfyUI) and suit instrumentals
best: `yue2` (non-commercial license; plans a melody-and-chord score first, which
`--no-thinking` skips; lyrics in English or Mandarin), `minimax-music3` (wide
dynamics; a caption already written as `Global Metadata:` / `Vocal Details:` /
`Arrangement:` is used as it is) and `sa3-medium` (instrumentals only, in seconds).
Give them a few descriptive tags; `--bpm` becomes part of the description. They
take `format`, `trim_end`, `steps`, `takes`, `loop` and lyrics as ACE-Step does,
but cannot start from a recording. YuE2 and MiniMax end a piece when it feels
finished: asked for 240 s, an instrumental came back at about 2 minutes from YuE2
and about 2.5 from MiniMax. Stable Audio makes exactly the length asked, up to
360 s (6 minutes; the other two stop at 300), so use it for long beds. On a 12 GB
card, once loaded, a new prompt takes it about 2 s for 60 s of music, 18 s for
240 s and 26 s for 360 s. While the image engine stays loaded, a `sa3-medium` job
with the same params and the same explicit `seed` as the one before returns the
audio it already made at once (well under a second, as a new job and file); a new
seed, or no seed (a random one is picked), always renders. `--loop` (with
`--bpm`) cuts whole bars from the first downbeat it hears and blends the seam.
Songs often end a few seconds early, padded with near-silence; the `trim_end`
control cuts that tail. Video sound and picture: `video_quality` (`high`, `max`)
encodes finer than the compact default, and `even_sound` on `ltx25` brings quiet
ambience up to -19 LUFS. Sound effects keep their own loudness, so a pin drop stays
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
model's own frame for 3D programs. With `--json` the object has the usual
fields plus `glb` (the GLB, before any conversion), `printable` and `reasons`
(why it is not, when it is not). `--back` with `--model trellis2` or
`pixal3d-single` is refused with exit code 2: those build from one picture.

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
finished job; the exit code is 0 only if it finished. Without `--wait` it is 0
for a success answer and 1 for an error answer (the answer is still printed);
a body that is not JSON exits 2. A body is UTF-8 text (`-` reads stdin as UTF-8,
whatever the console's code page). A job's `outputs[n].path` is the file on disk. Quoting JSON on a command line differs
between shells, so a body file and `-` is the dependable way.

| Call | Does |
|---|---|
| `GET /api/models` | every model: `id`, `kind`, `ready`, `needs`, license, voices under `options` |
| `POST /api/models/{id}/prepare` | install and download what a model needs |
| `POST /api/models/{id}/verify` | re-hash a model's files against their recorded hashes: per file `ok`, `wrong_hash` or `missing`; changes nothing, and reads several GB, so it takes a while |
| `POST /api/jobs` with `{"model", "params"}` | queue a job; 400 for a value out of range or a body that is not a JSON object with a `model`, 404 for an unknown model, 409 with `needs` if the model is not ready |
| `GET /api/jobs/{id}` | `status` (`queued`, `running`, `done`, `failed`, `cancelled`, `interrupted`), `progress`, `message`, `error`, `outputs` |
| `GET /api/jobs?q=fox&kind=image&limit=20` | search what has been made; `limit` is 1 to 500 (400 outside that) |
| `POST /api/jobs/{id}/cancel` | stop a queued or running job; 404 for an unknown id, 409 with the job's `status` if it has already ended |
| `DELETE /api/jobs/{id}` | remove a finished job and its files; 404 for an unknown id, 409 while it is queued or running or if a file is open in another program (nothing is lost; close it and retry) |
| `POST /api/jobs/{id}/favorite` with `{"favorite": true}` | mark or unmark a job; 404 for an unknown id |
| `POST /api/uploads` (multipart `file`) | add a picture or sound for a job to use; returns its `id`. Up to 64 MB (413); the kind is read from the file's first bytes: PNG, JPEG, WebP, MP4, WAV, MP3, FLAC or OGG, else 415. Uploads no job mentions are deleted after a week |
| `GET /api/system` | GPU (`hardware.problem` says why the card could not be read, `hardware.note` that the card chosen in Settings is not in use), memory in use right now (`vram`: `used_mb` and `free_mb` per card, the whole card), what is loaded, queue length |
| `POST /api/system/unload` | free the GPU for another program; 409 while a job is running |
| `GET /api/jobs/{id}/outputs/{n}/export?format=stl&height_mm=80` | a 3D model in `glb`, `stl`, `3mf` (millimetres, upright on the plate), `obj` (zip) or `ply` |
| `GET /api/jobs/{id}/outputs/{n}/preview` | the still picture of a 3D model |

Errors always look like `{"error": "plain message"}`, with extra fields where they help (`needs` on
a 409 for a model that is not ready, `status` on a 409 for a job). `detail` repeats
`error` for scripts written for 0.2.2 and will go in a later release.

Job `params` by kind of model:

| Kind | Models | Params |
|---|---|---|
| image | `z-image-turbo`, `flux-schnell`, `qwen-image-fast`, `qwen-image`, `flux-dev`, `flux-krea`, `krea-2`, `qwen-image-21`, `z-image-control` | `prompt`, `aspect`, `seed`; `image1` and `denoise` (0.05 to 1, default 0.2) to start from a picture, for `flux-schnell`, `flux-dev`, `flux-krea` and `qwen-image-21` only (the others do not read `image1`); `z-image-control` needs a `guide` picture and takes `guide_type` (`canny`, `depth`, `pose`, or `as_is` for a ready-made map) |
| edit | `qwen-edit-fast`, `qwen-edit`, `qwen-image-21-edit`, `flux-kontext` | `prompt`, `image1` (and `image2`, `image3` for `qwen-edit` and `qwen-edit-fast`; up to `image10` for `qwen-image-21-edit`), `seed`; `box` or `mask` to change only an area (Qwen models) |
| upscale | `seedvr2`, `seedvr2-7b` | `image1`, `scale` (2, 3 or 4), `retain` (true keeps the original size), `seed` |
| video | `ltx25`, `wan22-t2v`, `wan22-i2v`, `wan22-i2v-1022`, `hunyuan15-i2v`, `wan22-camera` | `prompt`, `aspect`, `seconds` (2 to 5), `size` (`standard`, `large`, or `720p` on a 12 GB card; `hunyuan15-i2v` has no `large`), `smooth`, and a `start` and/or `end` frame: required for the `i2v` models (`hunyuan15-i2v` takes `start` only), optional for `ltx25`; `wan22-camera` needs `start` and takes `camera` (`static`, `pan_up`, `pan_down`, `pan_left`, `pan_right`, `zoom_in`, `zoom_out`, `clockwise`, `anticlockwise`) |
| voice | `kokoro` | `text`, `voice`, `speed`, `clean` |
| clone | `chatterbox-turbo`, `chatterbox` | `text`, `ref`, `consent` (must be `true`), `takes` (1 to 4), `seed`, `clean` (false keeps the model's own output); `exaggeration` and `cfg` on `chatterbox` |
| music | `ace-step`, `ace-step-xl`, `ace-step-xl-sft`, `yue2`, `minimax-music3`, `sa3-medium` | `prompt` (style tags), `duration` (seconds: 10 to 480 on the ACE-Step models, 10 to 360 on `sa3-medium`, 10 to 300 on `yue2` and `minimax-music3`), `bpm`, `lyrics`, `takes` (1 to 4), `loop` (needs `bpm`), `thinking` (default `true`), `seed`; from a recording (ACE-Step models only): `source` (an upload id of a WAV, MP3, FLAC or OGG) and `task` `cover`, `repaint` (with `region` `[start_s, end_s]`) or `extend` (with `extend_by` seconds) |
| sfx | `sa3-sfx` | `prompt`, `duration` (0.5 to 30), `takes` (1 to 4, default 3), `trim` (default true; false is the command line's `--raw`), `seed`; `init_audio` (an upload id) for a variation of a sound, plus `region` `[start_s, end_s]` to make only that part again |
| video-upscale | `seedvr2-video` | `video` (an upload id of an MP4 clip), `short_side` (720 to 1080, default 1080), `seed` |
| model3d | `trellis2`, `pixal3d`, `pixal3d-single` | `front` (and `back` for `pixal3d`; `pixal3d-single` builds from `front` alone), `detail` (`standard` or `high`), `faces` (500000, 100000 or 20000), `textures` (default true), `openings` (`close` or `keep`), `seed` |
| views3d | `object-views` | `image1`, `level` (`auto`, `best` or `keep`), `seed` |

`image1`, `image2`, `image3`, `mask`, `guide`, `start`, `end`, `ref`, `source`, `init_audio`, `video`, `front` and `back`
are upload ids.
An agent on this computer can skip the upload call: copy the file into the
`uploads` folder inside the data folder under a new name and pass that name.
The one rule for a name, for every kind of upload: 8 to 64 lowercase hex digits
(0 to 9, a to f), a dot, and the file's extension, 2 to 5 lowercase letters or
digits (`3fa2b1c4.png`, `5d0e7a91c3b84f26.wav`). Any other name is refused, with a
message that says so; no folder part is accepted. (`irisecho` commands stage
their pictures and recordings this way for you.)

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

The FLUX image models (`flux-schnell`, `flux-dev`, `flux-krea`) and
`qwen-image-21` take an optional `image1` to redraw, with `denoise` (0.05 to 1,
default 0.2) for how much changes. The FLUX models also take `width` and
`height` (multiples of 16, at most 1920 x 1088 in area; without them the
picture keeps its own aspect at about one megapixel).
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
  A value out of range is refused with exit code 2 and a message that states the range.
- Finding out what a model takes: `irisecho models --controls MODEL` prints its
  controls (name, type, range or choices, recommended value, one line of help) and
  its `limits`; `irisecho models --controls` prints every model's. With `--json`
  you get `{"id", "kind", "limits", "controls": [...]}` for one model, a list of
  those for all. This is the full reference; the list at the end of this section
  only names them.
- API: put the control in `params`. `GET /api/models` lists each model's
  `controls` (name, type, range or options, `default`, help) and `limits`. A value
  out of range is refused with a 400 and a message that states the range
  (`takes`, `duration`, `seconds` and `loop` without `bpm` too: nothing is
  clamped or dropped quietly). `limits` holds the range and default of `takes`,
  `duration` or `seconds` where the model has one: `{"takes": {"min": 1, "max": 4, "default": 1}}`.
  A param the model does not use has no effect and is named in the response's
  `warnings` (`irisecho` prints them too).
- Add-ons (LoRAs): `.safetensors` files in the `loras` folder of the models
  folder (or of a linked models folder). `GET /api/loras?family=flux` lists them
  with the model family each fits (`flux`, `zimage`, `wan14b`, ...) and any
  trigger words. Up to 3 per job, strength -2 to 2. Wan takes pairs:
  `{"high": FILE, "low": FILE, "strength": 1}`. A file made for another family
  is refused. Qwen-Image and Qwen Edit take no add-ons; Qwen-Image 2.1 takes
  add-ons made for 2.1 (family `qwen-image-21`).
- Changing only an area (`qwen-edit`, `qwen-edit-fast`, `qwen-image-21-edit`): `box`
  `[left, top, right, bottom]` as fractions of Picture 1, or `mask` (an upload id
  of a picture, white where it may change). Outside the area the result is
  Picture 1's own pixels. `qwen-image-21-edit` takes up to three boxes as a list
  (`[[...], [...]]`): the model is shown a red, a blue and a green circle around
  them, in that order, so the prompt can say "the mug in the red circle".
- `qwen-image-21-edit` reads every picture with its transparency: a transparent PNG can
  be edited and stays transparent, and "remove the background" gives a cut-out.
- Presets: `GET /api/presets`, `PUT /api/presets` with `{"model", "name",
  "values"}`, `DELETE /api/presets?model=M&name=N`. They are kept in
  `presets.json` in the data folder.
- Settings in pictures: with the "Keep prompts out of image files" setting off,
  each PNG carries the prompt and settings (A1111's `parameters` text, which
  Civitai and Forge read, plus IrisEcho's own). `GET /api/uploads/{id}/settings`
  reads them back from an uploaded picture.
- To compare one setting, run the same job with the same `seed` and only that
  control changed.

Every model, what it is for, its limits and the names of its advanced controls
(values, ranges and help: `irisecho models --controls MODEL`):

<!-- controls:start -->
<!-- Generated by scripts/gen_models_md.py from the registry. Do not edit. -->

- `qwen-edit-fast` (edit): Change a picture by describing the change. Four steps, about fifteen seconds. Controls: `steps`, `megapixels`, `keep_size`, `feather`, `shift`, `sampler`, `scheduler`.
- `qwen-edit` (edit): The careful version. Eight steps for finished work; combine up to three pictures. Controls: `steps`, `megapixels`, `keep_size`, `feather`, `shift`, `sampler`, `scheduler`.
- `qwen-image-21-edit` (edit): Qwen-Image 2.1 editing a picture, up to 4 MP. Combine up to ten pictures, or change up to three marked areas. Non-commercial. Controls: `steps`, `megapixels`, `feather`, `ref_megapixels`, `transparent`, `loras`, `cache`, `sampler`, `scheduler`.
- `flux-kontext` (edit): Restyle or retouch while keeping what matters. Say only what should change. Non-commercial. Controls: `guidance`, `steps`, `loras`, `sampler`, `scheduler`.
- `seedvr2` (upscale): Restores detail and enlarges 2, 3 or 4 times. No prompt needed. Controls: `color`, `tile`.
- `seedvr2-7b` (upscale): The larger SeedVR2. Slower, with finer restored detail than the standard one, and it needs an 8 GB download. Controls: `color`, `tile`.
- `seedvr2-video` (video-upscale): Restores detail in a whole clip and enlarges it to 1080p. Experimental; it can make faces look plastic and add grit and flicker, so compare it with a plain resize. Controls: `color`, `video_quality`.
- `pixal3d` (model3d): Builds a 3D model from a front and a back picture taken at eye level; IrisEcho can make the back for you. The most complete shapes and the truest backs. Controls: `triangles`, `texture_size`, `smoothing`, `crease`, `keep_small`, `steps`, `guidance`.
- `trellis2` (model3d): Builds a 3D model from one picture taken from any angle. Keeps your object's details and stands it upright; the back is guessed. Controls: `triangles`, `texture_size`, `smoothing`, `crease`, `keep_small`, `steps`, `guidance`.
- `pixal3d-single` (model3d): Builds a 3D model from one picture, with Pixal3D's own shape model and a camera-angle estimate. A different take to compare with TRELLIS.2; for eye-level pictures. Needs a 6 GB download. Controls: `triangles`, `texture_size`, `smoothing`, `crease`, `keep_small`, `steps`, `guidance`.
- `object-views` (views3d): Makes the eye-level front and the matching back view that Pixal3D builds from, and checks that they fit together. Uses the same files as Qwen Edit. Controls: `back_words`.
- `ltx25` (video): Picture and sound in one take, from a description, a first frame, a last frame or both. The fastest; best with a long, detailed prompt. Limits: seconds 2 to 5 (default 3). Controls: `motion`, `start_strength`, `end_strength`, `sound`, `sampler`, `interpolation`, `even_sound`, `video_quality`.
- `wan22-i2v` (video): Brings a still to life. Give a first frame, a last frame, or both. Limits: seconds 2 to 5 (default 3). Controls: `loras`, `interpolation`, `video_quality`.
- `wan22-i2v-1022` (video): Bigger camera moves that keep faces intact. A last frame sets where the move ends. Limits: seconds 2 to 5 (default 3). Controls: `distill_high`, `distill_low`, `loras`, `interpolation`, `video_quality`.
- `wan22-t2v` (video): A short clip from a description alone. Limits: seconds 2 to 5 (default 3). Controls: `distill_high`, `distill_low`, `loras`, `interpolation`, `video_quality`.
- `wan22-camera` (video): Moves the camera the way you choose (pan, zoom, roll) through the scene of your first frame. Needs a first frame and a 30 GB download. Limits: seconds 2 to 5 (default 3). Controls: `camera_speed`, `loras`, `interpolation`, `video_quality`.
- `hunyuan15-i2v` (video): The most natural faces and hands from a first frame. Slower than Wan, and silent. Not licensed in the EU, UK or South Korea. Limits: seconds 2 to 5 (default 3). Controls: `steps`, `sr_noise`, `interpolation`, `video_quality`.
- `ace-step` (music): Beds, jingles and stingers from a few style tags. Instrumental or with lyrics. Limits: takes 1 to 4 (default 1), duration 10 to 480 (default 30). Controls: `key`, `time_signature`, `vocal_language`, `raw`, `format`, `trim_end`, `steps`, `shift`, `lm_temperature`, `lm_top_p`, `cover_strength`, `cover_noise`.
- `ace-step-xl` (music): Richer, more detailed songs from ACE-Step's 4B renderer and its strongest song planner. About twice as slow. Limits: takes 1 to 4 (default 1), duration 10 to 480 (default 30). Controls: `key`, `time_signature`, `vocal_language`, `raw`, `format`, `trim_end`, `steps`, `shift`, `lm_temperature`, `lm_top_p`, `cover_strength`, `cover_noise`.
- `ace-step-xl-sft` (music): The most detailed ACE-Step, refining each song in 50 steps with prompt guidance. Several times slower; for finished pieces. Limits: takes 1 to 4 (default 1), duration 10 to 480 (default 30). Controls: `key`, `time_signature`, `vocal_language`, `raw`, `format`, `trim_end`, `steps`, `shift`, `cfg`, `lm_temperature`, `lm_top_p`, `cover_strength`, `cover_noise`.
- `yue2` (music): Full songs with clean, lively mixes; plans a melody and chords first. English and Mandarin. Limits: takes 1 to 4 (default 1), duration 10 to 300 (default 30). Controls: `format`, `trim_end`, `steps`.
- `minimax-music3` (music): Songs and instrumentals up to 5 minutes with wide dynamics, from a detailed description. Limits: takes 1 to 4 (default 1), duration 10 to 300 (default 30). Controls: `format`, `trim_end`, `steps`.
- `sa3-medium` (music): Instrumentals up to 6 minutes in seconds; strong on jazz, orchestral and lo-fi beds. No vocals. Limits: takes 1 to 4 (default 1), duration 10 to 360 (default 30). Controls: `format`, `trim_end`, `steps`.
- `sa3-sfx` (sfx): One-shot sound effects in seconds. Foley, UI sounds, impacts, ambience. Limits: takes 1 to 4 (default 3), duration 0.5 to 30 (default 3). Controls: `format`, `steps`, `cfg`, `negative`, `init_strength`, `track_type`.
- `chatterbox-turbo` (clone): A voice from a short sample, with performed reactions like [laugh] and [sigh]. English. Limits: takes 1 to 4 (default 1). Controls: `temperature`, `repetition_penalty`, `format`, `top_p`, `top_k`.
- `chatterbox` (clone): A voice from a short sample, with dials for intensity and pacing. English. Limits: takes 1 to 4 (default 1). Controls: `temperature`, `repetition_penalty`, `format`, `min_p`, `top_p`.
- `prompt-writer` (prompt): Rewrites a rough idea as a prompt the chosen model responds to, or describes a picture as one. Qwen3-VL 4B.
- `kokoro` (voice): Narration in 28 preset English voices. Very fast, identical delivery every time. Controls: `blend`, `format`.
- `z-image-turbo` (image): Quick, sharp pictures in 9 steps. Strong at photographs. Controls: `megapixels`, `steps`, `loras`, `shift`, `sampler`, `scheduler`.
- `z-image-control` (image): Draws a new picture that follows the outline, depth or pose of a picture you give it. Same quick Z-Image Turbo, plus a 3 GB guide model and two small readers (depth and pose, 2 GB). Controls: `guide_strength`, `megapixels`, `steps`, `loras`, `shift`, `sampler`, `scheduler`.
- `flux-schnell` (image): Four steps to a finished image. Good for illustration and quick ideas. Controls: `megapixels`, `steps`, `loras`, `sampler`, `scheduler`.
- `qwen-image-fast` (image): Qwen-Image's text and layout in four steps. Use it for drafts, then switch to Qwen-Image. Controls: `steps`, `megapixels`, `trained_sizes`, `shift`, `sampler`, `scheduler`.
- `qwen-image` (image): The one to use for words in pictures. Logos, signs, posters, labels. Eight steps. Controls: `steps`, `megapixels`, `trained_sizes`, `shift`, `sampler`, `scheduler`.
- `qwen-image-21` (image): Qwen's newer model. Long, exact lettering and fine detail up to 4 MP in eight steps. Non-commercial. Controls: `steps`, `megapixels`, `trained_sizes`, `transparent`, `loras`, `sampler`, `scheduler`.
- `flux-dev` (image): Detailed images in 20 steps. Works best with a full description. Non-commercial. Controls: `guidance`, `steps`, `megapixels`, `loras`, `max_shift`, `sampler`, `scheduler`.
- `flux-krea` (image): Photoreal without the glossy "AI look". Non-commercial. Controls: `guidance`, `steps`, `megapixels`, `loras`, `max_shift`, `sampler`, `scheduler`.
- `krea-2` (image): Photographs with natural skin, hands and fur, even from a short prompt. Eight steps, about ten seconds. Controls: `megapixels`, `steps`, `sampler`, `scheduler`.

<!-- controls:end -->

## From another computer on the network

An agent on another computer can use this IrisEcho over the local network the
way a phone does, once the person running it lets it in. The command line and
this guide's paths are for this computer only; from elsewhere, everything goes
through HTTP.

1. The person turns on Settings → Phone and network access, chooses Pair a
   phone, and gives you the pairing link (`http://HOST:7789/pair?code=CODE`).
   The code works once and expires after five minutes.
2. Send the code to `/pair` as a form (opening the link in a browser only shows
   a page with a Pair button, so a link preview cannot use the code up). The
   answer sets the cookie `irisecho_device`; send it with every call from then
   on. It lasts until the person revokes the device in Settings.

```sh
curl -c jar.txt -d "code=CODE" http://HOST:7789/pair
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
  person for a new code. After five wrong codes from one address (or twenty from all), pairing pauses for a minute.
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
  cannot run it). On the command line the same words are `needs` in the
  `--json` object. For `license`, see Setting up: it is the person's to accept.
- A command that exits 2 was given something wrong; its message says what and
  often the range. Fix the call, do not retry it unchanged.
- A job that fails carries the reason in `error`; the traceback is in
  `logs/jobs.log`.
- *"did not start"*: an engine's own program failed to launch. Its log in
  `logs/` has the last lines it printed. Every job waiting for that engine is
  failed at once rather than left to wait.
- Image models want plain descriptive sentences. `irisecho write "rough idea"
  --for MODEL` rewrites a draft the way that model likes it.
