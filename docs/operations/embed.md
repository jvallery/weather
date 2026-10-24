# Current-weather embed

Weather #45 owns the child at `https://weather.vallery.net/embed/weather/`;
WordPress #242 owns its parent policy and homepage content. This source prepares
an unregistered component; no embed route or framing exception is activated.
Weather #14 and #21 remain the native dependencies for integration and delivery.

The card selects the API's canonical `temperature`, `wind_avg`, `wind_gust`,
`wind_direction` and `rain_today` measurements. It uses Today's current parser,
US display recipe, source-age formatting and shared request cache. Missing
metrics stay unavailable. Expiry is re-evaluated against the clock; a failed
request never changes observation times. A missing display-conversion recipe
falls back to labelled source units rather than inventing a conversion.

Polling uses the shared hook's card options: refresh once on visible resume,
10% bounded jitter, one in-flight request and a maximum 120-second backoff.
Hidden documents cancel and pause requests. SSR displays the supplied snapshot;
the no-script text says explicitly that it does not auto-update. The component
contains no chart, player, editor, third-party resource or camera access.

## Parent contract

The parent iframe URL is fixed as above, with title `Current weather in Longmont`
and `loading="lazy"`. It is 24rem high when the container is at least 48rem wide,
and 36rem below that width. Content may scroll inside the frame; it is never
clipped. The card's region and ordinary Open Weather link are keyboard reachable;
the link targets the top-level `https://weather.vallery.net/`. No postMessage
sizing or parent polling is used. The parent also provides its own outside link.

## Remaining commissioning

Wire an SSR route after the shared web/API dependencies land. Use bounded public
current and metric-registry reads with an explicit API-unavailable fallback.
Keep source times in loader data; do not create substitute measurements.

The route and every error response need exactly `frame-ancestors
https://vallery.net`, no X-Frame-Options and existing HSTS. Other routes retain
framing denied. The namespace route/middleware exception must be limited to
`/embed/weather/`; CORS is unchanged. No such route/header change is part of this
source preparation.

Use the owner fleet browser runner to verify the permitted parent, an unrelated
parent and a forbidden nested ancestor, plus route 404/500/API failures. Check
Chromium, WebKit and Firefox at 320px, keyboard access, reduced motion, 200% zoom,
no-script and third-party requests blocked. Confirm no cookies/private source
fields, and inspect the build manifest for chart/player/editor isolation.
Focused SSR component tests cover unit/source-time parity, expiry without a new
response, missing metrics and explicit transport failure. They do not establish
browser, header, accessibility or live framing acceptance.
