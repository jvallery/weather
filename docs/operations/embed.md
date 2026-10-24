# Current-weather embed

Weather #45 owns the child at `https://weather.vallery.net/embed/weather/`;
VerdifyConsultancy/wordpress#242 owns the parent CSP `frame-src` and the homepage
block. The combined live check on vallery.net is #36.

The SSR card (`web/app/routes/embed.weather.tsx`) reads `/api/v1/weather/current`
and the metric registry through the generated types and shows the API's
`temperature`, `wind_avg`, `wind_gust`, `wind_direction` and `rain_today`, each
with its own `observed_at` and age. It uses Today's parser, US display recipe
(`?units=metric` switches), source-age logic and request cache. Missing metrics stay
unavailable; expiry is re-evaluated against the clock; a failed request never
changes observation times. A missing conversion recipe shows labelled source units.

Polling uses the shared `useCurrent` hook with the card's options: one refresh on
visible resume, 10% bounded jitter, one in-flight request and a 120-second backoff
ceiling. Hidden documents cancel and pause requests. Without JavaScript the card is
the SSR snapshot with its generation time and says it does not auto-update. The card
has no chart, player, editor, third-party resource, cookie or camera access.

## Framing

`web/security-headers.mjs` sets the headers before any handler runs, so success,
404, 500 and API-down responses get the same policy:

- `/embed/weather/` and paths below it: CSP `frame-ancestors https://vallery.net`
  and no `X-Frame-Options`. Route errors render the unavailable card.
- Every other path: `frame-ancestors 'none'` and `X-Frame-Options: DENY`.

The policy is one HTTP header, never a meta tag; the edge (Traefik, Cloudflare) adds
no CSP or `X-Frame-Options`. CORS is unchanged. `https://www.vallery.net` returns a
308 to the apex, so it is not an allowed ancestor; add it only if the parent is
ever served from www. Never add wildcards, localhost or the WordPress admin origin.

## Parent contract

The parent iframe URL is fixed as above, with title `Current weather in Longmont`
and `loading="lazy"`. It is 24rem high when the container is at least 48rem wide,
and 36rem below that width. Content may scroll inside the frame; it is never
clipped. The card's region and its Open Weather link are keyboard reachable; the
link targets the top-level `https://weather.vallery.net/`. No postMessage sizing or
parent polling is used. The parent also provides its own outside link.

## Checks

Unit tests cover the header policy per path, card/Today parity, embed polling and
the error card. The browser suite checks the 200/404/500/API-down headers and
bodies, the denied routes, a cookie-free same-origin load, the no-JS snapshot and
one Chromium axe pass at 320 px. Live readback after promotion:

```sh
curl -sI https://weather.vallery.net/embed/weather/ | grep -iE '^(content-security-policy|x-frame-options|set-cookie)'
curl -sI https://weather.vallery.net/ | grep -iE '^(content-security-policy|x-frame-options)'
curl -sI https://www.vallery.net/ | grep -iE '^(HTTP|location)'
```

The embed must show exactly one `frame-ancestors https://vallery.net` and no
`X-Frame-Options`; `/` must show `frame-ancestors 'none'` and `X-Frame-Options: DENY`.
