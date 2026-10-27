# Frontend redesign (2026-10-27)

Jason's review: the back end works, but the site is "ugly and nearly unusable". This record is
the design review and the decisions behind the refactor. It changes presentation only. Every
honesty rule stays: provenance, source times, coverage, gaps, embargo and degraded states.

## Review of the 10-27 site

Desktop (1440 px) and phone (390 px) screenshots of every public page.

| Problem | Where | Effect |
|---|---|---|
| No visual identity | All pages | The site looks unrelated to vallery.net: grey field background, underlined text nav, a plain "Weather" word in place of the V mark, no eyebrows, no type scale. |
| Equal weight for everything | Today | 15 identical metric cards. The temperature looks the same as lightning distance. Phone height is 8,621 px. |
| Repeated metadata | Today, events, day | Every card repeats "Observed 10/27/2026, 10:21:55 GMT-06:00 · Measured · expected interval 60 s". Raw codes show through ("Partial · low_coverage", "no_observations"). |
| Machine-facing controls | Video, history, all unit forms | "Source start (UTC ISO)", "Seek to source time (UTC ISO-8601)" and an "Apply units" submit button on every page. Download links give byte counts. |
| Long single columns | Day, history | The day page is three tall columns (10,568 px on a phone). History always renders its 288-row table (14,314 px on desktop, 28,549 px on a phone). |
| Navigation dead ends | Video, day, archive | There is no camera or segment switcher on a recording. The archive calendar gives no hint which days have recordings, and its 100-card catalog repeats the calendar. |
| Jargon in the lede | Day, video, events | "Solar-cycle recording", "±150 s interval", "a video link is navigation, not a claim…" lead the page instead of sitting with the detail. |

## Principles

1. **One look with vallery.net.** Use its tokens as they are: IBM Plex Sans (headings 620,
   -0.02em) and Plex Mono uppercase eyebrows. Colours are ink, muted, line and field, forest,
   leaf and mint, with an amber focus ring. The header is sticky and white with the V wordmark.
   Cards have a 1px line border and an xs shadow, and the dark ink band is used for the hero.
2. **Lead with the answer.** Each page opens with the one thing a visitor came for (the
   temperature now, the recording, the day's three cameras). Detail follows, in disclosure.
3. **State once, not per item.** The time zone, units, cadence and provenance are stated once
   per panel. An item mentions them only when it differs (stale, partial, derived, AI).
4. **Humanize codes.** Every reason code maps to words. An unknown code still shows, but
   readably, with underscores turned into spaces.
5. **Navigation follows the data.** A recording links to its sibling cameras, the other
   segment, the previous and next day, and its day. A day links to its neighbours. The archive
   calendar shows what exists.
6. **Dense, not cramped.** Use compact rows for events, stills and tables, and chips for
   filters. Keep 44 px targets, and axe WCAG 2.2 AA must keep passing at 320, 768 and 1440 px.

## Information architecture

- Primary nav: **Now** · **Timelapses** · **History** · **Almanac** · **Events** · **Search**.
  About moves to the footer with the methods links. The units toggle (°F / °C) sits in the
  header. It is a pair of links that keep the current URL, so it works without JavaScript and
  needs no "Apply" button.
- **Now** (`/`):
  - An ink hero with the temperature, the freshness ("Updated 12 s ago") and a stat strip
    (humidity, wind and direction, gust, pressure, rain, UV, solar).
  - "All readings" is a disclosure table holding every metric's time, cadence and quality.
  - Three camera cards: the delayed preview, then the latest daytime and overnight posters with
    keograms.
  - Compact recent events.
- **Timelapses** (`/archive/`): a month calendar whose cells carry one dot per camera and
  segment, coloured by state. Camera and segment chips filter it. A compact list of the month's
  recordings follows.
- **Day** (`/day/D/`):
  - A date header with previous and next day.
  - A camera × segment grid of poster, state, keogram and a short AI description.
  - Hourly stills as a compact per-camera strip, then the day's events.
- **Recording** (`/timelapses/…`): a theatre layout.
  - The player, then the keogram scrubber, then the cursor readout.
  - A sidebar with the switchers (camera, segment, day), state and coverage, the description,
    and a "Files" disclosure.
  - The linked station plots and the events follow.
- **History**:
  - Range presets (24 h, 3 d, 7 d, 31 d) and metric chips.
  - A summary row (low, mean and high), then the plot.
  - The data table and the custom UTC range sit behind disclosures.
- **Events**, **Stills**, **Almanac**, **Search**, **About**: the same components in compact
  form. Their behaviour does not change.

## Not changed

Routes and URLs, API reads, the SSR admission budget, CSP (no inline styles), JS-off flows,
linked-playback timing, the embed card and the private editor's behaviour.

## Follow-ups

- The catalog exposes only the 1920 px poster, so the calendar shows state dots, not
  thumbnails. Exposing the existing `poster_480` role in `public_api` would allow thumbnails
  there.
