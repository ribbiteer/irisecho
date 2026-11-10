// SPDX-License-Identifier: AGPL-3.0-or-later
// The studios down the left edge. Pictures are violet, sound is teal.

import type { Kind } from "./types";

export interface Studio {
  kind: Kind;
  label: string;
  hint: string;
  sound: boolean;
}

export const STUDIOS: Studio[] = [
  { kind: "image", label: "Image", hint: "Pictures from a description", sound: false },
  { kind: "edit", label: "Edit", hint: "Change a picture with words", sound: false },
  { kind: "video", label: "Video", hint: "Short clips from text or a frame", sound: false },
  { kind: "upscale", label: "Upscale", hint: "Sharpen and enlarge", sound: false },
  { kind: "model3d", label: "3D", hint: "Objects to view, print and export", sound: false },
  { kind: "voice", label: "Voice", hint: "Narration in preset voices", sound: true },
  { kind: "clone", label: "Clone", hint: "A voice from a short sample", sound: true },
  { kind: "music", label: "Music", hint: "Beds, jingles and stingers", sound: true },
  { kind: "sfx", label: "Sound", hint: "One-shot sound effects", sound: true },
];
