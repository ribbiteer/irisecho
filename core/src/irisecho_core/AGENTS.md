# Using IrisEcho from a script or a coding agent

IrisEcho makes images, image edits, video, speech, cloned voices, music and
sound effects on this computer's own GPU. Its command line and local API cover
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
| `image PROMPT` | pictures | `--model` (default `z-image-turbo`), `--aspect 1:1\|4:3\|3:4\|16:9\|9:16\|3:2\|2:3`, `--count`, `--seed`; FLUX models also `--from picture.png`, `--denoise`, `--size 1920x1088` |
| `say TEXT` | narration | `--voice` (default `af_heart`), `--speed`, `--file cues.txt` |
| `clone TEXT --ref clip.wav` | speech in a sampled voice | `--model chatterbox-turbo\|chatterbox`, `--takes`, `--file cues.txt` |
| `music TAGS` | music | `--seconds`, `--bpm`, `--loop`, `--lyrics`, `--takes`, `--file cues.txt` |
| `write DRAFT` | a better prompt, printed on stdout | `--for MODEL`, `--image picture.png` |

All generating commands take `--out FOLDER` (copy results there; with a cue
sheet, files are named by cue id) and `--json`. `irisecho COMMAND --help` lists
everything.

Cue sheets are text files, one job per line, `#` for comments:

- `say` and `clone`: `id|text`
- `music`: `id|seconds|bpm|tags` (bpm may be empty)

Only clone a voice the person has the right to use. Cloned speech carries an
inaudible watermark.

## Everything else: the local API

Edits, upscaling, video, sound effects and library housekeeping have no command
of their own yet. Reach them through the running app's API. `irisecho api`
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
| `GET /api/system` | GPU, what is loaded, queue length |

Job `params` by kind of model:

| Kind | Models | Params |
|---|---|---|
| image | `z-image-turbo`, `flux-schnell`, `qwen-image-fast`, `qwen-image`, `flux-dev`, `flux-krea` | `prompt`, `aspect`, `seed` |
| edit | `qwen-edit-fast`, `qwen-edit`, `flux-kontext` | `prompt`, `image1` (and `image2`, `image3` for the Qwen models), `seed` |
| upscale | `seedvr2` | `image1`, `scale` (2, 3 or 4), `retain` (true keeps the original size) |
| video | `wan22-t2v`, `wan22-i2v` | `prompt`, `aspect`, `seconds` (2 to 5), `size` (`standard` or `large`), `smooth`, and for `wan22-i2v` a `start` and/or `end` frame |
| voice | `kokoro` | `text`, `voice`, `speed`, `clean` |
| clone | `chatterbox-turbo`, `chatterbox` | `text`, `ref`, `consent` (must be `true`), `takes`, `seed`; `exaggeration` and `cfg` on `chatterbox` |
| music | `ace-step` | `prompt` (style tags), `duration`, `bpm`, `lyrics`, `takes`, `loop`, `thinking`, `seed` |
| sfx | `sa3-sfx` | `prompt`, `duration` (0.5 to 30), `takes`, `trim`, `seed` |

`image1`, `image2`, `image3`, `start`, `end` and `ref` are upload ids. An agent
on this computer can skip the upload call: copy the file into the `uploads`
folder inside the data folder under a new name of lowercase hex digits plus its
extension (`3fa2b1c4.png`) and pass that name.

The FLUX image models (`flux-schnell`, `flux-dev`, `flux-krea`) also take an
optional `image1` to redraw, with `denoise` (0.05 to 1, default 0.2) for how
much changes, and `width` and `height` (multiples of 16, at most 1920 x 1088 in
area; without them the picture keeps its own aspect at about one megapixel).
The tested use is `flux-krea` redrawing a whole picture larger at 0.2, with the
prompt that made it: people and composition stay, skin and hair gain detail.
Props can change, so check them; a crop of a face redrawn this way does not
keep the person.

To call the API without `irisecho api`: read `port` and `token` from
`server.json` in the data folder, send the cookie `irisecho_session=<token>`
to `http://127.0.0.1:<port>`, and add the header `X-IrisEcho: 1` to anything
that is not a GET. The token changes every time the app starts. It opens this
computer's IrisEcho to whoever holds it, so keep it out of logs, prompts and
commits.

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
