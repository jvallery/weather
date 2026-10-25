// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { CAMERA_LABELS, dateShift } from "../lib/format";
import type { Camera } from "../lib/contracts";
import { playable, type Timelapse } from "../lib/weather-api";
import { Status } from "./common";
import { PreviewTile } from "./camera-previews";

const SEGMENTS = [["daytime", "Daytime"], ["overnight", "Overnight"]] as const;
const path = (video: Timelapse) => `/timelapses/${video.camera}/${video.cycle_date}/${video.segment}/`;
/** The newest catalog entry per camera and segment (the API lists newest first). */
export function latestMedia(items: readonly Timelapse[]): Map<string, Timelapse> {
  const latest = new Map<string, Timelapse>();
  for (const item of items) if (!latest.has(`${item.camera}/${item.segment}`)) latest.set(`${item.camera}/${item.segment}`, item);
  return latest;
}
function Tile({ camera, segment, label, video }: { camera: Camera; segment: string; label: string; video: Timelapse | undefined }) {
  if (!video) return <li><h4>{label}</h4><p><span className="status">Pending</span> · no recording published yet.</p></li>;
  const name = `${CAMERA_LABELS[camera]} · ${label.toLowerCase()} · ${video.cycle_date}${segment === "overnight" ? ` → ${dateShift(video.cycle_date, 1)}` : ""}`;
  const keogram = (video.keogram_state === "published" || video.keogram_state === "partial") && video.keogram;
  return <li><h4>{label} · {video.cycle_date}</h4><Status state={video.state} reason={video.reason} />
    {playable(video) ? <Link className="media-tile" to={path(video)}><img src={video.poster!.url} alt={`Poster frame, ${name}`} width={video.poster!.width ?? undefined} height={video.poster!.height ?? undefined} loading="lazy" /><span>Watch {name}</span></Link>
      : <p><Link to={path(video)}>Recording details · {name}</Link> · no playable media.</p>}
    {keogram ? <Link className="media-tile" to={path(video)}><img src={video.keogram!.url} alt={`Keogram, ${name}${video.keogram_state === "partial" ? " (partial coverage)" : ""}`} width={video.keogram!.width ?? undefined} height={video.keogram!.height ?? undefined} loading="lazy" /></Link>
      : <p className="small">Keogram {video.keogram_state === "pending" ? "pending" : video.keogram_state === "withdrawn" ? "withdrawn" : "unavailable"}.</p>}
  </li>;
}
/** Today's camera cards: delayed preview (#39), then the latest video, poster, keogram and state per segment. */
export function CameraTiles({ media }: { media: readonly Timelapse[] | null }) {
  const latest = latestMedia(media || []);
  return <section aria-labelledby="cameras-title"><h2 id="cameras-title">Three cameras, one day</h2><p>Camera previews are delayed at least 10 minutes and refresh about once a minute. The time shown is when the frame was captured. This is an observational archive, not a forecast.</p>
    {media === null && <p className="fault">Recording API unavailable. Latest videos and keograms cannot be listed; nothing is shown in their place.</p>}
    <div className="grid three">{(Object.keys(CAMERA_LABELS) as Camera[]).map((camera) => <article className="card" key={camera}><h3>{CAMERA_LABELS[camera]}</h3><PreviewTile camera={camera} />
      <ul className="media-tiles">{SEGMENTS.map(([segment, label]) => <Tile key={segment} camera={camera} segment={segment} label={label} video={latest.get(`${camera}/${segment}`)} />)}</ul></article>)}</div></section>;
}
