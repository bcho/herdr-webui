# Security review

Scope: `server.py`, `index.html`, tests, documentation, mockups, and tracked Git
history. Includes restart-safe CSRF recovery and the Alt-Enter control.

## Findings and fixes

| Finding | Impact | Resolution |
| --- | --- | --- |
| No socket read timeout; negative or ambiguous HTTP body lengths accepted | Stalled requests could retain handler threads; a negative length requests an unbounded body read | Set a 10-second socket idle timeout, require one nonnegative ASCII decimal Content-Length, reject Transfer-Encoding, enforce the existing 64 KB cap, reject incomplete bodies, and return 408 for body timeouts |
| Identifier/key regexes used `$` with `match()` | A final newline passed validation despite the documented identifier/key allowlist | Use `fullmatch()` for identifiers, keys, and agent names |
| Browser token remains stale after a service restart | Legitimate actions fail with 403; availability issue, not a CSRF bypass | Explicit machine-readable pre-action rejection permits refreshing the same-origin page token and retrying the original payload once; no retries for other failures |

Regression tests cover malformed framing, slow headers/bodies, newline rejection,
Host/client/CSRF checks, security headers, stale-token recovery, bounded retries,
refresh failure, original-target/draft preservation, and concurrent refreshes.

## Verified protections

- Listener is loopback-only. Exact Host allowlist rejects unconfigured hosts.
- Optional client allowlist is checked before serving pages or tokens and before
  checking mutation credentials. It depends on a trusted local proxy overwriting
  forwarding headers; local processes remain trusted.
- Mutations require the per-process token and JSON content type. Tokens are not
  stored in localStorage. Responses use `no-store`; no CORS allow-origin header.
- CLI invocations use argv lists without a shell. IDs, keys, agent kinds, text
  sizes, read counts, and split directions have validation.
- Terminal text and user-supplied names are escaped at reviewed HTML sinks;
  browser regressions include malicious labels. CSP denies external origins and
  framing. Session-file paths are omitted from normalized pane data.
- No application third-party package dependency, CDN, installation hook, or
  dependency manifest. Playwright/Chrome are optional preinstalled test tools.
- Pattern scanning found no credential literals in tracked current text or
  historical text blobs. This is not a guarantee that every possible secret
  format has been detected. Mockup images use fictional data.

## CVE applicability and limits

- Python's [3.13 changelog](https://docs.python.org/3.13/whatsnew/changelog.html)
  lists fixes newer than 3.13.11. Python 3.13.11 is **not** a blanket CVE-clean
  runtime: for example, [CVE-2026-4224](https://nvd.nist.gov/vuln/detail/CVE-2026-4224)
  affects its XML/Expat declaration handler. This bridge does not parse XML or
  register that handler, so that advisory is not an identified application
  attack path. Runtime maintenance remains a separate operator responsibility.
- [CVE-2025-13836](https://nvd.nist.gov/vuln/detail/CVE-2025-13836), excessive
  response buffering in `http.client`, was fixed in Python 3.13.11. The bridge
  does not make outbound HTTP requests in production.
- Canonical public GitHub advisory feeds for
  [Herdr](https://github.com/ogulcancelik/herdr/security/advisories) and
  [Playwright Python](https://github.com/microsoft/playwright-python/security/advisories)
  returned no published entries during this review. This does **not** prove
  absence of vulnerabilities or cover transitive dependencies inside separately
  installed binaries. Advisories for the separate `herdr-remote` project do not
  automatically apply to this repository.
- No scanner was installed or software upgraded. The review used source
  inspection, bounded credential-pattern scanning, authoritative advisory
  research, and executable regressions—not an exhaustive SCA/SBOM audit of the
  host, browser, Python distribution, or Herdr binary.

## Residual risk

**Moderate risk; private use only.** This dashboard intentionally controls real
terminals. It has no application login, per-pane authorization, rate limiter, or
connection concurrency cap. Socket timeouts limit idle stalls, not all sustained
resource-exhaustion attacks. Standard-library HTTP serving and inline-script CSP
are not a hardened public-service boundary. Do not expose it to public/LAN
listeners, untrusted users, or an arbitrary reverse proxy. Terminal output may
itself contain sensitive information; do not forward it automatically to remote
services or an LLM.
