---
id: KB-004
title: "JWT Authentication Failures: 401 Unauthorized and Token Validation Errors"
category: authentication
component: "JWT verification (token issuance, signing keys, and FastAPI auth dependency)"
severity: high
tags:
  - jwt
  - authentication
  - 401-unauthorized
  - signature-verification
  - jwks
  - token-expiry
  - clock-skew
  - algorithms
source_type: synthetic
applies_to: "Python >= 3.10, PyJWT >= 2.8, FastAPI >= 0.100"
last_updated: 2026-11-10
related_documents:
  - KB-002
  - KB-006
---

# JWT Authentication Failures: 401 Unauthorized and Token Validation Errors

## 1. Problem Overview

A JSON Web Token is three base64url-encoded segments: a header (algorithm and key ID), a
payload (claims), and a signature over the first two. Verification has four independent gates,
and failing any one produces a `401`:

1. **Structure** — the token parses as three segments with valid base64url and JSON.
2. **Signature** — recomputed with the key the header names, using the algorithm you allow.
3. **Claims** — `exp`, `nbf`, `iat`, `iss`, `aud` satisfy policy at the current time.
4. **Authorisation** — after identity is established, the subject is allowed to do this
   (which is a `403`, not a `401`).

Keeping `401` and `403` distinct matters for diagnosis: `401` means "I do not know who you are
(or your proof is stale)", `403` means "I know who you are and you may not do this". A client
retrying on `403` is a bug; a client refreshing a token on `401` is correct behaviour.

The endpoint that accepts a token is also part of the problem space. Most JWT incidents are
configuration drift between issuer and verifier — different algorithm, different secret, stale
JWKS cache, or a clock that disagrees — rather than an attack.

**Security baseline:** never resolve an authentication failure by disabling signature
verification, accepting `alg: none`, trusting the token's own `alg` field without an allow-list,
or turning off expiry checks. Those are not workarounds; they are vulnerabilities.

## 2. Symptoms and Typical Error Messages

**PyJWT exception families** (names are stable, messages vary by version):

```text
jwt.exceptions.InvalidSignatureError: Signature verification failed
jwt.exceptions.DecodeError: Not enough segments
jwt.exceptions.DecodeError: Invalid base64-encoded string
jwt.exceptions.ExpiredSignatureError: Signature has expired
jwt.exceptions.ImmatureSignatureError: The token is not yet valid (nbf)
jwt.exceptions.InvalidAlgorithmError: The specified alg value is not allowed
jwt.exceptions.InvalidAudienceError: Audience doesn't match
jwt.exceptions.InvalidIssuerError: Invalid issuer
jwt.exceptions.InvalidKeyError: Could not parse the provided public key
```

