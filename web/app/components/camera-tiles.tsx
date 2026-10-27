// SPDX-License-Identifier: MIT
import { Link } from "./unit-link";
import { CAMERA_LABELS, dateShift, dayTitle } from "../lib/format";
import type { Camera } from "../lib/contracts";
import { playable, type Timelapse } from "../lib/weather-api";
import { Status, videoPath } from "./common";
import { PreviewTile } from "./camera-previews";

const SEGMENTS = [["daytime", "Daytime"], ["overnight", "Overnight"]] as const;
/** The newest catalog entry per camera and segment (the API lists newest first). */
export function latestMedia(items: readonly Timelapse[]): Map<string, Timelapse> {
  const latest = new Map<string, Timelapse>();
  for (const item of items) if (!latest.has(`${item.camera}/${item.segment}`)) latest.set(`${item.camera}/${item.segment}`, item);
  return latest;
}
/** The camera's newest playable recording (the catalog lists newest first), for the preview's link. */
function newest(items: readonly Timelapse[], camera: string): string | null {
  const video = items.find((item) => item.camera === camera && playable(item));
  return video ? videoPath(video) : null;
}
function Tile({ camera, segment, label, video }: { camera: Camera; segment: string; label: string; video: Timelapse | undefined }) {
  if (!video) return <li><h4>{label}</h4><p className="media-empty">No recording published yet</p></li>;
  const name = `${CAMERA_LABELS[camera]} · ${label.toLowerCase()} · ${video.cycle_date}${segment === "overnight" ? ` → ${dateShift(video.cycle_date, 1)}` : ""}`;
  const keogram = (video.keogram_state === "published" || video.keogram_state === "partial") && video.keogram;
  return <li><h4><span>{label} · {dayTitle(video.cycle_date, "short")}</span>{video.state !== "published" && <Status state={video.state} />}</h4>
    {playable(video) ? <Link className="media-tile" to={videoPath(video)}><img src={(video.poster_small ?? video.poster)!.url} alt={`Poster frame, ${name}`} width={(video.poster_small ?? video.poster)!.width ?? undefined} height={(video.poster_small ?? video.poster)!.height ?? undefined} loading="lazy" decoding="async" /><span className="play" aria-hidden="true" /><span className="screen-reader">Watch {name}</span></Link>
      : <Link className="media-empty" to={videoPath(video)}>No playable media · details</Link>}
    {keogram ? <Link className="keo-thumb" to={videoPath(video)}><img src={(video.keogram_thumbnail ?? video.keogram)!.url} alt={`Keogram, ${name}${video.keogram_state === "partial" ? " (partial coverage)" : ""}`} width={(video.keogram_thumbnail ?? video.keogram)!.width ?? undefined} height={(video.keogram_thumbnail ?? video.keogram)!.height ?? undefined} loading="lazy" decoding="async" /></Link>
      : <p className="small">Keogram {video.keogram_state === "pending" ? "pending" : video.keogram_state === "withdrawn" ? "withdrawn" : "unavailable"}.</p>}
  </li>;
}
/** The three camera cards: delayed preview (#39), then the latest recording, poster and keogram per segment. */
export function CameraTiles({ media }: { media: readonly Timelapse[] | null }) {
  const latest = latestMedia(media || []);
  return <section className="section" aria-labelledby="cameras-title">
    <div className="section-head"><div><span className="eyebrow">Three cameras · delayed at least 10 minutes</span><h2 id="cameras-title">Sky and yard</h2></div><Link to="/archive/">All timelapses →</Link></div>
    {media === null && <p className="fault">Recording API unavailable. Latest videos and keograms cannot be listed; nothing is shown in their place.</p>}
    <div className="grid three">{(Object.keys(CAMERA_LABELS) as Camera[]).map((camera) => <article className="card camera-card" key={camera}><h3>{CAMERA_LABELS[camera]}</h3><PreviewTile camera={camera} href={newest(media || [], camera)} />
      <ul className="media-tiles">{SEGMENTS.map(([segment, label]) => <Tile key={segment} camera={camera} segment={segment} label={label} video={latest.get(`${camera}/${segment}`)} />)}</ul></article>)}</div></section>;
}
