// SPDX-License-Identifier: MIT
import { CAMERA_LABELS } from "../lib/format";
import type { Camera } from "../lib/contracts";
export function CameraPlaceholders() {
  return <section aria-labelledby="cameras-title"><h2 id="cameras-title">Three cameras, one day</h2><p>Camera previews are delayed at least 10 minutes. This is an observational archive, not a forecast.</p><div className="grid three">{(Object.keys(CAMERA_LABELS) as Camera[]).map((camera) => <article className="card" key={camera}><h3>{CAMERA_LABELS[camera]}</h3><div className="preview-placeholder"><p>Preview coming soon</p></div><p>Delayed preview · coming soon in the timelapse slice.</p><ul className="segment-list"><li>Daytime latest video · coming soon</li><li>Overnight latest video · coming soon</li></ul><p className="small">No camera image or completed recording is implied by this placeholder.</p></article>)}</div></section>;
}
