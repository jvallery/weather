// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import type { Video } from "../lib/contracts";

/** Player seconds for a UTC source instant through the recording's source-time map, or null. */
export function seekSeconds(video: Video, instant: string): number | null {
  const at = Date.parse(instant);
  if (!Number.isFinite(at) || at < Date.parse(video.source_interval.start) || at >= Date.parse(video.source_interval.end)) return null;
  for (const entry of video.time_map) {
    const start = Date.parse(entry.source_interval.start), end = Date.parse(entry.source_interval.end);
    if (at < start || at >= end || entry.frame_end <= entry.frame_start) continue;
    const frame = entry.frame_start + Math.floor((at - start) * (entry.frame_end - entry.frame_start) / (end - start));
    return frame * video.fps.denominator / video.fps.numerator;
  }
  return null;
}

export function VideoPlayer({ video, seekTo = null }: { video: Video; seekTo?: number | null }) {
  const player = useRef<HTMLVideoElement>(null);
  const resume = useRef<{ time: number; playing: boolean } | null>(null);
  // A bookmark (?t=) seeks once metadata is known; a later bookmark seeks the loaded player.
  const pendingSeek = useRef<number | null>(seekTo);
  useEffect(() => {
    if (seekTo === null) return;
    const element = player.current;
    if (element && element.readyState >= 1) { element.currentTime = Math.min(seekTo, Math.max(0, element.duration - 0.001)); pendingSeek.current = null; }
    else pendingSeek.current = seekTo;
  }, [seekTo]);
  const [quality, setQuality] = useState("auto");
  const [autoRole, setAutoRole] = useState("web");
  const previousAutoRole = useRef("web");
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(max-width: 767px)");
    const changed = () => {
      const element = player.current;
      const nextRole = media.matches ? "mobile" : "web";
      if (quality === "auto" && nextRole !== previousAutoRole.current && element && element.readyState >= 1) resume.current = { time: element.currentTime, playing: !element.paused };
      previousAutoRole.current = nextRole;
      setAutoRole(nextRole);
    };
    changed(); media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, [quality]);
  const [resumeMessage, setResumeMessage] = useState("");
  if (video.state !== "published" && video.state !== "partial") return <p>No playable media · {video.reason || video.state}. Coverage and metadata remain available.</p>;
  const partial = video.state === "partial";
  const role = quality === "auto" ? autoRole : quality;
  const rendition = video.renditions.find((item) => item.role === role);
  const native = video.renditions.find((item) => item.role === "native");
  if (!rendition || !native || !video.poster || !video.native_download_url) return <p className="fault">Media metadata is unavailable.</p>;
  return <section aria-labelledby="playback-title"><h2 id="playback-title">Recording</h2>
    <p id="playback-note">Silent, accelerated weather recording. Source dates and description follow.</p>
    {partial && <p id="coverage-label" className="notice">Partial coverage · {(video.coverage.fraction * 100).toFixed(1)}% of the source interval was captured; this recording has gaps.</p>}
    <video ref={player} aria-describedby={partial ? "playback-note coverage-label" : "playback-note"} controls muted playsInline preload="metadata" poster={video.poster.asset.url} src={rendition.asset.url}
      onPlay={(event) => { document.querySelectorAll("video").forEach((other) => { if (other !== event.currentTarget) other.pause(); }); }}
      onLoadedMetadata={() => {
        const element = player.current;
        if (element && pendingSeek.current !== null && !resume.current) {
          element.currentTime = Math.min(pendingSeek.current, Math.max(0, element.duration - 0.001));
          pendingSeek.current = null;
        }
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
      // Prefer the current loaded media, retaining a pending seek only while a
      // previous rendition switch is still loading its metadata.
      if (element && element.readyState >= 1) resume.current = { time: element.currentTime, playing: !element.paused };
      else resume.current ||= { time: element?.currentTime || 0, playing: !!element && !element.paused };
      setQuality(event.target.value);
    }}><option value="auto">Auto ({autoRole === "mobile" ? "Mobile" : "Web"})</option><option value="web">Web</option><option value="mobile">Mobile</option><option value="native">Native</option></select></label></div>
    {resumeMessage && <p>{resumeMessage}</p>}
    <p>{rendition.asset.width} × {rendition.asset.height} pixels · {rendition.codec} · {(rendition.fps.numerator / rendition.fps.denominator).toFixed(0)} fps</p>
    <div className="player-links"><a className="button-link" href={video.native_download_url} download>Download native · {native.asset.bytes.toLocaleString("en-US")} bytes · {native.asset.width} × {native.asset.height}</a><a className="button-link" href={`/api/v1/timelapses/${video.video_id}`}>Metadata JSON / sidecar</a></div>
  </section>;
}
