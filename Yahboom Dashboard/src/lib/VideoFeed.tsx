// Shared video-feed component used by both the Dashboard widget and the

// Controller page. The stream URL is always derived automatically from

// /api/status — WebRTC via native <video> or MJPEG relay (/api/video_feed).



import { useEffect, useRef, useState } from 'react';

import { VideoOff } from 'lucide-react';

import { useSettingsStore } from '../app/store';

import type { SettingsStore } from '../app/store';

import { registerVideoElement, unregisterVideoElement } from './clientVit/videoFrameRegistry';



interface VideoFeedCoreProps {

  /** Tighten padding / font sizes for the compact controller layout. */

  compact?: boolean;

  /** Extra elements rendered on top of the stream (HUD overlays, crosshairs…). */

  children?: React.ReactNode;

  className?: string;

  style?: React.CSSProperties;

}



/**

 * VideoFeedCore — native WebRTC <video> (video only), or MJPEG via <img>.

 */

export function VideoFeedCore({ compact = false, children, className = '', style }: VideoFeedCoreProps) {

  const videoStreamUrl = useSettingsStore((s: SettingsStore) => s.videoStreamUrl);

  const videoRef = useRef<HTMLVideoElement>(null);



  const [imgError, setImgError] = useState(false);

  const [videoError, setVideoError] = useState(false);



  const streamUrl = videoStreamUrl;

  const isWebRtc = Boolean(streamUrl?.startsWith('http'));



  // Reset error state whenever the URL changes so a reconnect re-attempts the stream.

  useEffect(() => {

    setImgError(false);

    setVideoError(false);

    if (streamUrl) {

      console.info('[VideoFeed] displaying stream:', streamUrl);

    }

  }, [streamUrl]);



  // WebRTC: connect directly to the Pi stream via backend /api/webrtc/offer proxy.

  useEffect(() => {

    if (!isWebRtc || !streamUrl) return;



    let pc: RTCPeerConnection | null = null;

    let cancelled = false;



    (async () => {

      try {

        pc = new RTCPeerConnection();



        pc.ontrack = (event) => {

          const el = videoRef.current;

          if (el && !cancelled) {

            el.srcObject = event.streams[0] ?? null;

          }

        };



        pc.addTransceiver('video', { direction: 'recvonly' });



        const offer = await pc.createOffer();

        await pc.setLocalDescription(offer);



        const res = await fetch('/api/webrtc/offer', {

          method: 'POST',

          headers: { 'Content-Type': 'application/json' },

          body: JSON.stringify({

            sdp: pc.localDescription!.sdp,

            type: pc.localDescription!.type,

          }),

        });



        if (!res.ok) {

          throw new Error(await res.text());

        }



        const answer = await res.json() as RTCSessionDescriptionInit;

        if (cancelled || !pc) return;

        await pc.setRemoteDescription(answer);

      } catch (err) {

        console.error('[VideoFeed] WebRTC failed:', err);

        if (!cancelled) setVideoError(true);

      }

    })();



    return () => {

      cancelled = true;

      pc?.close();

      const el = videoRef.current;

      if (el) el.srcObject = null;

    };

  }, [isWebRtc, streamUrl]);



  // Expose the live WebRTC <video> to the client edge-inference loop.

  useEffect(() => {

    const el = isWebRtc ? videoRef.current : null;

    registerVideoElement(el);

    return () => unregisterVideoElement(el);

  }, [isWebRtc, streamUrl, videoError]);



  const iconSize = compact ? 20 : 28;

  const fontSize = compact ? 10 : 11;



  return (

    <div

      className={`relative w-full h-full overflow-hidden ${className}`}

      style={{ background: '#08060a', ...style }}

    >

      {/* ── Live stream (WebRTC video only, or MJPEG relay) ──────────────── */}

      {streamUrl && isWebRtc && !videoError && (

        <video

          ref={videoRef}

          autoPlay

          playsInline

          muted

          className="absolute inset-0 w-full h-full"

          style={{ objectFit: 'contain', background: '#000' }}

        />

      )}

      {streamUrl && !isWebRtc && !imgError && (

        <img

          key={streamUrl}

          src={streamUrl}

          alt="video stream"

          onError={() => setImgError(true)}

          className="absolute inset-0 w-full h-full"

          style={{ objectFit: 'contain' }}

        />

      )}



      {/* ── Not-connected placeholder ────────────────────────────────────── */}

      {!streamUrl && (

        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4">

          <VideoOff size={iconSize} style={{ color: 'var(--text-muted)' }} />

          <p style={{ fontSize, color: 'var(--text-muted)', textAlign: 'center', margin: 0 }}>

            Video unavailable

          </p>

        </div>

      )}



      {/* ── Error state (connected but stream unreachable) ───────────────── */}

      {streamUrl && (imgError || videoError) && (

        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4">

          <VideoOff size={iconSize} style={{ color: 'var(--state-error)' }} />

          <p style={{ fontSize, color: 'var(--state-error)', margin: 0 }}>Video unavailable</p>

          <p style={{ fontSize: compact ? 8 : 9, color: 'var(--text-muted)', margin: 0, textAlign: 'center' }}>

            Start the video server on the Pi

          </p>

        </div>

      )}



      {/* ── Caller overlays (HUD, crosshair, brackets…) ─────────────────── */}

      {children}

    </div>

  );

}


