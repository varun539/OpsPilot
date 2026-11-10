---
id: KB-002
title: "Missing or Invalid Environment Variables at Application Startup"
category: configuration
component: "Application settings layer (environment loading / Pydantic Settings)"
severity: critical
tags:
  - environment-variables
  - configuration
  - startup-failure
  - pydantic-settings
  - twelve-factor
  - dotenv
  - secrets-management
source_type: synthetic
applies_to: "Python >= 3.10, pydantic-settings >= 2.0, python-dotenv >= 1.0"
last_updated: 2026-11-10
related_documents:
  - KB-001
  - KB-007
---

# Missing or Invalid Environment Variables at Application Startup

## 1. Problem Overview

Applications read environment variables to obtain values that differ between environments:
database URLs, service endpoints, feature flags, and credentials. A *missing* variable and an
*empty* variable are different conditions, and both differ from a *malformed* one.

- **Missing** — the key is absent from `os.environ`; accessing it via `os.environ["KEY"]`
  raises `KeyError`, via `os.getenv("KEY")` returns `None`.
- **Empty** — the key exists with an empty string. `os.getenv` returns `""`, which is falsy but
  not `None`, so `if not value` checks may pass while validation that expects a URL fails.
- **Malformed** — the key exists but does not parse: a URL without a scheme, a port that is
  not an integer, a boolean written as `"yes"` where a strict parser expects `"true"`.

The reason this is a *critical* severity rather than an inconvenience is the failure mode.
A variable consumed lazily at request time turns into a `500` on arbitrary endpoints
(see KB-001), often intermittently, and often only on the replica whose configuration drifted.
A variable validated at startup turns into a crash-loop that is obvious in the logs. The
second behaviour is preferable: **fail fast, fail loudly, and fail at boot**.

Environment variables are also loaded from several places with a precedence order that
surprises people: real process environment, then a `.env` file if the application loads one,
then code defaults. In containers the `.env` file usually does not exist, so a service that
works locally on the strength of `.env` can fail in production where only the platform's
environment injection applies.

## 2. Symptoms and Typical Error Messages

**At import/startup time:**

```text
KeyError: 'DATABASE_URL'
```

```text
pydantic_core._pydantic_core.ValidationError: 3 validation errors for Settings
database_url
  Field required [type=missing, input_value={...}, input_type=dict]
jwt_secret
  Field required [type=missing, input_value={...}, input_type=dict]
log_level
  Input should be 'debug', 'info', 'warning', 'error' [type=literal_error, input_value='verbose', input_type=str]
```

```text
ValueError: invalid literal for int() with base 10: 'eight-thousand'
```

**At request time (the worse case):**

```text
TypeError: 'NoneType' object is not iterable
```
raised where an unset `ALLOWED_ORIGINS` was expected to be a list, or:

```text
sqlalchemy.exc.ArgumentError: Could not parse SQLAlchemy URL from given URL string
```

**Container/orchestrator symptoms:**

- Process exits immediately with a non-zero code; restart count climbs and the pod is in
  `CrashLoopBackOff`.
- `docker inspect` shows `"ExitCode": 1` with the traceback in `docker logs`.
- Local `pytest` passes while the deployed service fails — a strong signal that configuration
  is environment-supplied and untested.

**Behavioural symptoms that masquerade as code bugs:**

- Authentication rejects every token because a signing key resolved to `""`.
- CORS failures because the allowed origin list is empty (browser blocks, API appears fine).
- Writes landing in a development database because `DATABASE_URL` silently fell back to a
  default.

## 3. Possible Root Causes

**Loading precedence and location**

- `.env` is not present in the container image (correctly excluded by `.gitignore`), and the
  platform variables were never set.
- The application reads `.env` from the current working directory, but the process is started
  from a different directory, so the file is not found.
- `load_dotenv()` is called *after* the settings object is instantiated at import time, so the
  values are read before the file is loaded.
- A variable is set on the host shell but not exported (`VAR=value` instead of
  `export VAR=value`), so child processes never see it.

**Naming and typing**

