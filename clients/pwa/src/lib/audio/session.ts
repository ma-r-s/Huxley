/**
 * iOS audio-session category (Safari 16.4+ `navigator.audioSession`).
 *
 * "playback" routes output through the media volume instead of the much
 * quieter ringer bus, but WebKit refuses microphone capture under it:
 * `getUserMedia` rejects with InvalidStateError "AudioSession category is
 * not compatible with audio capture." So the category follows the mic:
 * "play-and-record" while the mic is open, "playback" otherwise.
 * Elsewhere `navigator.audioSession` is undefined and this is a no-op.
 */

export type AudioSessionType = "playback" | "play-and-record";

export function setAudioSessionType(type: AudioSessionType): void {
  try {
    const session = (
      navigator as Navigator & { audioSession?: { type: string } }
    ).audioSession;
    if (session && session.type !== type) session.type = type;
  } catch {
    /* unsupported — default routing */
  }
}
