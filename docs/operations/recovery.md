# Media recovery

This page covers restoring weather media from its offsite custody copy, and the
recovery times measured when it was done (#77). Recovering the database itself
(the CNPG catalog) is covered in [database.md](database.md) and weather#6.

## What can be restored, and from where

| Data | Production store | Offsite custody | Restore reader |
|---|---|---|---|
| `weather-media` objects (video, poster, keogram, still, sidecar) | Garage bucket `weather-media` | B2 `object-custody-vallery`, prefix `object-custody/weather-media/` | Secret `weather-custody-restore-reader` (storage-infra; readFiles only, no listFiles) |
| `weather-originals` hourly originals | Garage bucket `weather-originals` | B2 `object-custody-vallery`, prefix `object-custody/weather-originals/` | same |
| Catalog rows (`weather.media_asset`) | CNPG `weather-pg` | B2 `s3://weather-db-vallery/cnpg` (barman base + WAL) | Secret `weather-pg-backup-b2-reader` |

The custody reader is used where it already lives, in namespace `weather`. It is
never copied. Because it cannot list, B2 answers a HEAD for a key it does not
hold with 403, not 404. The drill treats a 403 as "absent" only for keys newer
than the newest key custody does hold. Any other 403 fails the drill.

## Drill procedure

`scripts/media_restore_drill.py` runs the whole drill as one disposable Pod
(`deploy/acceptance/restore/media-drill.yaml`, scripts in
`deploy/acceptance/restore/media-drill/`), with a deny-by-default
NetworkPolicy. Egress is DNS plus 443 to non-RFC1918 addresses only. The Pod has
no Service account token, no Service and no route. It writes nothing to
production databases, PVCs or buckets.

1. `catalog-init` restores the newest `weather-pg` B2 base into a pod-local
   Postgres and replays WAL. It then scopes every `media_asset` row outside the
   drill day to `unavailable`.
2. `garage` starts an isolated pod-local Garage, the drill target, with the
   production bucket names and fresh drill-only keys.
3. `driver` copies every catalog object of the day from B2 custody into the drill
   Garage and checks each object's SHA-256 against its catalog row. It withholds
   the smallest still, runs `weather reconcile-objects` for both buckets, and
   requires the set of rows marked `unavailable` to equal the withheld object
   plus any objects missing from custody. It then measures RPO (below).
4. `api` and `web` run at the production digests. `web` uses its in-cluster
   fixture mode (`WEATHER_CI_IN_CLUSTER=1`), which admits Host `localhost` and
   upstream `http://localhost:8000` in place of `weather.vallery.net` and
   `http://api`. Rendering is unchanged.
5. Over `kubectl port-forward`, the orchestrator fetches the day page, the
   restored still (expects 200), the withheld still (expects 404) and an
   original's ID (expects 404, so originals stay private after restore).
6. `--cleanup` deletes each object by UID and confirms that nothing with the
   drill label remains.

```sh
python3 scripts/media_restore_drill.py --operation <id> --day <YYYY-MM-DD> --evidence <file> --plan
python3 scripts/media_restore_drill.py --operation <id> --day <YYYY-MM-DD> --evidence <file> --run
python3 scripts/media_restore_drill.py --operation <id> --evidence <file> --cleanup
```

Run it under the `NS-weather` lock. A drill reserves about 36 GiB of ephemeral
storage and takes about 15 minutes in total.

**RTO** runs from the start of the copy to the first rendered day page.
**RPO** compares two things: the newest production media row in custody, and the
newest of 541 production rows. Those rows are the newest 40 plus the newest of
every hour for 21 days, read from a `weather-pg` replica. A positive control
(HEAD on a key just copied) must succeed.

## Recorded drills

### 2026-11-17 (operation `20261117k`), PASS

| Field | Value |
|---|---|
| Date | 2026-11-17, copy started 06:49:36Z, page rendered 06:59:25Z |
| Revision | `f1bdd2aab0ff8a567d342aebbcafd1c682188047` (branch `w5c/wx77-media-drill`) |
| Digests | api and driver `weather-app@sha256:44c3c174…`; web `weather-web@sha256:084d8a36…`; catalog `postgresql:16.16-system-trixie@sha256:d7289505…`; Garage `garage-runtime-deadline@sha256:f54c84df…` |
| Source tier | B2 (`object-custody-vallery` for objects; `weather-db-vallery` base `20261031T091501` for the catalog, replayed to 2026-10-31T11:19:41Z) |
| Day | 2026-10-29: 588 available catalog rows (2898 rows on other days scoped out) |
| Objects | `weather-media` 516 rows: 515 copied, 1 withheld (3.36 GB). `weather-originals` 72 copied (114 MB). Missing from custody: 0. SHA-256 mismatches: 0 |
| Reconcile | `weather-media`: 516 checked, 515 present, 1 missing marked `unavailable` (exactly the withheld still). `weather-originals`: 72 present |
| Probes | day page 200 (199533 bytes, with media links and the unavailable state); restored still 200; withheld still 404; original ID 404 |
| RTO (measured) | 590 s (copy 520 s for 3.47 GB) |
| RPO (achieved) | 53142 s (14.8 h). The newest custody object is from 2026-11-16T16:03:53Z, and 52 newer production keys are not yet in custody. 488 older keys were checked, with 0 403s on expected-present keys |
| Cleanup | `kubectl get all,pvc,pv,secret,networkpolicy,configmap -l weather.vallery.net/restore-drill=20261117k` in `weather` returns nothing. The drill Garage was pod-local and is gone |

No RTO or RPO target has been set for weather media. The values above are
measured results, not guarantees; a target is a proposal for weather#6. The
catalog's offsite base is older than the objects: the newest `weather-pg` B2 base
is from 2026-10-31, because WAL archiving now goes to `weather-pg-local`. A full
loss restored from B2 alone would recover objects up to about 15 h old, but
catalog rows only to 2026-10-31. That gap is tracked in weather#6.

Earlier attempts that day (operations `a` to `j`) failed closed and were torn
down by UID. Each found a drill-harness defect, fixed in the commits on this
branch. None found a restore defect.