- Typo or case mismatch: environment variables are case-sensitive on Linux; `DATABASE_URL`
  and `database_url` are different keys unless the settings layer is case-insensitive.
- Prefix conventions: a settings class using `env_prefix="OPSPILOT_"` will not read a bare
  `DATABASE_URL`.
- Quoting: `DATABASE_URL="postgresql://..."` in a shell file keeps the quotes in some parsers,
  producing a URL that fails to parse.
- Lists and booleans: `ALLOWED_ORIGINS=a,b` needs explicit splitting; `DEBUG=1` is not accepted
  by a parser that only understands `true`/`false`.
- Multiline values (private keys, certificates) broken by newline handling in YAML or in the
  platform UI.

**Secret management**

- A secret exists in the secret store but is not attached to this deployment, or is attached
  under a different key name.
- A rotated secret was updated in one environment and not another.
- The runtime identity lacks permission to read the secret, which surfaces as "missing"
  rather than "denied" if the error is swallowed.

**Frameworks and defaults**

- A code default (`localhost:5432`) masks the absence of a real value until traffic flows.
- Settings are cached at module import, so a later `os.environ` mutation has no effect and a
  test that monkeypatches the environment still sees stale values.

## 4. Step-by-Step Investigation

This section is **diagnostic only**. Do not paste values into tickets or chat; compare *keys*
and *shapes*, never secrets.

1. **Confirm the failure is configuration, not code.** Reproduce locally with the deployed
   configuration keys set to safe dummy values. If it starts, the defect is a missing value.

2. **List the keys the process actually sees.** Print sorted names and, at most, lengths:

   ```bash
   # Inside the container/process environment; names only.
   python - <<'PY'
   import os
   for key in sorted(k for k in os.environ if k.isupper()):
       print(f"{key}=<set:{len(os.environ[key])} chars>")
   PY

   docker exec api env | cut -d= -f1 | sort     # names only, never full env
   ```

3. **Compare with the declared contract.** Determine which variables the code requires, then
   diff that list against step 2:

   ```bash
   grep -RnoE 'os\.(environ\[|getenv\()[^)]*' app | sort -u
   grep -RnoE 'Field\(' app/core/config.py
   ```

   Anything required by code and absent from the environment is a candidate; anything present
   in one environment and absent in another explains "works in staging, fails in prod".

4. **Distinguish missing from empty.** `docker exec api printenv DATABASE_URL >/dev/null` exits
   non-zero when unset; an empty value exits `0` with no output. Checking the exit code is
   enough — do not print the value.

5. **Trace load order.** Confirm when settings are constructed relative to `.env` loading:

   ```python
   import app.core.config as config
   print(config.settings.model_fields_set)   # which fields came from the environment
   ```

   If fields are unset at import time but the `.env` file exists, loading is happening too
   late.

6. **Validate types explicitly, offline.** Re-run the settings model against captured key
   names to collect *all* errors at once instead of fixing them one crash at a time:

   ```python
   from app.core.config import Settings
   try:
       Settings()
   except Exception as exc:          # pydantic.ValidationError
       print(exc)                    # lists every failing field with the reason
   ```

7. **Check the platform, not the app.** Inspect the deployment's environment/secret bindings
   and revision history; a variable removed during a "cleanup" is a common cause. Confirm
   which secret version is mounted and whether the workload identity has read access.

8. **Rule out a stale build.** If the variable exists but the app behaves as if it does not,
   confirm the running image/commit is the one you think it is (a cached image or an old
   container will read the old code path).

## 5. Safe Troubleshooting Recommendations

Remediation. Apply after the diagnosis identifies which key, in which environment, is wrong.

- **Add the value through the deployment's configuration mechanism** — platform environment
  settings, secret manager, or orchestrator secret — and redeploy. Editing a running
  container's environment is not durable and diverges from the declared configuration.
- **Never commit real values** to the repository, and never add a `.env` containing secrets to
  version control. A committed `.env.example` listing *names* and safe placeholders is the
  correct artefact.
