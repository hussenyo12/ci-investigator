"""Bounded, deterministic pattern redaction; never a complete secret detector."""

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

RedactionCategory = Literal[
    "GITHUB_TOKEN",
    "BEARER_TOKEN",
    "BASIC_AUTH",
    "URL_CREDENTIALS",
    "API_KEY",
    "PASSWORD",
    "SECRET",
    "AWS_ACCESS_KEY",
    "PRIVATE_KEY",
]
MAX_REDACTION_CHARS = 4 * 1024 * 1024
_PEM_BEGIN = re.compile(
    r"-----BEGIN ((?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY(?: BLOCK)?)-----"
)
# Support literal quotes and one layer of backslash-escaped quote delimiters.
# Quoted values stop at their delimiter or the physical line ending. Malformed
# unclosed quotes mask to line end; no decoding or reserialization is performed.
_AUTH_VALUE = (
    r"(?P<value>\\(?P<escaped_quote>[\"'])"
    r"(?:(?!\\(?P=escaped_quote))[^\r\n])*(?:\\(?P=escaped_quote))?"
    r"|\"(?:\\[^\r\n]|[^\"\\\r\n])*\"?"
    r"|'(?:\\[^\r\n]|[^'\\\r\n])*'?"
    r"|\[REDACTED:[A-Z_]+\]"
    r"|(?:[^\s\"',;\\}\]]|\\(?![\"']))+)"
)
_BEARER = re.compile(r"\bBearer[ \t]+" + _AUTH_VALUE, re.IGNORECASE)
_BASIC_AUTH = re.compile(
    r"(?<![\w-])(?P<header_quote>\\?[\"'])?(?:Proxy-)?Authorization"
    r"(?(header_quote)(?P=header_quote))[ \t]*[:=][ \t]*(?:\\?[\"'])?Basic[ \t]+"
    + _AUTH_VALUE,
    re.IGNORECASE,
)
_URL_CREDENTIALS = re.compile(
    r"(?P<prefix>(?:https?|git\+https?|ssh|ftp|postgres(?:ql)?|mysql|redis|mongodb(?:\+srv)?)://)"
    r"(?P<value>[^\s/?#]+)@",
    re.IGNORECASE,
)
_GITHUB_TOKEN = re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_.-]{16,}")
_AWS_KEY = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_-])(?P<prefix>[\"']?(?P<key>[A-Za-z_][A-Za-z0-9_-]{0,127})"
    r"[\"']?[ \t]*[:=][ \t]*)"
)
_PLACEHOLDER = re.compile(r"\[REDACTED:[A-Z_]+\]")
_ENV_REFERENCE = re.compile(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)")


class SanitizationError(ValueError):
    """Sanitization failed; messages contain no source values."""


@dataclass(frozen=True)
class RedactionResult:
    text: str = field(repr=False)
    counts: dict[RedactionCategory, int]


def _already_masked(value: str) -> bool:
    return _PLACEHOLDER.fullmatch(value) is not None or value in {"***", "<redacted>"}


def _category(key: str) -> RedactionCategory | None:
    key = key.lower().replace("-", "_")
    parts = set(key.split("_"))
    if key.endswith(("password", "passwd", "pwd")) or parts & {"password", "passwd", "pwd"}:
        return "PASSWORD"
    if key.endswith(("api_key", "apikey")) or {"api", "key"} <= parts:
        return "API_KEY"
    if "secret" in parts or key.endswith(
        (
            "token",
            "secret",
            "secret_key",
            "credentials",
            "access_key_id",
            "access_key",
            "private_key",
        )
    ):
        return "SECRET"
    if key in {"sig", "signature"}:
        return "SECRET"
    return None


class SecretRedactor:
    """Apply fixed patterns without executing, interpreting, or logging source text.

    LF and CR characters are preserved, including inside private-key blocks. YAML
    secret block scalars are masked by indentation. Other multiline syntax and
    unknown/encoded/obfuscated credentials are not comprehensively understood.
    """

    def redact(self, text: str) -> RedactionResult:
        if not isinstance(text, str) or len(text) > MAX_REDACTION_CHARS:
            raise SanitizationError("Source exceeds sanitization limits or has an invalid type.")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in text):
            raise SanitizationError("Unsupported Unicode in source; preparation refused.")
        counts: Counter[RedactionCategory] = Counter()
        text = self._private_keys(text, counts)

        def mask_value(
            match: re.Match[str], category: RedactionCategory, *, quoted: bool = False
        ) -> str:
            value = match.group("value")
            start, end = match.span("value")
            if quoted:
                delimiter = ""
                if value.startswith(('\\"', "\\'")):
                    delimiter = value[:2]
                elif value[0] in "\"'":
                    delimiter = value[:1]
                if delimiter:
                    start += len(delimiter)
                    if len(value) >= 2 * len(delimiter) and value.endswith(delimiter):
                        end -= len(delimiter)
                    value = match.string[start:end]
            if not value or _already_masked(value):
                return match.group()
            counts[category] += 1
            return (
                match.group()[: start - match.start()]
                + f"[REDACTED:{category}]"
                + match.group()[end - match.start() :]
            )

        text = _BEARER.sub(lambda match: mask_value(match, "BEARER_TOKEN", quoted=True), text)
        text = _BASIC_AUTH.sub(lambda match: mask_value(match, "BASIC_AUTH", quoted=True), text)
        text = _URL_CREDENTIALS.sub(lambda match: mask_value(match, "URL_CREDENTIALS"), text)
        for pattern, category in [(_GITHUB_TOKEN, "GITHUB_TOKEN"), (_AWS_KEY, "AWS_ACCESS_KEY")]:

            def mask_token(match: re.Match[str], category: RedactionCategory = category) -> str:
                counts[category] += 1
                return f"[REDACTED:{category}]"

            text = pattern.sub(mask_token, text)

        lines = text.split("\n")
        block: tuple[int, RedactionCategory] | None = None
        output: list[str] = []
        for line in lines:
            indent = len(line) - len(line.lstrip(" \t"))
            if block is not None and (not line.strip() or indent > block[0]):
                if line.strip():
                    cr = "\r" if line.endswith("\r") else ""
                    line = line[:indent] + f"[REDACTED:{block[1]}]" + cr
                output.append(line)
                continue
            block = None
            line, new_block = self._assignments(line, counts)
            if new_block is not None:
                block = (indent, new_block)
            output.append(line)
        sanitized = "\n".join(output)
        if len(sanitized) > MAX_REDACTION_CHARS:
            raise SanitizationError(
                "Sanitized source exceeds processing limits; preparation refused."
            )
        return RedactionResult(text=sanitized, counts=dict(sorted(counts.items())))

    @staticmethod
    def _private_keys(text: str, counts: Counter[RedactionCategory]) -> str:
        output: list[str] = []
        cursor = 0
        while match := _PEM_BEGIN.search(text, cursor):
            closing = f"-----END {match.group(1)}-----"
            end = text.find(closing, match.end())
            end = len(text) if end == -1 else end + len(closing)
            output.append(text[cursor : match.start()])
            output.append(re.sub(r"[^\r\n]+", "[REDACTED:PRIVATE_KEY]", text[match.start() : end]))
            counts["PRIVATE_KEY"] += 1
            cursor = end
        output.append(text[cursor:])
        return "".join(output)

    @staticmethod
    def _assignments(
        line: str, counts: Counter[RedactionCategory]
    ) -> tuple[str, RedactionCategory | None]:
        output: list[str] = []
        cursor = 0
        block_category: RedactionCategory | None = None
        while match := _ASSIGNMENT.search(line, cursor):
            category = _category(match.group("key"))
            start = match.end()
            if category is None or start >= len(line):
                output.append(line[cursor:start])
                cursor = start
                continue
            end = start
            quote = line[start] if line[start] in "\"'" else None
            if quote is not None:
                end = start + 1
                while end < len(line) and line[end] != "\r":
                    if line[end] == "\\":
                        end += 2
                        continue
                    if line[end] == quote:
                        if quote == "'" and end + 1 < len(line) and line[end + 1] == "'":
                            end += 2
                            continue
                        break
                    end += 1
                value_start = start + 1
                value_end = min(end, len(line))
                end = end + 1 if end < len(line) and line[end] == quote else min(end, len(line))
            elif line.startswith("${{", start):
                closing = line.find("}}", start + 3)
                end = len(line.rstrip("\r")) if closing == -1 else closing + 2
                value_start, value_end = start, end
            elif line.startswith("[REDACTED:", start):
                closing = line.find("]", start)
                end = len(line.rstrip("\r")) if closing == -1 else closing + 1
                value_start, value_end = start, end
            elif match.group("prefix").rstrip().endswith(":"):
                # YAML plain scalars may contain spaces; retain a trailing comment.
                while end < len(line) and line[end] not in "\r,;&":
                    if line[end] == "#" and end > start and line[end - 1].isspace():
                        break
                    end += 1
                value_start, value_end = start, end
                while value_end > value_start and line[value_end - 1] in " \t":
                    value_end -= 1
            else:
                while end < len(line) and line[end] not in " \t\r,;&":
                    if line[end] == "\\" and end + 1 < len(line):
                        end += 2
                        continue
                    end += 1
                value_start, value_end = start, end
            value = line[value_start:value_end]
            output.append(line[cursor:value_start])
            if value in {"|", "|-", "|+", ">", ">-", ">+"}:
                block_category = category
                counts[category] += 1
                output.append(value)
            elif (
                not value
                or _already_masked(value)
                or _ENV_REFERENCE.fullmatch(value)
                or (value.startswith("${{") and value.endswith("}}"))
            ):
                output.append(value)
            else:
                counts[category] += 1
                output.append(f"[REDACTED:{category}]")
            output.append(line[value_end:end])
            cursor = end
        output.append(line[cursor:])
        return "".join(output), block_category
