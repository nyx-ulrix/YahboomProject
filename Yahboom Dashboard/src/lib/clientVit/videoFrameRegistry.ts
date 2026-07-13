// Minimal registry so edge inference can reach the live WebRTC <video> element.
//
// VideoFeedCore registers its <video> ref here on mount; the client detection
// loop reads the most-recently registered element to sample frames. Kept out of
// zustand to avoid re-renders on every frame grab.

let activeVideo: HTMLVideoElement | null = null;

export function registerVideoElement(el: HTMLVideoElement | null): void {
  activeVideo = el;
}

export function unregisterVideoElement(el: HTMLVideoElement | null): void {
  if (activeVideo === el) activeVideo = null;
}

export function getActiveVideoElement(): HTMLVideoElement | null {
  return activeVideo;
}
