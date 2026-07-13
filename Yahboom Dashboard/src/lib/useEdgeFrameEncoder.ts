// Edge Only frame forwarder.
//
// In edge_aware mode the browser samples the live WebRTC <video>, encodes a JPEG,
// and POSTs it to the backend (POST /api/vit/edge/encode). All MobileCLIP encoding
// and image-to-image matching happens on the backend; the browser just forwards
// frames. The stop path polls /api/vit/status (see useEdgeAwareStopLabelEstop).
//
// Cache Aware mode does nothing here — the Pi provides embeddings over MQTT.

import { useEffect, useRef } from 'react';
import { getActiveVideoElement } from './clientVit/videoFrameRegistry';

const DEFAULT_EDGE_FPS = 5;
const STATUS_POLL_MS = 700;
const JPEG_QUALITY = 0.7;
// Downscale before upload; the backend resizes to the model input anyway.
const MAX_EDGE_DIM = 384;

type DetectionMode = 'edge_aware' | 'cache_aware_offloading';

function videoHasFrame(v: HTMLVideoElement | null): v is HTMLVideoElement {
  return Boolean(v && v.readyState >= 2 && v.videoWidth > 0 && v.videoHeight > 0);
}

/** Samples WebRTC frames and forwards them to the backend encoder in Edge Only mode. */
export function useEdgeFrameEncoder() {
  const modeRef = useRef<DetectionMode>('edge_aware');
  const inFlightRef = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Track detection mode from backend status.
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/vit/status', { cache: 'no-store' });
        if (!res.ok || !alive) return;
        const data = (await res.json()) as { detection_mode?: DetectionMode };
        if (data.detection_mode) modeRef.current = data.detection_mode;
      } catch { /* backend unreachable */ }
    };
    poll();
    const id = setInterval(poll, STATUS_POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, []);

  // Frame sampling loop.
  useEffect(() => {
    let alive = true;
    const fps = Number(import.meta.env.VITE_CLIENT_EDGE_FPS ?? DEFAULT_EDGE_FPS) || DEFAULT_EDGE_FPS;
    const intervalMs = Math.max(100, Math.round(1000 / fps));

    const tick = async () => {
      if (!alive || inFlightRef.current) return;
      if (modeRef.current !== 'edge_aware') return;
      const video = getActiveVideoElement();
      if (!videoHasFrame(video)) return;

      const blob = await captureJpeg(video, canvasRef);
      if (!blob || !alive) return;

      inFlightRef.current = true;
      try {
        const form = new FormData();
        form.append('frame', blob, 'frame.jpg');
        await fetch('/api/vit/edge/encode', { method: 'POST', body: form });
      } catch {
        /* backend unreachable — next tick retries */
      } finally {
        inFlightRef.current = false;
      }
    };

    const id = setInterval(tick, intervalMs);
    return () => { alive = false; clearInterval(id); };
  }, []);
}

async function captureJpeg(
  video: HTMLVideoElement,
  canvasRef: React.MutableRefObject<HTMLCanvasElement | null>,
): Promise<Blob | null> {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  const scale = Math.min(1, MAX_EDGE_DIM / Math.max(vw, vh));
  const w = Math.round(vw * scale);
  const h = Math.round(vh * scale);

  let canvas = canvasRef.current;
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvasRef.current = canvas;
  }
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(video, 0, 0, w, h);

  return new Promise((resolve) => {
    canvas!.toBlob((b) => resolve(b), 'image/jpeg', JPEG_QUALITY);
  });
}
