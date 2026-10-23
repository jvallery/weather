# Recovery closure byte contract v1

Status: selected planning contract for WX-08/28/30/32. Policy ID: `weather-closure-jcs-v1`. This defines reproducible bytes and digest inputs; it does not establish catalog completeness, trusted custody or restore success. See [recovery contract](recovery-contract.md) for those separate checks.

## Canonical bytes and identities

Use UTF-8 encoding of RFC 8785 JSON Canonicalization Scheme (JCS), with no BOM and no trailing whitespace. The [primary specification](https://www.rfc-editor.org/rfc/rfc8785), checked 2026-10-23, defines deterministic serialization and rejects duplicate properties and invalid Unicode. Preserve strings exactly; never normalize Unicode. All numeric values in these records are integers within the schema's safe-integer bounds, so no binary floating-point rounding is allowed. Reject out-of-range values before conversion to a runtime number. Reject duplicate JSON keys before an ordinary parser can discard them.

Object property ordering follows JCS. Separately, **closure row ordering** compares the UTF-8 bytes of `object_id`, unsigned lexicographically. This is deliberately specified rather than using locale, database collation or a language's default string comparison. Distinct canonically equivalent Unicode strings remain distinct IDs. Application-generated opaque identities should be ASCII; the contract still handles valid Unicode without silently changing it. Object IDs, not content hashes or locator IDs, establish uniqueness: equal bytes in two distinct assets do not collapse their catalog identity.

Each SourceObject has exactly the five fields in the schema. Catalog closure rows allow `video`, `poster`, `keogram`, `original`, `public_still`, `metadata` and `keogram_map`. Other SourceObject classes belong to separate recovery ledgers and are rejected from this closure. Preserve exact source locator identity, size and SHA-256. Sort all rows, then reject duplicate object IDs even when duplicate rows are identical. Resolve conflicting catalog references before emitting the candidate; never choose one by input order.

## Shards and closure digest

Partition the sorted stream into consecutive groups of **4096 rows**, except the final shard which may contain fewer. Never emit an empty shard. An empty catalog closure has zero shards, zero objects and zero total bytes; its database/bootstrap ledgers remain required. The schema ceiling of 10,000 shards is an admission limit, not permission to truncate a larger closure.

Encode each row as JCS bytes followed by exactly one LF byte (`0a`), including the last row. No CRLF, whitespace padding or blank lines. A shard's `bytes` is the length of these serialized bytes; its `sha256` hashes those bytes including LFs; `row_count` is the number of rows. Its deterministic `shard_id` is `closure-` plus a six-digit, zero-based decimal index. Its private locator is assigned immutably before the descriptor is committed and must resolve those exact bytes. Locator relocation changes the descriptor and root digest; reusing identical shard bytes does not imply an identical descriptor.

Construct the following root object with exactly these four fields:

- `closure_policy_revision`: `weather-closure-jcs-v1`.
- `closure_object_count`: sum of row counts, counting each immutable identity once.
- `closure_total_bytes`: sum of SourceObject `bytes`, **not** serialized shard sizes.
- `closure_shards`: complete ordered array of the schema's ClosureShard descriptors.

Hash the bytes `weather-recovery-closure-v1` followed by one LF, followed by the JCS root bytes with **no final LF**. The lowercase SHA-256 hex result is `catalog_closure_digest`. Include the entire descriptor array, including private locators. Do not include the checkpoint ID, generation, creation time or resulting digest in this input. The candidate separately binds generation and target to that closure. Equal closure digests do not establish equal database targets.

All sums use checked exact integer arithmetic and must remain within 0–9007199254740991. Reject overflow, repeated/out-of-order shards, noncanonical row bytes, incomplete partitions, counts/byte totals inconsistent with rows, and any unrecognized policy. A verifier recomputes hashes from the immutable bytes, then proves row/set equality with the catalog and destination ledgers; parsing and reserializing a noncanonical shard must not hide its byte mismatch.

## Database target digest and other files

`database_target_sha256` is SHA-256 of the exact candidate `database` object's JCS bytes, without a domain prefix or final LF. Include every required DatabaseTarget field; do not infer omitted values. PostgreSQL identifiers/LSNs stay strings as declared by the schema. Numeric JSON representations must not lose system-identifier precision.

Candidate files and offsite ledgers are hashed as their actual stored bytes, not silently reserialized. This distinction is intentional: only the closure root and DatabaseTarget have semantic canonical hash inputs; shard files are stored in the specified canonical JSONL format. The verified receipt binds the candidate file's exact independent version and byte hash. No record hashes itself.

## Golden vectors and acceptance

[recovery-canonical-vectors.json](recovery-canonical-vectors.json) records synthetic rows, canonical shard text, descriptors, exact root preimage and expected hashes, plus a database-target vector. The empty, ordinary, Unicode-ordering and safe-integer-limit examples were recomputed independently with Python and JavaScript during planning. The check covers this restricted record vocabulary, not general-purpose JCS compliance or runtime recovery. Production implementations must use or qualify a proper JCS implementation; Python `sort_keys` is not a universal substitute for JCS UTF-16 property ordering.

WX-30 must implement the declared negative vectors, cross-shard duplicate/set checks, 4096/4097-row partition boundaries, overflow and policy rejection. Also retain the recovery contract's equal-count/different-set and forged/untrusted receipt tests. Canonical hashes do not resolve the remaining owner ledger formats, independent discovery/trust, copy coverage, restore budgets or bootstrap admission.
