import json

import pytest

from ci_investigator.redactor import MAX_REDACTION_CHARS, SanitizationError, SecretRedactor


@pytest.mark.parametrize(
    ("text", "secret", "category"),
    [
        ("Invalid ghp_" + "A" * 36, "ghp_" + "A" * 36, "GITHUB_TOKEN"),
        (
            "Invalid github_pat_" + "B" * 22 + "_" + "C" * 59,
            "github_pat_" + "B" * 22 + "_" + "C" * 59,
            "GITHUB_TOKEN",
        ),
        (
            "ghs_12345_" + "eyJtest" * 10 + ".payload.signature",
            "ghs_12345_" + "eyJtest" * 10 + ".payload.signature",
            "GITHUB_TOKEN",
        ),
        ("Authorization: Bearer fake-bearer.value", "fake-bearer.value", "BEARER_TOKEN"),
        ("api_key=synthetic-key", "synthetic-key", "API_KEY"),
        ('{"password": "synthetic phrase"}', "synthetic phrase", "PASSWORD"),
        ("CLIENT_SECRET: 'test-client-secret'", "test-client-secret", "SECRET"),
        ("AWS_SECRET_ACCESS_KEY=test-aws-secret", "test-aws-secret", "SECRET"),
        ("https://alice:p%40ssword@example.com/path", "alice:p%40ssword", "URL_CREDENTIALS"),
        ("postgresql://alice:db-password@example.com/db", "alice:db-password", "URL_CREDENTIALS"),
        ("AKIA" + "A" * 16, "AKIA" + "A" * 16, "AWS_ACCESS_KEY"),
        ("ASIA" + "B" * 16, "ASIA" + "B" * 16, "AWS_ACCESS_KEY"),
    ],
)
def test_sensitive_patterns_are_masked(text: str, secret: str, category: str) -> None:
    result = SecretRedactor().redact(text)
    assert secret not in result.text
    assert f"[REDACTED:{category}]" in result.text
    assert result.counts[category] == 1
    assert secret not in repr(result)
    assert SecretRedactor().redact(result.text).text == result.text


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_private_key_blocks_preserve_line_separators(newline: str) -> None:
    text = newline.join(
        [
            "before",
            "-----BEGIN RSA PRIVATE KEY-----",
            "synthetic-secret-body",
            "more-private-material",
            "-----END RSA PRIVATE KEY-----",
            "ModuleNotFoundError: yaml",
            "",
        ]
    )
    result = SecretRedactor().redact(text)
    assert result.text.count("\n") == text.count("\n")
    assert result.text.count("\r\n") == text.count("\r\n")
    assert "synthetic-secret-body" not in result.text and "more-private-material" not in result.text
    assert result.text.split("\n")[-2] == "ModuleNotFoundError: yaml" + (
        "\r" if newline == "\r\n" else ""
    )
    assert result.counts == {"PRIVATE_KEY": 1}


def test_unterminated_private_key_masks_remaining_material() -> None:
    result = SecretRedactor().redact("start\n-----BEGIN PRIVATE KEY-----\nsynthetic-key-material")
    assert "synthetic-key-material" not in result.text
    assert result.text.count("\n") == 2


def test_yaml_secret_blocks_and_multiple_inline_values() -> None:
    text = (
        "env:\r\n  API_KEY: |-\r\n    synthetic-line-one\r\n"
        "    synthetic-line-two\r\n  LOG_LEVEL: info\r\n"
    )
    result = SecretRedactor().redact(text)
    assert "synthetic-line" not in result.text
    assert result.text.count("\r\n") == 5 and "LOG_LEVEL: info" in result.text
    assert SecretRedactor().redact(result.text).text == result.text
    result = SecretRedactor().redact(
        "API_KEY=alpha PASSWORD='beta phrase' other=ok Authorization: Bearer gamma"
    )
    assert (
        "alpha" not in result.text
        and "beta phrase" not in result.text
        and "gamma" not in result.text
    )
    assert "other=ok" in result.text
    assert sum(result.counts.values()) == 3


def test_context_and_secret_references_are_preserved() -> None:
    text = (
        "ModuleNotFoundError: No module named 'yaml'\nkey=cache api_version=v2\n"
        "TOKEN: ${{ secrets.CI_TOKEN }}\nPASSWORD: ${DB_PASSWORD}\npasswordless=true\n"
    )
    result = SecretRedactor().redact(text)
    assert result.text == text and result.counts == {}


def test_escaped_and_unclosed_quoted_assignments() -> None:
    result = SecretRedactor().redact(
        'password="synthetic \\"quote\\" phrase" API_KEY=another\nPASSWORD="unclosed-value\r\n'
    )
    assert (
        "synthetic" not in result.text
        and "another" not in result.text
        and "unclosed-value" not in result.text
    )
    assert result.text.count("\n") == 2 and result.text.endswith("\r\n")


