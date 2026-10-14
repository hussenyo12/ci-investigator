"""Parse public GitHub workflow run URLs without performing network requests."""

import re
from urllib.parse import urlsplit

from ci_investigator.schemas import WorkflowRunReference

_RUN_PATH = re.compile(r"/([^/]+)/([^/]+)/actions/runs/([0-9]+)/?")


class WorkflowRunURLParseError(ValueError):
    """The input is not an accepted public GitHub Actions workflow run URL."""


def parse_workflow_run_url(url: str) -> WorkflowRunReference:
    """Accept query strings and a trailing slash, and return a canonical run identity.

    Ports, credentials, fragments, encoded path components, whitespace, and control
    characters are rejected to avoid ambiguous inputs. DNS and run existence are
    deliberately not checked.
    """
    if not isinstance(url, str) or not url:
        raise WorkflowRunURLParseError("workflow run URL must be a nonempty string")
    if any(
        character.isspace() or ord(character) <= 32 or ord(character) == 127 for character in url
    ):
        raise WorkflowRunURLParseError("workflow run URL must not contain whitespace or controls")
    try:
        parts = urlsplit(url)
    except ValueError as error:
        raise WorkflowRunURLParseError("malformed workflow run URL") from error

    if parts.scheme != "https":
        raise WorkflowRunURLParseError("workflow run URL must use HTTPS")
    # Comparing the entire authority also excludes every port and embedded credential.
    if parts.netloc.lower() != "github.com":
        raise WorkflowRunURLParseError(
            "workflow run URL must use exactly github.com without credentials or ports"
        )
    if "#" in url:
        raise WorkflowRunURLParseError("workflow run URL must not contain a fragment")
    match = _RUN_PATH.fullmatch(parts.path)
    if match is None:
        raise WorkflowRunURLParseError("expected /OWNER/REPO/actions/runs/RUN_ID")

    owner, repo, run_id_text = match.groups()
    try:
        run_id = int(run_id_text)
        return WorkflowRunReference(
            owner=owner,
            repo=repo,
            run_id=run_id,
            url=f"https://github.com/{owner}/{repo}/actions/runs/{run_id}",
        )
    except ValueError as error:
        raise WorkflowRunURLParseError(f"invalid workflow run identity: {error}") from error
