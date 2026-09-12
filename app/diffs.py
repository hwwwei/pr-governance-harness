"""Unified-diff parsing shared by agents, patch generation and validation."""

from dataclasses import dataclass
from pathlib import PurePosixPath

from unidiff import PatchSet


class DiffParseError(ValueError):
    pass


@dataclass(frozen=True)
class AddedLine:
    file: str
    line: int
    text: str


def parse_diff(diff: str) -> PatchSet:
    if not diff.strip():
        raise DiffParseError("diff is empty")
    try:
        parsed = PatchSet(diff)
    except Exception as exc:  # unidiff exposes several parser exception types
        raise DiffParseError(f"invalid unified diff: {exc}") from exc
    if not parsed:
        raise DiffParseError("diff contains no file patches")
    if any(len(file_patch) == 0 for file_patch in parsed):
        raise DiffParseError("diff contains a file patch without a hunk")
    return parsed


def changed_files(diff: str) -> list[str]:
    return sorted({file_path.path for file_path in parse_diff(diff) if file_path.path and file_path.path != "/dev/null"})


def added_lines(diff: str) -> list[AddedLine]:
    result: list[AddedLine] = []
    for file_patch in parse_diff(diff):
        path = file_patch.path
        if not path or path == "/dev/null":
            continue
        target_line = 0
        for hunk in file_patch:
            target_line = hunk.target_start
            for line in hunk:
                if line.is_added:
                    result.append(AddedLine(path, target_line, line.value.rstrip("\n")))
                    target_line += 1
                elif line.is_context:
                    target_line += 1
                # Removed lines do not advance the post-change line number.
    return result


def safe_relative_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def validate_candidate_patch(patch: str, allowed_files: set[str]) -> dict[str, object]:
    if not patch:
        return {
            "parseable": True,
            "scope_allowed": True,
            "safe": True,
            "tests_not_executed": True,
            "files": [],
            "errors": [],
        }
    errors: list[str] = []
    try:
        parsed = parse_diff(patch)
    except DiffParseError as exc:
        return {
            "parseable": False,
            "scope_allowed": False,
            "safe": False,
            "tests_not_executed": True,
            "files": [],
            "errors": [str(exc)],
        }
    files = [item.path for item in parsed if item.path]
    if any(not safe_relative_path(path) for path in files):
        errors.append("path traversal or absolute path")
    if any(path not in allowed_files for path in files):
        errors.append("patch modifies a file outside the PR scope")
    additions = "\n".join(line.value for item in parsed for hunk in item for line in hunk if line.is_added)
    forbidden = ("git push", "subprocess.Popen", "os.system", "rm -rf", "BEGIN PRIVATE KEY")
    hits = [token for token in forbidden if token.lower() in additions.lower()]
    if hits:
        errors.append(f"dangerous content: {', '.join(hits)}")
    return {
        "parseable": True,
        "scope_allowed": not any("scope" in error or "path" in error for error in errors),
        "safe": not hits and not any("path" in error for error in errors),
        "tests_not_executed": True,
        "files": files,
        "errors": errors,
    }
