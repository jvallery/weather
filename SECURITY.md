# Security reporting and boundaries

Weather is under active implementation. No Weather application release is deployed yet.

Report suspected vulnerabilities using the repository's private vulnerability reporting feature when enabled. If that feature is unavailable, contact the repository owner through an existing private channel. Do not put exploit details, credentials, private media or infrastructure receipts into a public issue. A public issue may request a private contact without disclosing the vulnerability.

The public service is intended to expose only sanitized published weather/media and bounded read APIs. Private originals, database archives, hardware identities, precise location and administrative writes are outside that public contract. Any future implementation must test those boundaries, including alternate routes, encoded paths, range requests and caches.

Dependency/security support windows and the supported application version will be recorded when releases exist. Do not infer a response-time commitment or production security qualification from this document.
