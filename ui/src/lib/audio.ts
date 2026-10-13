// SPDX-License-Identifier: AGPL-3.0-or-later
// Voice samples the server cannot read as they are (phone voice memos in M4A,
// browser recordings in WebM) are decoded here and sent as WAV instead.

const READABLE = new Set(["audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/flac", "audio/ogg"]);
const AUDIO_EXT = /\.(wav|mp3|flac|ogg|oga|opus|m4a|mp4|aac|caf|webm|weba|aif|aiff)$/i;

/** Enough of a voice sample to clone from; the rest is never used. */
const KEEP_SECONDS = 30;

export function looksLikeAudio(file: File): boolean {
  return file.type.startsWith("audio/") || AUDIO_EXT.test(file.name);
}

/** The file as is when the server reads its format, otherwise a 16-bit mono WAV of it. */
export async function readableAudio(file: File, options: { seconds?: number; stereo?: boolean } = {}): Promise<File> {
  if (READABLE.has(file.type)) return file;
  const Ctx = window.AudioContext ?? (window as any).webkitAudioContext;
  if (!Ctx) throw new Error("This browser cannot read that recording. Use a WAV or MP3 file.");
  const ctx: AudioContext = new Ctx();
  let decoded: AudioBuffer;
  try {
    decoded = await ctx.decodeAudioData(await file.arrayBuffer());
  } catch {
    throw new Error("That recording could not be read. Use a WAV, MP3, FLAC, OGG or M4A file.");
  } finally {
    ctx.close();
  }
  const name = file.name.replace(/\.[^.]+$/, "") + ".wav";
  const wav = encodeWav(decoded, options.seconds ?? KEEP_SECONDS, !!options.stereo);
  return new File([wav], name, { type: "audio/wav" });
}

// Mono and 30 s for a voice sample; music and sounds to start from keep their channels and their length.
function encodeWav(buffer: AudioBuffer, seconds: number, stereo: boolean): Blob {
  const rate = buffer.sampleRate;
  const length = Math.min(buffer.length, Math.floor(rate * seconds));
  const channels = stereo ? Math.min(2, buffer.numberOfChannels) : 1;
  const mixed: Float32Array[] = [];
  for (let k = 0; k < channels; k++) mixed.push(new Float32Array(length));
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    // Without stereo every channel is averaged into the one; with it, channel 0 is left and the rest right.
    const target = mixed[stereo ? Math.min(c, 1) : 0];
    const share = stereo ? 1 : 1 / buffer.numberOfChannels;
    for (let i = 0; i < length; i++) target[i] += data[i] * share;
  }
  const bytes = length * channels * 2;
  const out = new DataView(new ArrayBuffer(44 + bytes));
  const text = (at: number, s: string) => [...s].forEach((ch, i) => out.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  out.setUint32(4, 36 + bytes, true);
  text(8, "WAVE");
  text(12, "fmt ");
  out.setUint32(16, 16, true);
  out.setUint16(20, 1, true); // PCM
  out.setUint16(22, channels, true);
  out.setUint32(24, rate, true);
  out.setUint32(28, rate * channels * 2, true);
  out.setUint16(32, channels * 2, true);
  out.setUint16(34, 16, true);
  text(36, "data");
  out.setUint32(40, bytes, true);
  for (let i = 0; i < length; i++) {
    for (let k = 0; k < channels; k++) {
      const s = Math.max(-1, Math.min(1, mixed[k][i]));
      out.setInt16(44 + (i * channels + k) * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
  }
  return new Blob([out.buffer], { type: "audio/wav" });
}
