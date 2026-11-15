"""Deterministic configuration validation tool."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .models import DiagnosticFinding, DiagnosticResult, Severity
from .sanitization import is_sensitive_key, redact_text

TOOL_NAME = "config_validator"

_TYPE_ALIASES = {
    "str": str,
    "string": str,
    "text": str,
    "int": int,
    "integer": int,
    "number": (int, float),
    "float": float,
    "double": float,
    "bool": bool,
    "boolean": bool,
    "list": list,
    "array": list,
    "dict": dict,
    "object": dict,
    "url": str,
    "port": int,
    "email": str,
}


def _normalize_rules(rules: Any, required_fields: Any = None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if isinstance(rules, dict):
        # Could be {field: rule_dict} or {field: type}
        for name, rule in rules.items():
            if isinstance(rule, dict):
                r = dict(rule)
                r.setdefault("name", name)
                r.setdefault("field", name)
                normalized.append(r)
            else:
                normalized.append({"name": name, "field": name, "type": rule})
    elif isinstance(rules, (list, tuple)):
        for item in rules:
            if isinstance(item, str):
                normalized.append({"name": item, "field": item})
            elif isinstance(item, dict):
                r = dict(item)
                name = r.get("name", r.get("field", r.get("key")))
                if name is None:
                    continue
                r.setdefault("name", name)
                r.setdefault("field", name)
                normalized.append(r)
            else:
                normalized.append({"name": str(item), "field": str(item)})
    # required_fields shortcut
    if required_fields:
        for rf in required_fields:
            if isinstance(rf, str):
                # add if not present
                if not any(r.get("field") == rf for r in normalized):
                    normalized.append({"name": rf, "field": rf, "required": True})
                else:
                    for r in normalized:
                        if r.get("field") == rf:
                            r["required"] = True
    return normalized


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def _validate_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False
    return bool(parsed.scheme and parsed.netloc)


def _validate_email(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))


def _check_type(rule: dict[str, Any], value: Any) -> bool:
    type_spec = rule.get("type", rule.get("expected_type", rule.get("dtype")))
    if type_spec is None:
        # infer from "types" list
        types = rule.get("types")
        if types:
            type_spec = types
    if type_spec is None:
        return True
    if isinstance(type_spec, (list, tuple)):
        return any(_check_type({"type": t}, value) for t in type_spec)
    if isinstance(type_spec, type):
        # bool is subclass of int; handle strict
        if type_spec is bool:
            return isinstance(value, bool)
        if type_spec is int:
            return isinstance(value, int) and not isinstance(value, bool)
        return isinstance(value, type_spec)
    key = str(type_spec).lower()
    if key in ("any", "object", "*"):
        return True
    if key in _TYPE_ALIASES:
        alias = _TYPE_ALIASES[key]
        if isinstance(alias, tuple):
            return isinstance(value, alias)
        if alias is bool:
            return isinstance(value, bool)
        if alias is int:
            return isinstance(value, int) and not isinstance(value, bool)
        if key in ("url",):
            return isinstance(value, str)
        if key in ("port",):
            return isinstance(value, int) and not isinstance(value, bool)
        if key in ("email",):
            return isinstance(value, str)
        return isinstance(value, alias)
    # dotted type name
    return type(value).__name__.lower() == key


def validate_configuration(config: Any, **options: Any) -> DiagnosticResult:
    rules = options.get("rules", options.get("validation_rules", options.get("rule_set")))
    required_fields = options.get("required_fields", options.get("required"))
    preset = options.get("preset", options.get("preset_name"))
    environment = options.get("environment", options.get("env"))
    redact = options.get("redact", options.get("redact_secrets", True))
    max_fields = int(options.get("max_fields", 10_000))

    warnings: list[str] = []

    if not isinstance(config, dict):
        return DiagnosticResult(
            tool_name=TOOL_NAME,
            status="invalid_input",
            summary="Configuration must be a mapping.",
            findings=[],
            analyzed_items=0,
            warnings=["config must be a dict-like mapping."],
            metadata={"config_type": type(config).__name__},
        )

    # Presets justified by OpsPilot docs: basic required fields for app config.
    if preset == "opspilot_basic" or preset == "basic":
        rules = rules or {}
        # merge
        base_rules = {
            "DATABASE_URL": {"required": True, "type": "url"},
            "JWT_SECRET": {"required": True, "type": "str", "min_length": 32},
        }
        if isinstance(rules, dict):
            merged = dict(base_rules)
            merged.update(rules)
            rules = merged
        elif rules is None:
            rules = base_rules

    normalized_rules = _normalize_rules(rules, required_fields)
    if max_fields > 0 and len(normalized_rules) > max_fields:
        warnings.append(
            f"Validation rules truncated to {max_fields} field(s) "
            f"(of {len(normalized_rules)})."
        )
        normalized_rules = normalized_rules[:max_fields]

    # Environment-specific rules: if rule has "environments" mapping, apply overrides
    if environment is not None:
        for r in normalized_rules:
            env_overrides = r.get("environments")
            if isinstance(env_overrides, dict) and environment in env_overrides:
                override = env_overrides[environment]
                if isinstance(override, dict):
                    r.update(override)

    findings: list[DiagnosticFinding] = []
    issues = 0
    validated_count = 0

    # Check required fields presence
    for rule in normalized_rules:
        field_name = str(rule.get("field", rule.get("name")))
        if field_name not in config:
            # required?
            if rule.get("required", False):
                issues += 1
                findings.append(
                    DiagnosticFinding(
                        code="CONFIG_MISSING_REQUIRED",
                        title=f"Missing required configuration: {field_name}",
                        description=f"Required field '{field_name}' is missing.",
                        severity=Severity.high,
                        evidence=[f"field={field_name}"],
                        source=TOOL_NAME,
                        remediation_suggestions=[f"Provide a value for {field_name}."],
                        observed_facts=[f"{field_name} is missing."],
                        detected_patterns=["missing_required_field"],
                    )
                )
            continue

        value = config.get(field_name)
        validated_count += 1

        # Empty check
        if rule.get("required", False) and _is_empty(value):
            issues += 1
            findings.append(
                DiagnosticFinding(
                    code="CONFIG_EMPTY_VALUE",
                    title=f"Empty configuration value: {field_name}",
                    description=f"Required field '{field_name}' is empty.",
                    severity=Severity.high,
                    evidence=[f"field={field_name}"],
                    source=TOOL_NAME,
                    remediation_suggestions=[f"Provide a non-empty value for {field_name}."],
                    observed_facts=[f"{field_name} is empty."],
                    detected_patterns=["empty_config_value"],
                )
            )
            continue

        # If value empty but not required, skip further checks
        if _is_empty(value) and not rule.get("required", False):
            continue

        # Type validation
        if not _check_type(rule, value):
            issues += 1
            type_name = rule.get("type", rule.get("expected_type", "unknown"))
            findings.append(
                DiagnosticFinding(
                    code="CONFIG_INVALID_TYPE",
                    title=f"Invalid type for {field_name}",
                    description=(
                        f"Field '{field_name}' expected type {type_name}, "
                        f"got {type(value).__name__}."
                    ),
                    severity=Severity.medium,
                    evidence=[f"field={field_name} value_type={type(value).__name__}"],
                    source=TOOL_NAME,
                    remediation_suggestions=[
                        f"Provide a value of type {type_name} for {field_name}."
                    ],
                    observed_facts=[f"{field_name} has type {type(value).__name__}."],
                    detected_patterns=["invalid_config_type"],
                )
            )
            continue

        # URL validation
        is_url = rule.get("url", False) or str(rule.get("type", "")).lower() == "url"
        if is_url and not _validate_url(value):
            issues += 1
            findings.append(
                DiagnosticFinding(
                    code="CONFIG_INVALID_URL",
                    title=f"Invalid URL for {field_name}",
                    description=f"Field '{field_name}' is not a valid URL.",
                    severity=Severity.medium,
                    evidence=[f"field={field_name}"],
                    source=TOOL_NAME,
                    remediation_suggestions=[
                        f"Provide a valid URL with scheme and host for {field_name}."
                    ],
                    observed_facts=[f"{field_name} URL validation failed."],
                    detected_patterns=["invalid_url"],
                )
            )

        # Port validation
        is_port = rule.get("port", False) or str(rule.get("type", "")).lower() == "port"
        if is_port:
            if (
                not (isinstance(value, int) and not isinstance(value, bool))
                or value < 1
                or value > 65535
            ):
                issues += 1
                findings.append(
                    DiagnosticFinding(
                        code="CONFIG_INVALID_PORT",
                        title=f"Invalid port for {field_name}",
                        description=f"Field '{field_name}' must be an integer between 1 and 65535.",
                        severity=Severity.medium,
                        evidence=[f"field={field_name} value={value}"],
                        source=TOOL_NAME,
                        remediation_suggestions=[f"Provide a valid port number for {field_name}."],
                        observed_facts=[f"{field_name} port validation failed."],
                        detected_patterns=["invalid_port"],
                    )
                )

        # Boolean strictness
        if rule.get("boolean", False) or str(rule.get("type", "")).lower() in ("bool", "boolean"):
            if not isinstance(value, bool):
                # allow string forms
                if isinstance(value, str) and value.strip().lower() in (
                    "true",
                    "false",
                    "1",
                    "0",
                    "yes",
                    "no",
                ):
                    pass
                else:
                    issues += 1
                    findings.append(
                        DiagnosticFinding(
                            code="CONFIG_INVALID_BOOLEAN",
                            title=f"Invalid boolean for {field_name}",
                            description=f"Field '{field_name}' must be a boolean.",
                            severity=Severity.medium,
                            evidence=[f"field={field_name}"],
                            source=TOOL_NAME,
                            remediation_suggestions=[f"Provide true/false for {field_name}."],
                            observed_facts=[f"{field_name} boolean validation failed."],
                            detected_patterns=["invalid_boolean"],
                        )
                    )

        # Integer range
        min_val = rule.get("min", rule.get("min_value"))
        max_val = rule.get("max", rule.get("max_value"))
        if min_val is not None or max_val is not None:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if min_val is not None and value < min_val:
                    issues += 1
                    findings.append(
                        DiagnosticFinding(
                            code="CONFIG_VALUE_BELOW_MIN",
                            title=f"Value below minimum for {field_name}",
                            description=(
                                f"Field '{field_name}' value {value} "
                                f"is below minimum {min_val}."
                            ),
                            severity=Severity.medium,
                            evidence=[f"field={field_name} value={value} min={min_val}"],
                            source=TOOL_NAME,
                            remediation_suggestions=[
                                f"Increase {field_name} to at least {min_val}."
                            ],
                            detected_patterns=["config_value_out_of_range"],
                        )
                    )
                if max_val is not None and value > max_val:
                    issues += 1
                    findings.append(
                        DiagnosticFinding(
                            code="CONFIG_VALUE_ABOVE_MAX",
                            title=f"Value above maximum for {field_name}",
                            description=(
                                f"Field '{field_name}' value {value} "
                                f"is above maximum {max_val}."
                            ),
                            severity=Severity.medium,
                            evidence=[f"field={field_name} value={value} max={max_val}"],
                            source=TOOL_NAME,
                            remediation_suggestions=[
                                f"Decrease {field_name} to at most {max_val}."
                            ],
                            detected_patterns=["config_value_out_of_range"],
                        )
                    )

        # String length
        min_len = rule.get("min_length", rule.get("minLength"))
        max_len = rule.get("max_length", rule.get("maxLength"))
        if isinstance(value, str):
            if min_len is not None and len(value) < min_len:
                issues += 1
                findings.append(
                    DiagnosticFinding(
                        code="CONFIG_VALUE_TOO_SHORT",
                        title=f"Value too short for {field_name}",
                        description=(
                            f"Field '{field_name}' length {len(value)} "
                            f"is below minimum {min_len}."
                        ),
                        severity=Severity.medium,
                        evidence=[f"field={field_name}"],
                        source=TOOL_NAME,
                        remediation_suggestions=[f"Provide a longer value for {field_name}."],
                        detected_patterns=["config_value_length"],
                    )
                )
            if max_len is not None and len(value) > max_len:
                issues += 1
                findings.append(
                    DiagnosticFinding(
                        code="CONFIG_VALUE_TOO_LONG",
                        title=f"Value too long for {field_name}",
                        description=(
                            f"Field '{field_name}' length {len(value)} "
                            f"exceeds maximum {max_len}."
                        ),
                        severity=Severity.medium,
                        evidence=[f"field={field_name}"],
                        source=TOOL_NAME,
                        remediation_suggestions=[f"Provide a shorter value for {field_name}."],
                        detected_patterns=["config_value_length"],
                    )
                )

        # Enum
        allowed = rule.get("enum", rule.get("allowed", rule.get("choices")))
        if allowed is not None and value not in allowed:
            issues += 1
            findings.append(
                DiagnosticFinding(
                    code="CONFIG_INVALID_CHOICE",
                    title=f"Invalid value for {field_name}",
                    description=f"Field '{field_name}' value is not among allowed choices.",
                    severity=Severity.medium,
                    evidence=[f"field={field_name}"],
                    source=TOOL_NAME,
                    remediation_suggestions=[f"Use one of the allowed values for {field_name}."],
                    detected_patterns=["config_invalid_choice"],
                )
            )

        # Pattern
        pattern = rule.get("pattern", rule.get("regex"))
        if pattern and isinstance(value, str):
            try:
                if not re.search(pattern, value):
                    issues += 1
                    findings.append(
                        DiagnosticFinding(
                            code="CONFIG_PATTERN_MISMATCH",
                            title=f"Pattern mismatch for {field_name}",
                            description=f"Field '{field_name}' does not match required pattern.",
                            severity=Severity.medium,
                            evidence=[f"field={field_name}"],
                            source=TOOL_NAME,
                            remediation_suggestions=[
                                f"Provide a value matching the required pattern for {field_name}."
                            ],
                            detected_patterns=["config_pattern_mismatch"],
                        )
                    )
            except re.error:
                warnings.append(
                    f"Invalid regex pattern for field {field_name}."
                )
     # Check for unknown extra fields? Not required.
     # Redact sensitive values in findings evidence.
    if redact:
        for f in findings:
            f.evidence = [redact_text(str(e)) for e in f.evidence]

    # Also detect sensitive fields present and warn? Redaction behavior.
    sensitive_present = [k for k in config.keys() if is_sensitive_key(k)]
    if sensitive_present:
        warnings.append("Sensitive fields were validated without exposing their values.")

    status = "valid" if issues == 0 else "invalid"

    metadata: dict[str, Any] = {
        "field_count": len(config),
        "validated_fields": validated_count,
        "rule_count": len(normalized_rules),
        "issue_count": issues,
        "environment": environment,
        "preset": preset,
        "redacted": True,
        "sensitive_fields": [str(k) for k in sensitive_present],
    }

    summary = f"Validated {len(config)} configuration field(s); {issues} issue(s) found."

    return DiagnosticResult(
        tool_name=TOOL_NAME,
        status=status,
        summary=summary,
        findings=findings,
        analyzed_items=len(config),
        warnings=warnings,
        metadata=metadata,
        observed_facts=[f"{len(config)} configuration fields provided."],
        detected_patterns=[f.code for f in findings],        potential_explanations=(
            ["Configuration issues may cause startup or runtime failures."] if issues else []
        ),
        recommended_next_steps=(
            ["Correct the reported configuration issues."]
            if issues
            else ["Configuration appears valid."]
        ),
    )
