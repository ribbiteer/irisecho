// SPDX-License-Identifier: AGPL-3.0-or-later
// The studios down the left edge. Pictures are violet, sound is teal.

import type { Kind } from "./types";

export interface Studio {
  kind: Kind;
  label: string;
  verb: string;
  hint: string;
  sound: boolean;
}

export const STUDIOS: Studio[] = [
  { kind: "image", label: "Image", verb: "Create", hint: "Pictures from a description", sound: false },
  { kind: "edit", label: "Edit", verb: "Edit", hint: "Change a picture with words", sound: false },
  { kind: "video", label: "Video", verb: "Animate", hint: "Short clips from text or a frame", sound: false },
  { kind: "upscale", label: "Upscale", verb: "Upscale", hint: "Sharpen and enlarge", sound: false },
  { kind: "model3d", label: "3D", verb: "Build", hint: "Objects to view, print and export", sound: false },
  { kind: "voice", label: "Voice", verb: "Speak", hint: "Narration in preset voices", sound: true },
  { kind: "clone", label: "Clone", verb: "Speak", hint: "A voice from a short sample", sound: true },
  { kind: "music", label: "Music", verb: "Compose", hint: "Beds, jingles and stingers", sound: true },
  { kind: "sfx", label: "Sound", verb: "Generate", hint: "One-shot sound effects", sound: true },
];

export const studio = (kind: Kind) => STUDIOS.find((s) => s.kind === kind)!;
