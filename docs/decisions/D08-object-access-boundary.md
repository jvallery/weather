# D08 — role-scoped object gateway and independent archive access

Status: **boundary and operation policy selected; owner implementation/admission and Barman qualification pending**. Requirements R17/R18/R23/R27/R30/R31; WX-07/17/19/28/32/37/38. Owner prerequisite: [OWN-GATEWAY](https://github.com/jvallery/storage-infra/issues/1108). No credentials, gateway or bucket changed during this planning work.

## Architecture and trust

Keep one private Garage bucket, with logical asset classes enforced by a Weather-specific adapter in the storage owner's gateway. The current consumer-specific adapter is not reusable unchanged: its database authority and restricted operation surface do not cover Weather media and Barman. Reuse the owner framework and supported deployment lifecycle, not another application's credentials or schema authority.

Expose two distinct private interfaces: an asset-ID interface for the media origin and a qualified S3-compatible interface for workers/Barman. Browsers see only the Weather-domain public API/media resolver. Worker credentials authenticate to the gateway; they are not native Garage bucket-wide keys. The upstream Garage credential stays exclusively in the trusted gateway process and its owner-controlled recovery custody. Network policy and secret projections must prevent callers from bypassing it.

Media authorization reads a committed, revisioned publication projection and fails closed when freshness/visibility cannot be established. Archive authorization uses independently recoverable fixed bucket/incarnation/server scope and role policy; it never queries the Weather database being restored. Separate role authentication and request dispatch so media-authority failure cannot accidentally widen archive access or stop an otherwise healthy independent archive path.

Gateway compromise is a trust boundary: its upstream authority can access the bucket. Prefix naming alone does not reduce that authority. The independent restore reader and central custody path remain owner-controlled and must survive loss of this gateway and Weather's database through the D15 bootstrap contract. No public presigned Garage URL or hidden direct-key route is allowed.

## Effective operation matrix

All unlisted actions are denied. Every allow is additionally scoped to the authenticated role, immutable asset/archive identity and admitted request/byte limits. A role name in a request body is never authority.

| Role | GET/HEAD | PUT and multipart | List | CopyObject | DeleteObject/DeleteObjects | Administration |
|---|---|---|---|---|---|---|
| Browser | Weather public routes only; no S3 | Deny | Deny S3 | Deny | Deny | Deny |
| Media origin | Committed eligible public asset ID only | Deny | Deny | Deny | Deny | Deny |
| Publisher | Its job's media/poster/metadata objects for verification | Its job's immutable media/poster/metadata identities | Its own outstanding upload IDs only | Same class, authorized job source and immutable destination only | Abort owned multipart only; no object deletion | Deny |
| Image worker | Job-authorized private originals and required public image/keogram/preview inputs | Job-authorized originals and transformed image products | Its own outstanding upload IDs only | Same class only; never original-to-public copying | Abort owned multipart only; no object deletion | Deny |
| Media retention | Exact candidate HEAD only | Deny | No arbitrary bucket listing; consume authorized GC candidates | Deny | Exact unreferenced media candidates after transaction/pin/grace checks | Deny |
| Database archive | Fixed database incarnation/server hierarchy | Qualified Barman-required writes and multipart in that hierarchy | Qualified prefix/pagination and multipart operations within that hierarchy | Deny initially; qualify any demonstrated Barman requirement explicitly | Qualified Barman retention only, with independent recovery/PITR protection checks | Deny |
| Independent recovery | Exact verified database/media/config set | Deny on source; separate isolated restore-destination authority | Only admitted independent discovery scope | Deny | Deny | Deny |
| Central custody | Existing owner-controlled source scope | Existing offsite writer contract, not this application route | Existing custody inventory scope | Existing owner workflow only | Existing protected lifecycle only; no new authority | Deny through application gateway |
| Bucket administrator | Owner provisioning/recovery identity | Owner provisioning/recovery identity | Owner scope | Owner scope | Explicit owner operations | Owner-only control path, never runtime passthrough |

Multipart includes CreateMultipartUpload, UploadPart, ListParts, CompleteMultipartUpload, AbortMultipartUpload and bounded ListMultipartUploads where admitted. Ownership of upload ID, destination key, role and job/incarnation is established at initiation and checked on every later action. UploadPartCopy is a copy operation and must satisfy both source and destination policy; otherwise reject it. No role may guess another role's upload ID to complete, inspect or abort its upload.

ListObjectsV2 is the baseline for archive listing; support ListObjects v1 only if the pinned Barman client actually requires it and the same constraints are qualified. Continuation tokens bind the original prefix/delimiter/role/incarnation and cannot be replayed under broader scope. XML DeleteObjects validates every entry before any upstream mutation; a mixed authorized/forbidden batch is rejected as a whole. Successful S3 responses must preserve client-required status, XML and error semantics, not translate everything into a custom JSON success.

The primary S3 references for [CopyObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CopyObject.html) and [CompleteMultipartUpload](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html), reviewed 2026-10-23, document embedded errors after an HTTP 200 header. Consume and validate the complete operation response, preserve SDK retry/error behavior, and bind success to the actual expected object readback. An initial 200 or connection close is not upload success. These are compatibility requirements to test against the installed Garage/gateway, not a claim that all AWS features exist there.

## Key, signature and copy validation

Verify the caller's signature against the original request under the supported S3 authentication contract, then map to one canonical internal bucket/key representation. Do not forward caller Authorization or trust Host/forwarded-host as the upstream bucket selector. Reject alternate bucket names, ambiguous encoding, encoded separators, dot/traversal segments, duplicate separators, invalid UTF-8, NUL and unsupported query/action combinations. Generated Weather/Barman keys use a qualified bounded ASCII subset; do not add Unicode/path normalization aliases. Canonicalization and SigV4 compatibility require actual SDK tests, not hand-written URL examples alone.

Authorize CopyObject and UploadPartCopy source independently of destination, including the decoded copy-source header and any source-version selector. Permit only known immutable same-class sources belonging to the authorized job. A private original can become a public derivative only through a validated transformation with metadata/mask/time eligibility receipts; copying identical private bytes to a public prefix cannot bypass this rule. Reject mismatched asset class, content type and catalog identity even if the prefix string looks valid.

Reject caller-controlled ACLs, public-read grants, bucket policy/lifecycle/versioning/website operations and arbitrary metadata that could forge publication or protection receipts. The gateway assigns trusted identity metadata itself. Unknown S3 actions fail closed. No successful request mutates the allowed scope or silently falls back to a native provider key.

## Immutability, publication and deletion

Bind each upload to an expected asset identity, size/hash and fencing generation. Existing same-identity/same-content retries may return the verified original result; differing content is a conflict. The gateway must prevent racing multipart completions from overwriting an immutable key. A preflight HEAD followed by unconditional PUT is insufficient. Qualify the installed provider's conditional operation behavior or use the owner's serialized immutable reservation/journal protocol; do not claim atomic immutability before that mechanism is proven.

Upload/readback alone does not establish power-loss durability or public eligibility. D07's durability receipt and the [publication transaction](../implementation/publication-recovery-contract.md) remain mandatory before pointer activation or raw cleanup. The media origin checks tombstone, expiry, class, revision and exposure upper bound plus 600 seconds before headers, conditional responses or bytes. Unavailable authorization does not permit stale original/backup access.

Retention receives an exact candidate set and rechecks active publication, pending jobs and recovery/PITR pins immediately before deletion. An expired lease or tombstone alone is insufficient. Archive retention uses independent archive/catalog recovery metadata available during Weather DB loss; fail closed if required protection evidence is unavailable. Qualification must reconcile Barman's retention behavior with these checks without pretending a denied deletion was successful. Orphan multipart cleanup is identity-bound and must not abort another live uploader.

## Barman qualification and recovery

Before enabling archive traffic, run the pinned Barman/CNPG plugin through the actual gateway: base backup, WAL upload/check, list/pagination, multipart/retry/abort, restore and retention. Include an empty new archive, wrong incarnation, partial upload, uncertain acknowledgement, unavailable database, absent protection ledger and gateway restart. Check archive-server and destination naming exactly; a custom media upload endpoint is not Barman compatibility.

Archive and restore credentials/configuration must be recoverable without the failed database, gateway-local disk, NAS or original Mac. D15 owns that bootstrap evidence. The gateway may be reconstructed from private pinned configuration and recovery authority, but media credentials still cannot read backups while recovery is underway. No restored service reuses a divergent old archive identity.

## Acceptance, remaining decisions and rollback

WX-07 owns the full cross-product role × action × class negative matrix, including GET/HEAD/range, guessed IDs, encoded keys, copy source, mixed deletes, stolen upload IDs, pagination escape, direct-provider bypass and same-key concurrent writes. WX-19 verifies every public alias/download/thumbnail route and cache behavior. WX-28/32 prove Barman operation and independent recovery with Weather DB unavailable; WX-37/38 prove originals/derivative/embargo separation.

OWN-GATEWAY must finish the exact upstream authentication/reservation mechanism, independently recoverable archive policy, installed-provider operation compatibility and bounded gateway resources. Those material implementation approaches remain open; this matrix does not assert that the current gateway already meets them. Record admitted operation versions and negative evidence before enabling traffic. Roll back only scoped gateway/policy revisions, preserving all objects, journals, multipart ownership and independent recovery access; never replace isolation with broad native keys as a rollback shortcut.
