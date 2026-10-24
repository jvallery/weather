// SPDX-License-Identifier: MIT
/** Response security headers. Only the embed card may be framed, and only by the vallery.net homepage (D13). */
export const EMBED_PARENT = "https://vallery.net";
/** The embed route and everything below it, so its 404s and errors keep the same framing policy. */
export function isEmbedPath(path) {
  return /^\/embed\/weather(?:\/|$)/i.test(path);
}
export function securityHeaders(path, nonce) {
  const embed = isEmbedPath(path);
  return {
    "Content-Security-Policy": `default-src 'self'; script-src 'self' 'nonce-${nonce}'; style-src 'self'; font-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors ${embed ? EMBED_PARENT : "'none'"}`,
    // X-Frame-Options cannot name an allowed parent; CSP frame-ancestors alone governs the embed.
    ...(embed ? {} : { "X-Frame-Options": "DENY" }),
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
  };
}
