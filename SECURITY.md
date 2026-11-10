# Security reporting and boundaries

Weather is deployed: the public service at weather.vallery.net runs from `main` through Argo CD Application `weather`, with image digests pinned in `deploy/k8s`. Development continues on `main`; there are no versioned releases.

Report suspected vulnerabilities using the repository's private vulnerability reporting feature when enabled. If that feature is unavailable, contact the repository owner through an existing private channel. Do not put exploit details, credentials, private media or infrastructure receipts into a public issue. A public issue may request a private contact without disclosing the vulnerability.

The public service is intended to expose only sanitized published weather/media and bounded read APIs. Private originals, database archives, hardware identities, precise location and administrative writes are outside that public contract. Any future implementation must test those boundaries, including alternate routes, encoded paths, range requests and caches.

Only the digests currently pinned on `main` are supported; fixes ship as a new pin, not as backports. Dependency/security support windows will be recorded if versioned releases are introduced. Do not infer a response-time commitment or production security qualification from this document.