- **Validate at startup** so a missing key stops the boot with a readable message rather than
  failing a random request later:

  ```python
  from functools import lru_cache
  from pydantic import Field, SecretStr, PostgresDsn, ValidationError
  from pydantic_settings import BaseSettings, SettingsConfigDict


  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

      database_url: PostgresDsn
      jwt_secret: SecretStr = Field(min_length=32)
      log_level: str = "info"
      debug: bool = False


  @lru_cache
  def get_settings() -> Settings:
      return Settings()


  try:
      settings = get_settings()
  except ValidationError as exc:
      raise SystemExit(f"Invalid configuration: {exc}") from exc
  ```

  `SecretStr` keeps the value out of tracebacks and `repr()`; `PostgresDsn` rejects malformed
  URLs at boot instead of at first query.

- **Make absence explicit.** Remove convenience defaults for values that must be supplied
  per environment. A default that points at `localhost` is how production silently writes to
  the wrong database.
- **Fail closed, not open.** If an auth-related value is missing, the service must refuse to
  start. Never substitute a placeholder key or disable verification to get the service up.
- **Rotate a credential that may have been exposed** in logs, shell history, or a pasted
  traceback. Rotate through the secret manager; do not simply re-use it with a new name.
- Do not "fix" a missing variable by editing a committed file, and do not disable environment
  validation, TLS requirements, or authentication to bypass the failure.

## 6. Verification Steps

1. The settings model loads cleanly with the deployed key set:

   ```bash
   python -c "from app.core.config import get_settings; get_settings(); print('config ok')"
   ```

   Expect `config ok` and no validation errors.
2. The service starts and stays up: restart count stable, no `CrashLoopBackOff`, `/health`
   returns `200` (see the health contract in the API documentation).
3. Startup logs contain no validation errors, and no secret value appears in them:

   ```bash
   grep -iE 'secret|password|token' /var/log/opspilot/startup.log | grep -v '<redacted>'
   # expect: no output
   ```

4. A configuration test asserts the contract without real values, so CI catches drift:

   ```python
   import pytest
   from pydantic import ValidationError
   from app.core.config import Settings


   def test_missing_required_variable_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
       monkeypatch.delenv("JWT_SECRET", raising=False)
       with pytest.raises(ValidationError):
           Settings(_env_file=None)
   ```

5. Behaviour that depends on the value is correct end-to-end: the intended database receives
   the write, the configured origin is allowed, and the token signed with the configured key
   validates.
6. `.env` remains untracked (`git check-ignore -v .env`) and no secret appears in
   `git grep` over the repository.

## 7. Prevention Strategies

- Declare the full configuration contract in one typed settings class, with no module-level
  `os.environ` reads scattered through the codebase.
- Publish `.env.example` with every key, a safe placeholder, and a comment; keep it in sync
  with the settings model, and diff it in review when a field is added.
- Validate at startup, before the server binds a port, and print *all* invalid fields at once.
- Treat configuration as code: version the deployment manifests, review environment changes,
  and record which values must be set per environment.
- Inject production values through the platform's secret manager, scoped to the workload, and
  audit reads.
- Add a startup smoke test in CI that boots the app with a complete dummy configuration, so a
  missing key fails the pipeline rather than the deploy.
- Avoid `lru_cache` on settings in tests without clearing it; provide a fixture that resets the
  cache to prevent stale-config test flakiness.
- Document rotation procedure and expiry tracking for every credential so "it worked
  yesterday" has a known explanation.

## 8. Related Technical Concepts

- Twelve-factor configuration: config in the environment, strict separation from code.
- Precedence between process environment, dotenv files, and defaults.
- `pydantic-settings` (`BaseSettings`, `env_prefix`, `SecretStr`, nested delimiters) and
  validation errors aggregated per field.
- Secret managers, workload identity, and least-privilege secret access.
- Container image immutability and why runtime environment edits are not reproducible.
- Fail-fast startup versus lazy configuration access, and the observability difference
  between a crash-loop and a scattered `500`.
- CORS origin configuration and its browser-side failure signature.