**HTTP-level symptoms**

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer error="invalid_token", error_description="Signature verification failed"
```

- `401` with an empty `WWW-Authenticate` header, or none at all — the client cannot tell a
  missing token from an invalid one.
- `403` on a route you believe is public — the dependency is doing authorisation, not
  authentication, or the route ordering is wrong.
- `401` on every request from one environment only — key or clock drift.
- Tokens work immediately after login and fail later, at the exact moment `exp` passes.
- Tokens work for a few minutes after a key rotation, then fail — a cached JWKS.

**Token-shape symptoms**

- `Not enough segments` usually means the header value retained the literal `Bearer ` prefix or
  quotes were copied from documentation.
- `Invalid base64-encoded string` often means the token was URL-decoded by an intermediary, or
  a `+` was turned into a space in a query string.
- A token that decodes fine on jwt.io but fails in the server typically differs in *which key*
  verifies it, not in structure.

## 3. Possible Root Causes

**Key and algorithm mismatch**

- Verifier configured with `HS256` while the issuer signs with `RS256` (or vice versa). With
  asymmetric tokens the verifier needs the public key, not the private key, and not the shared
  secret.
- Two different secrets across environments: staging tokens presented to production.
- Rotation: issuer signs with a new `kid`; verifier's cached JWKS has no matching entry.
- Symmetric secrets shorter than the hash output (HS256 needs at least 32 bytes) accepted by
  one library and rejected by another.
- Algorithm allow-list omitted, so the library picks the header's `alg` — the precondition for
  algorithm-confusion attacks.

**Claim and clock problems**

- `exp` honoured without leeway on hosts whose clocks drift by seconds; a token minted "now" is
  already expired on the verifier.
- `nbf`/`iat` set slightly in the future by a busy issuer.
- `aud`/`iss` checked strictly with values that differ by a trailing slash or environment
  suffix.
- `sub` is a string in one issuer and an integer in another; the code indexes a dict with the
  wrong type.
- Long-running requests: the token expires mid-request, or a downstream service re-verifies at
  the end and rejects.

**Transport and plumbing**

- Trailing whitespace, a duplicated `Authorization` header, or `Basic`/`Bearer` confusion.
- A reverse proxy stripping or rewriting `Authorization` (common with some default
  configurations) — the app sees no header at all.
- Cookie-based tokens with `SameSite`/`Secure` misconfiguration, so the browser never sends them
  on cross-site calls; the API then reports "missing token".
- CORS preflight (`OPTIONS`) rejected with `401` because the auth dependency runs on all methods.

**Token lifecycle**

- Refresh implemented as "reuse the access token", so clients keep presenting expired tokens.
- Logout deletes the cookie but the token remains valid until `exp` (stateless by design).
- Token revocation expected from a system that has no revocation list.

## 4. Step-by-Step Investigation

**Diagnostic only.** Never paste real tokens into chat, tickets, or third-party decoders; they
are bearer credentials. Decode locally and inspect claims only.

1. **Distinguish missing, malformed, and invalid.** Log which gate failed, as a category:

   ```python
   import jwt
   from fastapi import HTTPException, status

   def classify(token: str) -> str:
       try:
           header = jwt.get_unverified_header(token)
       except jwt.DecodeError:
           return "malformed"
       return f"header_alg={header.get('alg')} kid={header.get('kid')}"
   ```

   The header tells you immediately whether the token is asymmetric, which dictates the key type.

2. **Inspect claims without verifying** to confirm the token is the one you think it is:

   ```python
   claims = jwt.decode(token, options={"verify_signature": False})
   print({k: claims.get(k) for k in ("iss", "aud", "sub", "exp", "nbf", "iat", "kid")})
   ```

   Compare `iss`/`aud` with the verifier's configuration character by character, and `exp` with
   the *server's* clock.

3. **Compare clocks.** A few seconds of drift breaks `exp` checks with no leeway:

   ```bash
   date -u +%s                     # verifier
   python -c "import jwt,sys; t=sys.argv[1]; print(jwt.decode(t, options={'verify_signature': False})['exp'])" "$TOKEN"
   ```

4. **Verify the key type matches the algorithm.** For `RS256`/`ES256` the verifier needs a
   public key or JWKS entry; for `HS256` it needs the shared secret. A "signature verification
   failed" with a structurally valid token is almost always this or a rotated key.

5. **Check `kid` resolution.** If the token has a `kid`, confirm it exists in the verifier's key
   set, and confirm the cache refreshes when the issuer publishes new keys. A stale cache
   produces failures that start at a rotation boundary and often resolve after a restart — a
   diagnostic clue.

6. **Reproduce end-to-end with curl** to eliminate client-side behaviour:

   ```bash
   curl -i -sS http://127.0.0.1:8000/orders \
     -H "Authorization: Bearer ${TOKEN}" | head -n 20
   ```

   Compare with the browser/client path. A difference implicates header handling, cookies, or
   CORS rather than verification.

7. **Confirm the proxy forwards the header.** Check access logs for the request and whether the
   upstream received an `Authorization` header; some proxies drop it by default.

8. **Test the authorisation boundary separately.** With a known-good token, call a route the
   subject should not access. `403` confirms authentication works and the issue is policy;
   `401` confirms identity itself is failing.

## 5. Safe Troubleshooting Recommendations

Remediation. Apply one change at a time and keep verification enabled throughout.

- **Verify with an explicit algorithm allow-list and the correct key type:**

  ```python
  from functools import lru_cache

  import jwt
  from jwt import PyJWKClient

  JWKS_URL = "https://issuer.example.com/.well-known/jwks.json"
  ALGORITHMS = ["RS256"]
  AUDIENCE = "opspilot-api"
  ISSUER = "https://issuer.example.com/"


  @lru_cache
  def jwks_client() -> PyJWKClient:
      return PyJWKClient(JWKS_URL, cache_keys=True)


  def verify_access_token(token: str) -> dict:
      signing_key = jwks_client().get_signing_key_from_jwt(token)
      return jwt.decode(
          token,
          signing_key.key,
          algorithms=ALGORITHMS,
          audience=AUDIENCE,
          issuer=ISSUER,
          leeway=30,          # tolerate modest clock skew; keep it small
          options={"require": ["exp", "iat", "sub"]},
      )
  ```

  > **Version note:** `PyJWKClient` caching behaviour and constructor keywords (`cache_keys`,
  > `lifespan`) changed across PyJWT 2.x releases. Confirm the signature for your pinned
  > version, and ensure the cache refreshes on an unknown `kid` rather than only on a timer.

- **Align configuration across issuer and verifier** as data, not code: one source of truth for
  `iss`, `aud`, algorithm, and key material per environment (see KB-002). Rotate keys by
  publishing the new key alongside the old, then switching signing, then retiring the old key.
- **Return the right status with a useful `WWW-Authenticate` header** so clients can act:

  ```python
  from fastapi import HTTPException, status

  raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid or expired token",
      headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
  )
  ```

  Do not include the underlying exception text in the response; log it server-side with a
  correlation ID.
- **Add bounded leeway for clock skew** rather than accepting expired tokens broadly. If drift
  exceeds seconds, fix time synchronisation on the hosts.
- **Make token lifetime and refresh explicit:** short-lived access tokens plus a refresh flow,
  and clients that refresh on `401` exactly once before surfacing an error to the user.
- **Keep `401` and `403` semantically correct** and test both.
- **Never disable verification to unblock a deploy.** Do not set `verify_signature=False`,
  `options={"verify_exp": False}`, or an allow-list containing `none` outside of a throwaway
  unit test with a synthetic key. Do not log full tokens; log `jti` or a hash prefix.
- If a token may have leaked, treat it as compromised: rotate the signing key, invalidate
  sessions, and shorten lifetimes — do not simply extend expiry.

## 6. Verification Steps

1. A freshly issued token is accepted:

   ```bash
   curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/orders -H "Authorization: Bearer ${TOKEN}"
   # expect: 200
   ```

2. An expired token yields `401` with the `WWW-Authenticate` header; a tampered payload yields
   `401` with `invalid_token`; a valid token on a forbidden route yields `403`.
3. Negative tests cover each gate independently — bad signature, wrong audience, wrong issuer,
   `alg: none`, missing `sub` — and all are rejected:

   ```python
   import pytest
   import jwt


   def test_alg_none_is_rejected(client) -> None:
       forged = jwt.encode({"sub": "1"}, key="", algorithm="none")
       response = client.get("/orders", headers={"Authorization": f"Bearer {forged}"})
       assert response.status_code == 401
   ```

4. Logs contain the failure category and correlation ID, and no token value appears:

   ```bash
   grep -c 'eyJ' /var/log/opspilot/api.log   # expect: 0
   ```

5. After a simulated key rotation (new `kid` published), tokens signed with the new key verify
   without a restart.
6. Browser and CLI paths behave identically for the same token, confirming no proxy or CORS
   interference.

## 7. Prevention Strategies

- Pin the algorithm allow-list in code, next to the key source, and review changes to it as
  security changes.
- Automate key rotation with overlap windows and monitor `kid`-resolution failures as a distinct
  metric.
- Add a startup self-check that signs and verifies a synthetic token with the configured key;
  this catches "wrong key in this environment" before traffic arrives.
- Keep clocks synchronised (NTP) on every host and container; treat skew as an infrastructure
  incident.
- Test all rejection paths in CI, including `alg: none` and key-confusion cases.
- Log authentication outcomes by category (`missing`, `malformed`, `expired`, `bad_signature`)
  without logging credentials, and alert on a spike in any single category.
- Document the token contract (issuer, audience, lifetimes, refresh semantics) in one place and
  reference it from both services.

## 8. Related Technical Concepts

- JWS/JWT structure, base64url encoding, and signature algorithms (HMAC versus RSA/ECDSA).
- Algorithm confusion, `alg: none`, and key-type confusion attacks.
- JWKS discovery, `kid` matching, and key rotation with overlapping validity.
- Standard claims: `iss`, `aud`, `sub`, `exp`, `nbf`, `iat`, `jti`.
- OAuth 2.0 bearer token semantics and the `WWW-Authenticate` error format.
- Session cookies versus bearer headers, and CORS credential handling.
- Clock skew, leeway, and time synchronisation.
- Stateless tokens versus server-side revocation and short expiry with refresh.