def test_long_lines_and_repeated_processing_are_deterministic() -> None:
    text = "x" * 500_000 + " ModuleNotFoundError: yaml PASSWORD=" + "a" * 100_000
    first = SecretRedactor().redact(text)
    assert first == SecretRedactor().redact(text)
    assert "ModuleNotFoundError: yaml" in first.text
    assert "a" * 1000 not in first.text
    assert first.counts == {"PASSWORD": 1}


@pytest.mark.parametrize(
    "text", ["x" * (MAX_REDACTION_CHARS + 1), "PASSWORD=synthetic-secret\ud800"]
)
def test_redaction_failure_does_not_echo_input(text: str) -> None:
    with pytest.raises(SanitizationError) as caught:
        SecretRedactor().redact(text)
    assert "synthetic-secret" not in str(caught.value)
    assert len(str(caught.value)) < 150


def test_unquoted_yaml_secret_with_spaces_is_fully_masked() -> None:
    result = SecretRedactor().redact(
        "env:\n  PASSWORD: synthetic phrase with spaces # harmless comment\n"
        "AssertionError: preserved\n"
    )
    assert "phrase with spaces" not in result.text
    assert "AssertionError: preserved" in result.text
    assert "harmless comment" in result.text


def test_valid_escaped_json_assignment_is_fully_masked() -> None:
    secret = 'synthetic "quoted" phrase'
    result = SecretRedactor().redact(f"PASSWORD={json.dumps(secret)}")
    assert not any(part in result.text for part in ["synthetic", "quoted", "phrase"])


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    ("header", "scheme", "quote", "secret", "category"),
    [
        ("Authorization", "Bearer", "", "synthetic-bearer-value", "BEARER_TOKEN"),
        ("Authorization", "Bearer", "'", "synthetic-single-value", "BEARER_TOKEN"),
        ("Authorization", "Bearer", '"', "synthetic-double-value", "BEARER_TOKEN"),
        ("aUtHoRiZaTiOn", "bEaReR", '"', "synthetic-mixed-value", "BEARER_TOKEN"),
        ("Proxy-Authorization", "Bearer", "'", "synthetic-proxy-value", "BEARER_TOKEN"),
        ("Authorization", "Basic", "", "dXNlcjpwYXNz", "BASIC_AUTH"),
        ("pRoXy-AuThOrIzAtIoN", "bAsIc", "", "cHJveHk6cGFzcw==", "BASIC_AUTH"),
        ("Authorization", "Basic", "'", "c3ludGhldGljOnBhc3M=", "BASIC_AUTH"),
        ("Authorization", "Basic", '"', "c3ludGhldGljOnNlY3JldA==", "BASIC_AUTH"),
    ],
)
def test_authentication_credentials_are_masked_with_context_and_newlines(
    header: str, scheme: str, quote: str, secret: str, category: str, newline: str
) -> None:
    text = (
        f"setup{newline}AssertionError: rejected {header}: {scheme} "
        f"{quote}{secret}{quote}; expected HTTP 200{newline}failure details{newline}"
    )
    redactor = SecretRedactor()
    result = redactor.redact(text)
    expected = text.replace(secret, f"[REDACTED:{category}]")
    assert result.text == expected
    assert result.counts == {category: 1}
    assert result.text.count("\n") == text.count("\n")
    assert result.text.count("\r\n") == text.count("\r\n")
    assert secret not in repr(result) and secret not in json.dumps(result.counts)
    assert redactor.redact(text) == result
    again = redactor.redact(result.text)
    assert again.text == result.text and again.counts == {}


def test_multiple_authentication_and_assignment_values_on_one_line() -> None:
    text = (
        'AssertionError: Authorization: Bearer "synthetic-bearer"; '
        "Proxy-Authorization: Basic dXNlcjpwYXNz; "
        "API_KEY=synthetic-api PASSWORD='synthetic-password'; expected HTTP 200"
    )
    result = SecretRedactor().redact(text)
    assert result.text == (
        'AssertionError: Authorization: Bearer "[REDACTED:BEARER_TOKEN]"; '
        "Proxy-Authorization: Basic [REDACTED:BASIC_AUTH]; "
        "API_KEY=[REDACTED:API_KEY] PASSWORD='[REDACTED:PASSWORD]'; expected HTTP 200"
    )
    assert result.counts == {"BEARER_TOKEN": 1, "BASIC_AUTH": 1, "API_KEY": 1, "PASSWORD": 1}
    assert SecretRedactor().redact(result.text).text == result.text


