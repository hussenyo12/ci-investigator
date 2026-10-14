import pytest

from ci_investigator import WorkflowRunURLParseError, parse_workflow_run_url


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/project/actions/runs/42",
        "https://github.com/example/project/actions/runs/42/",
        "https://github.com/example/project/actions/runs/42?check_suite_focus=true",
        "https://github.com/example/project/actions/runs/42/?attempt=2&foo=bar",
        "https://github.com/example/project/actions/runs/42?next=https%3A%2F%2Fexample.com",
        "https://GITHUB.COM/example/project/actions/runs/42",
        "https://github.com/example/project/actions/runs/00042",
    ],
)
def test_valid_urls_are_canonicalized(url: str) -> None:
    reference = parse_workflow_run_url(url)
    assert reference.owner == "example"
    assert reference.repo == "project"
    assert reference.run_id == 42
    assert reference.url == "https://github.com/example/project/actions/runs/42"


def test_valid_owner_and_repository_characters() -> None:
    reference = parse_workflow_run_url("https://github.com/Team-123/repo_name.v2-1/actions/runs/7")
    assert reference.owner == "Team-123"
    assert reference.repo == "repo_name.v2-1"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a URL",
        "http://github.com/example/project/actions/runs/42",
        "ftp://github.com/example/project/actions/runs/42",
        "//github.com/example/project/actions/runs/42",
        "https:github.com/example/project/actions/runs/42",
        "https://github.com.evil.test/example/project/actions/runs/42",
        "https://evilgithub.com/example/project/actions/runs/42",
        "https://api.github.com/example/project/actions/runs/42",
        "https://github.com./example/project/actions/runs/42",
        "https://gіthub.com/example/project/actions/runs/42",  # Cyrillic i.
        "https://github%2Ecom/example/project/actions/runs/42",
        "https://user@github.com/example/project/actions/runs/42",
        "https://user:password@github.com/example/project/actions/runs/42",
        "https://github.com@evil.test/example/project/actions/runs/42",
        "https://evil.test@github.com/example/project/actions/runs/42",
        "https://github.com:443/example/project/actions/runs/42",
        "https://github.com:80/example/project/actions/runs/42",
        "https://github.com:/example/project/actions/runs/42",
        "https://github.com:invalid/example/project/actions/runs/42",
        "https://[github.com/example/project/actions/runs/42",
        "https://github.com/example/project/actions/runs/0",
        "https://github.com/example/project/actions/runs/000",
        "https://github.com/example/project/actions/runs/-1",
        "https://github.com/example/project/actions/runs/+1",
        "https://github.com/example/project/actions/runs/1.2",
        "https://github.com/example/project/actions/runs/abc",
        "https://github.com/example/project/actions/runs/٤٢",
        "https://github.com/example/project/actions/runs/42/jobs/7",
        "https://github.com/example/project/actions/run/42",
        "https://github.com/example/project/Actions/runs/42",
        "https://github.com/example/project/actions/runs/",
        "https://github.com/example/project/actions/runs/42//",
        "https://github.com//project/actions/runs/42",
        "https://github.com/example//actions/runs/42",
        "https://github.com/example/../actions/runs/42",
        "https://github.com/example/./actions/runs/42",
        "https://github.com/exam_ple/project/actions/runs/42",
        "https://github.com/-example/project/actions/runs/42",
        "https://github.com/example-/project/actions/runs/42",
        "https://github.com/example/repo%2Fname/actions/runs/42",
        "https://github.com/example/project/actions/runs/%34%32",
        "https://github.com/example/project/actions/runs/42#logs",
        "https://github.com/example/project/actions/runs/42#",
        " https://github.com/example/project/actions/runs/42",
        "https://github.com/example/project/actions/runs/42 ",
        "https://git\nhub.com/example/project/actions/runs/42",
        "https://github.com/example/project/actions/runs/42\t",
        "https://github.com/example/project/actions/runs/42?x=\x00",
        "https://github.com/example/project/actions/runs/42\x7f",
        "https://github.com/example/project/actions/runs/42?x=\u00a0",
        "https://github.com\\@evil.test/example/project/actions/runs/42",
        "https://github.com/example\\project/actions/runs/42",
        f"https://github.com/{'a' * 40}/project/actions/runs/42",
        f"https://github.com/example/{'a' * 101}/actions/runs/42",
        f"https://github.com/example/project/actions/runs/{'9' * 5000}",
    ],
)
def test_invalid_urls_raise_dedicated_error(url: str) -> None:
    with pytest.raises(WorkflowRunURLParseError) as caught:
        parse_workflow_run_url(url)
    assert str(caught.value)


@pytest.mark.parametrize("value", [None, 42, b"https://github.com/example/project/actions/runs/42"])
def test_non_string_inputs_are_rejected(value: object) -> None:
    with pytest.raises(WorkflowRunURLParseError, match="nonempty string"):
        parse_workflow_run_url(value)  # type: ignore[arg-type]
