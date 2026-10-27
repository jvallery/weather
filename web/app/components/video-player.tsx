// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import { playable, type Timelapse } from "../lib/weather-api";

export type SeekRequest = { seconds: number; serial: number };
type FrameCallback = (now: number, metadata: { mediaTime: number }) => void;
type FrameVideo = HTMLVideoElement & { requestVideoFrameCallback?: (callback: FrameCallback) => number; cancelVideoFrameCallback?: (handle: number) => void };
const MOBILE = "(max-width: 767px)";

export type Quality = "auto" | "web" | "mobile" | "native";
/** The rendition the browser's one-time <source> selection picks for "auto". */
function useAutoRole(): "web" | "mobile" {
  const [role, setRole] = useState<"web" | "mobile">("web");
  useEffect(() => { if (typeof window.matchMedia === "function" && window.matchMedia(MOBILE).matches) setRole("mobile"); }, []);
  return role;
}
const mib = (bytes: number) => `${(bytes / 1_048_576).toFixed(bytes < 10_485_760 ? 1 : 0)} MB`;

/** The recording's controls and files: coverage, quality, size, downloads and the moment link. */
export function PlayerPanel({ video, quality, onQuality, coverageLabel = null, moment = null }: { video: Timelapse; quality: Quality; onQuality: (quality: Quality) => void; coverageLabel?: string | null; moment?: string | null }) {
  const autoRole = useAutoRole();
  if (!playable(video)) return null;
  const rendition = video.renditions.find((item) => item.role === (quality === "auto" ? autoRole : quality))!;
  const native = video.renditions.find((item) => item.role === "native")!;
  return <div className="card player-panel"><div className="card-head"><h2>Playback</h2></div>
    {video.state === "partial" && <p id="coverage-label" className="small">{coverageLabel || "Partial coverage · this recording has gaps."}</p>}
    <div className="filters"><label htmlFor="quality">Playback quality<select id="quality" value={quality} onChange={(event) => onQuality(event.target.value as Quality)}><option value="auto">Auto ({autoRole === "mobile" ? "Mobile" : "Web"})</option><option value="web">Web</option><option value="mobile">Mobile</option><option value="native">Native</option></select></label></div>
    {rendition.asset.width && rendition.asset.height && <p className="small">{rendition.asset.width} × {rendition.asset.height} · H.264 · 60 fps · {(video.allocated_frames / 60).toFixed(0)} s</p>}
    <div className="player-links">
      {moment && <a href={moment}>Link to this moment</a>}
      <a href={video.native_download_url!} download>Download native ({native.asset.width && native.asset.height ? `${native.asset.width} × ${native.asset.height}, ` : ""}{mib(native.asset.bytes)})</a>
      <a href={`/api/v1/timelapses/${video.video_id}`}>Catalog metadata JSON</a>
      {video.sidecar && <a href={video.sidecar.url}>Sidecar JSON · time map and coverage</a>}
    </div>
  </div>;
}

/**
 * Native playback. The route controller owns time: it sends seek requests and receives player
 * time (per presented frame while playing, and on every seek), so this component never maps time.
 * With a `quality` prop the controls live elsewhere (PlayerPanel); without one it renders its own.
 */
export function VideoPlayer({ video, seek = null, onTime, coverageLabel = null, quality: controlled }: { video: Timelapse; seek?: SeekRequest | null; onTime?: (seconds: number) => void; coverageLabel?: string | null; quality?: Quality }) {
  const player = useRef<FrameVideo>(null);
  const resume = useRef<{ time: number; playing: boolean } | null>(null);
  const report = useRef(onTime);
  useEffect(() => { report.current = onTime; }, [onTime]);
  // A seek before metadata is known applies once it is; a later seek moves the loaded player.
  const pendingSeek = useRef<number | null>(null);
  useEffect(() => {
    if (seek === null) return;
    const element = player.current;
    if (element && element.readyState >= 1) { element.currentTime = Math.min(seek.seconds, Math.max(0, element.duration - 0.001)); pendingSeek.current = null; }
    else pendingSeek.current = seek.seconds;
  }, [seek]);
  const [own, setOwn] = useState<Quality>("auto");
  const quality = controlled ?? own;
  // Auto quality is chosen once, natively, by the browser's resource selection over <source> media
  // queries (server and client render the same markup, and it works without JavaScript). A later
  // viewport change never swaps media: a post-hydration swap downloaded both renditions on phones.
  // <source> children are not re-selected on their own, so only a quality change reloads; the
  // playhead (or a still-pending earlier switch's) is kept across it.
  const selected = useRef(quality);
  useEffect(() => {
    if (selected.current === quality) return;
    selected.current = quality;
    const element = player.current;
    if (element && element.readyState >= 1) resume.current = { time: element.currentTime, playing: !element.paused };
    else resume.current ||= { time: element?.currentTime || 0, playing: !!element && !element.paused };
    element?.load();
  }, [quality]);
  // While playing, report each presented frame's media time (timeupdate is only ~4 Hz).
  const frameLoop = useRef<number | null>(null);
  const stopLoop = () => { const element = player.current; if (frameLoop.current !== null && element?.cancelVideoFrameCallback) element.cancelVideoFrameCallback(frameLoop.current); frameLoop.current = null; };
  const startLoop = () => {
    const element = player.current;
    if (!element?.requestVideoFrameCallback) return;
    stopLoop();
    const step: FrameCallback = (_now, metadata) => { if (!element.seeking) report.current?.(metadata.mediaTime); frameLoop.current = element.paused ? null : element.requestVideoFrameCallback!(step); };
    frameLoop.current = element.requestVideoFrameCallback(step);
  };
  useEffect(() => stopLoop, []);
  const [resumeMessage, setResumeMessage] = useState("");
  if (!playable(video)) return <p>No playable media · {video.reason || video.state}. Source times remain available.</p>;
  const source = (item: string) => video.renditions.find((entry) => entry.role === item)!.asset.url;
  const sources = quality === "auto" ? [{ role: "mobile", media: MOBILE }, { role: "web", media: undefined }] : [{ role: quality, media: undefined }];
  // A report taken mid-seek would carry the old position; the seeked event reports the new one.
  const time = () => { const element = player.current; if (element && !element.seeking) report.current?.(element.currentTime); };
  return <section aria-labelledby="playback-title" className="player"><h2 id="playback-title" className="screen-reader">Recording</h2>
    <p id="playback-note" className="screen-reader">Silent, accelerated weather recording at 60 frames per second. Source times follow.</p>
    <video ref={player} aria-describedby={video.state === "partial" ? "playback-note coverage-label" : "playback-note"} controls muted playsInline preload="metadata" poster={video.poster!.url}
      onPlay={(event) => { document.querySelectorAll("video").forEach((other) => { if (other !== event.currentTarget) other.pause(); }); startLoop(); }}
      onPause={() => { stopLoop(); time(); }} onSeeked={time} onTimeUpdate={time} onEnded={time}
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
      {sources.map((item) => <source key={item.role} src={source(item.role)} media={item.media} />)}
      Your browser cannot play this recording. Use the native download.
    </video>
    {resumeMessage && <p>{resumeMessage}</p>}
    {controlled === undefined && <PlayerPanel video={video} quality={own} onQuality={setOwn} coverageLabel={coverageLabel} />}
  </section>;
}
