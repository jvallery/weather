// SPDX-License-Identifier: MIT
import { useRef, useState } from "react";
import type { Video } from "../lib/contracts";

export function VideoPlayer({ video }: { video: Video }) {
  const player = useRef<HTMLVideoElement>(null);
  const resume = useRef<{ time: number; playing: boolean } | null>(null);
  const [quality, setQuality] = useState("auto");
  const [resumeMessage, setResumeMessage] = useState("");
  if (video.state !== "published") return <p>No playable media · {video.reason || video.state}. Coverage and metadata remain available.</p>;
  const role = quality === "auto" ? "web" : quality;
  const rendition = video.renditions.find((item) => item.role === role);
  const native = video.renditions.find((item) => item.role === "native");
  if (!rendition || !native || !video.poster || !video.native_download_url) return <p className="fault">Media metadata is unavailable.</p>;
  return <section aria-labelledby="playback-title"><h2 id="playback-title">Recording</h2>
    <video ref={player} controls playsInline preload="metadata" poster={video.poster.asset.url} src={rendition.asset.url}
      onPlay={(event) => { document.querySelectorAll("video").forEach((other) => { if (other !== event.currentTarget) other.pause(); }); }}
      onLoadedMetadata={() => {
        const element = player.current;
        if (element && resume.current) {
          element.currentTime = Math.min(resume.current.time, Math.max(0, element.duration - 0.001));
          if (resume.current.playing) void element.play().catch(() => setResumeMessage("Press Play to resume at the same point."));
          resume.current = null;
        }
      }}>
      Your browser cannot play this recording. Use the native download below.
    </video>
    <div className="filters"><label htmlFor="quality">Playback quality<select id="quality" value={quality} onChange={(event) => {
      const element = player.current;
      resume.current = { time: element?.currentTime || 0, playing: !!element && !element.paused };
      setQuality(event.target.value);
    }}><option value="auto">Auto (Web)</option><option value="web">Web</option><option value="mobile">Mobile</option><option value="native">Native</option></select></label></div>
    {resumeMessage && <p>{resumeMessage}</p>}
    <p>{rendition.asset.width} × {rendition.asset.height} pixels · {rendition.codec} · {(rendition.fps.numerator / rendition.fps.denominator).toFixed(0)} fps</p>
    <div className="player-links"><a className="button-link" href={video.native_download_url} download>Download native · {native.asset.bytes.toLocaleString("en-US")} bytes · {native.asset.width} × {native.asset.height}</a><a className="button-link" href={`/api/v1/timelapses/${video.video_id}`}>Metadata JSON / sidecar</a></div>
  </section>;
}
