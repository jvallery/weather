# Exact font reuse review

Decision: use the two **unmodified** Fontsource 5.3.0 WOFF2 members below as locally hosted assets when implementing the Weather frontend. This separate font-license review resolves D13's provenance question; it does not import assets now, grant rights in a wordmark, or change Weather's MIT code license.

## Evidence chain

The active WordPress source migration record points to historical `jvallery/vallery.net` revision `b125a47c4440b3e16551b369c3b9f34014124f37`. Its package-lock pins both Fontsource packages at 5.3.0 with SHA-512 integrity values. On 2026-10-23 the exact npm registry archives were fetched into memory, bounded below 8 MB each, and independently matched those lockfile integrities. No package was installed and no package code/script was executed.

Each selected archive member exactly matches the corresponding font bytes at WordPress revision `7d7e5c4b0588f1a323221111dba5df14646aec98`, which the earlier live HTTP readback independently matched. The [machine-readable receipt](../research/font-provenance.json) records archive URLs, exact integrity, member hashes, license hashes and package metadata. The [live theme receipt](../research/theme-evidence.json) remains a historical observation; its then-open licensing limitation is resolved by this later evidence, not silently rewritten.

| Package pin | Selected member | Font SHA-256 |
|---|---|---|
| `@fontsource-variable/ibm-plex-sans@5.3.0` | `package/files/ibm-plex-sans-latin-wght-normal.woff2` | `e2291e842cf5af167122a22881a740c7f2dda7716f1e8cd76680264f4a859470` |
| `@fontsource/ibm-plex-mono@5.3.0` | `package/files/ibm-plex-mono-latin-400-normal.woff2` | `08949f728dc52d528e69b1667d15c89a5686a4ee9a296ff90983985f99c380f7` |

Both exact packages declare OFL-1.1 and contain `package/LICENSE`; the actual packaged notices were read, not inferred from filenames or a badge. Fontsource also identifies the families under OFL-1.1 on its [Sans](https://fontsource.org/fonts/ibm-plex-sans/about) and [Mono](https://fontsource.org/fonts/ibm-plex-mono/about) primary pages. Package-specific notices take precedence over a paraphrased generic attribution.

## Distribution obligations and scope

Keep the fonts under OFL-1.1, with the complete corresponding copyright notice and license available in the distributed artifact and local third-party notices. Do not describe these files as MIT. The OFL permits bundling/embedding with software, subject to its conditions; it does not require the surrounding application to use the font license. Do not sell the font files by themselves or imply IBM endorsement. Preserve the bytes and family identities selected here. Any later subsetting, conversion, alteration or other modified-font proposal requires a fresh reserved-name/license review; this decision covers no font modification.

The Sans and Mono package copyright headers differ. Retain each package's actual LICENSE separately or preserve both complete notices when composing a distribution notice. Verify each notice against the recorded license hash. Do not replace either with a guessed common header, copy another version's notice, or rely on the current main-site theme to supply Weather's distribution obligations.

WX-20/26 acceptance: the reproducible build obtains the exact integrity-verified packages or an equivalently verified approved cache, ships only the selected needed font members and their complete notices, serves them locally with cacheable hashed URLs, and confirms fallback/layout behavior without any public font-host dependency. Inventory package and file hashes in release provenance. No npm install, image build, font modification or application asset copy occurred in this planning review.

This closes the exact-font provenance and selected unmodified-use decision. Actual distribution compliance and rendered accessibility remain implementation checks. Other dependencies, FFmpeg/model licenses and any copied wordmark remain separate reviews.