@pytest.mark.parametrize("quote", ["'", '"'])
def test_escaped_or_unterminated_quoted_authentication_is_line_bounded(quote: str) -> None:
    credential = f"synthetic\\{quote}escaped-value"
    text = (
        f"AssertionError: Authorization: Bearer {quote}{credential}{quote}; useful detail\r\n"
        f"Proxy-Authorization: Basic {quote}unterminated-synthetic\r\n"
        "AssertionError: next line remains useful\r\n"
    )
    result = SecretRedactor().redact(text)
    assert "synthetic" not in result.text and "escaped-value" not in result.text
    assert "; useful detail\r\n" in result.text
    assert result.text.endswith("AssertionError: next line remains useful\r\n")
    assert result.text.count("\r\n") == 3
    assert result.counts == {"BEARER_TOKEN": 1, "BASIC_AUTH": 1}
    assert SecretRedactor().redact(result.text).text == result.text


def test_basic_prose_is_not_treated_as_an_authorization_header() -> None:
    text = "AssertionError: Basic installation failed; authentication is required"
    result = SecretRedactor().redact(text)
    assert result.text == text and result.counts == {}


def test_authentication_sanitization_error_contains_no_credentials() -> None:
    with pytest.raises(SanitizationError) as caught:
        SecretRedactor().redact('Authorization: Bearer "synthetic-invalid"\ud800')
    assert "synthetic-invalid" not in str(caught.value)
    assert "synthetic-invalid" not in repr(caught.value)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    ("template", "category"),
    [
        ('"Authorization": "Basic {secret}"', "BASIC_AUTH"),
        ('"Proxy-Authorization": "Basic {secret}"', "BASIC_AUTH"),
        ("Authorization=Basic {secret}", "BASIC_AUTH"),
        ('Proxy-Authorization="Basic {secret}"', "BASIC_AUTH"),
        ("'Authorization': 'Basic {secret}'", "BASIC_AUTH"),
        ('"aUtHoRiZaTiOn": "bAsIc {secret}"', "BASIC_AUTH"),
        ('pRoXy-AuThOrIzAtIoN=bAsIc "{secret}"', "BASIC_AUTH"),
        ('"Authorization": "Basic \'{secret}\'"', "BASIC_AUTH"),
        (r'\"Authorization\": \"Basic {secret}\"', "BASIC_AUTH"),
        (r'\"Proxy-Authorization\": \"bAsIc {secret}\"', "BASIC_AUTH"),
        (r'Authorization=\"Basic {secret}\"', "BASIC_AUTH"),
        (r'Authorization=Basic \"{secret}\"', "BASIC_AUTH"),
        ('"Authorization": "Bearer {secret}"', "BEARER_TOKEN"),
        (r'Authorization=Bearer \"{secret}\"', "BEARER_TOKEN"),
    ],
)
def test_serialized_authorization_preserves_context_and_line_boundaries(
    template: str, category: str, newline: str
) -> None:
    secret = "c3ludGhldGljOnNlY3JldA=="
    header = template.format(secret=secret)
    text = f"setup{newline}AssertionError: headers={{{header}}}; expected HTTP 200{newline}"
    redactor = SecretRedactor()
    result = redactor.redact(text)
    assert result.text == text.replace(secret, f"[REDACTED:{category}]")
    assert result.counts == {category: 1}
    assert result.text.count("\n") == text.count("\n")
    assert result.text.count("\r\n") == text.count("\r\n")
    assert secret not in repr(result) and secret not in json.dumps(result.counts)
    assert redactor.redact(text) == result
    repeated = redactor.redact(result.text)
    assert repeated.text == result.text and repeated.counts == {}


def test_serialized_authorization_keeps_json_structure_and_multiple_credentials() -> None:
    secrets = ["c3ludGhldGljOm9uZQ==", "c3ludGhldGljOnR3bw=="]
    headers = {"Authorization": f"Basic {secrets[0]}", "Proxy-Authorization": f"Basic {secrets[1]}"}
    redactor = SecretRedactor()
    for text in [json.dumps(headers), json.dumps(json.dumps(headers))]:
        result = redactor.redact(text)
        expected = text
        for secret in secrets:
            expected = expected.replace(secret, "[REDACTED:BASIC_AUTH]")
        assert result.text == expected
        assert result.counts == {"BASIC_AUTH": 2}
        decoded = json.loads(result.text)
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
        assert decoded == {
            "Authorization": "Basic [REDACTED:BASIC_AUTH]",
            "Proxy-Authorization": "Basic [REDACTED:BASIC_AUTH]",
        }
        assert redactor.redact(result.text).text == result.text


def test_serialized_authorization_does_not_mask_unrelated_keys_or_prose() -> None:
    text = (
        'AssertionError: {"Description": "Basic installation failed", '
        '"X-Authorization": "Basic unrelated-value"}; '
        "AuthorizationError=Basic useful-detail"
    )
    result = SecretRedactor().redact(text)
    assert result.text == text and result.counts == {}
