// SPDX-License-Identifier: MIT
export const meta = () => [{ title: "About these observations · Weather" }];
export default function About() {
  return <article className="prose"><span className="eyebrow">About · methods and limits</span><h1>About these observations</h1>
    <h2>Local measurements and three cameras</h2><p>A local Tempest station records weather measurements. Three cameras record the east sky, west sky and front yard. This archive begins with received observations; it does not import a longer climate history.</p>
    <h2>Measurements and interpretation</h2><p>Measured values come from recorded instruments. Derived values are calculations from those observations. AI descriptions are labeled interpretations of recorded evidence, not measurements. Human-edited text carries its own provenance label. Missing measurements are never displayed as zero.</p>
    <h2>Solar cycles and calendar days</h2><p>A solar cycle starts at civil dawn and ends at the next civil dawn. Civil twilight uses the Sun 6° below the horizon. Daytime is dawn to dusk. Overnight is dusk to the following dawn and is labeled with both dates. Calendar-day weather history is separate from a solar cycle. Times show America/Denver and an explicit UTC offset, including the repeated hour when daylight saving time ends.</p>
    <h2>Processed, accelerated imagery</h2><p>Timelapses are processed and accelerated. Observed exposures are accumulated from real captured frames; a processed frame is not a new instantaneous measurement. Each camera cycle receives exactly 7,200 output frames, split between Daytime and Overnight. Coverage and gaps remain visible.</p>
    <h2>Freshness and service availability</h2><p>Current conditions refresh about every 15 seconds while the page is visible. Source age always counts from the original observation, including during an API outage. A refreshed page cannot make an old observation fresh. Capture, processing, publication and AI analysis have separate availability states.</p>
    <h2>Privacy and delayed previews</h2><p>Public previews are delayed at least 10 minutes. Original camera images, private notes and private operational details are not public. Expired, withdrawn or unavailable products retain honest labels rather than substitute imagery.</p>
    <h2>Limits</h2><p>This is an observational archive. It provides no forecast, safety alerts, person identification or complete historical climate record. It does not publish the station's exact location or address. Use appropriate official sources for safety decisions.</p>
  </article>;
}
