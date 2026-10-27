// SPDX-License-Identifier: MIT
import { CAMERA_LABELS } from "../lib/format";
import { captureLabel, usePreview } from "../lib/preview";
import type { Camera } from "../lib/contracts";

/** One camera's delayed preview (#39): the image with its true capture age, stale, or unavailable. */
export function PreviewTile({ camera }: { camera: Camera }) {
  const label = CAMERA_LABELS[camera];
  const tile = usePreview(camera);
  let body;
  if (tile.kind === "current") {
    const caption = captureLabel(tile.preview.source_interval!.start, tile.age);
    const large = tile.preview.images.find((image) => image.variant === "large") || tile.preview.images[0];
    const small = tile.preview.images.find((image) => image.variant === "small") || large;
    body = <figure className="preview">
      <img src={small.url} srcSet={`${small.url} ${small.width}w, ${large.url} ${large.width}w`} sizes="(max-width: 550px) 100vw, 33vw" width={large.width} height={large.height} alt={`${label} camera, ${caption}`} decoding="async" />
      <figcaption>{tile.state === "stale" ? <span className="status status-stale">Stale</span> : <span className="dot" aria-hidden="true" />}<span>Delayed preview, {caption}</span></figcaption>
    </figure>;
  } else {
    body = <div className="preview-placeholder" role="status"><p>{tile.kind === "loading" ? "Checking for a delayed preview…" : `Preview unavailable. ${tile.message}`}</p></div>;
  }
  return <div className="preview-slot" data-preview-state={tile.kind === "current" ? tile.state : tile.kind}>
    {body}<noscript><p className="small">Delayed previews need JavaScript.</p></noscript>
  </div>;
}
