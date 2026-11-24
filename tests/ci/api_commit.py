"""Commit file changes through GitHub GraphQL so GitHub signs them.

The repo ruleset requires signed commits on every branch; commits made by
`git commit` in a workflow are unsigned, `createCommitOnBranch` ones are not.
"""

import base64
import json
import os
import subprocess
from pathlib import Path

MUTATION = """
mutation($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) { commit { oid } }
}
"""

HEAD_MOVED = "Expected branch to point to"


class GraphQLError(RuntimeError):
    """GraphQL returned `errors` (or gh failed)."""


def gh_graphql(query: str, variables: dict) -> dict:
    """Run a GraphQL call with `gh api graphql`, authenticated by GH_TOKEN."""
    proc = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps({"query": query, "variables": variables}),
        capture_output=True, text=True, env=os.environ.copy(),
    )
    try:
        body = json.loads(proc.stdout)
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("errors"):
        raise GraphQLError("; ".join(str(e.get("message", e)) for e in body["errors"]))
    if proc.returncode != 0 or not isinstance(body, dict):
        raise GraphQLError((proc.stderr or proc.stdout).strip() or f"gh exited {proc.returncode}")
    return body


def commit_files(repo, branch, expected_head, message, changes, runner=gh_graphql):
    """Commit `changes` (path -> bytes to write, None to delete) onto `branch`.

    Returns the new commit oid, or None when the branch head is no longer
    `expected_head` (someone pushed meanwhile: the caller skips this PR).
    """
    headline, _, body = message.partition("\n")
    msg = {"headline": headline}
    if body.strip():
        msg["body"] = body.strip()
    file_changes = {
        "additions": [{"path": p, "contents": base64.b64encode(c).decode()}
                      for p, c in sorted(changes.items()) if c is not None],
        "deletions": [{"path": p} for p, c in sorted(changes.items()) if c is None],
    }
    variables = {"input": {
        "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
        "expectedHeadOid": expected_head,
        "message": msg,
        "fileChanges": file_changes,
    }}
    try:
        result = runner(MUTATION, variables)
    except GraphQLError as exc:
        if HEAD_MOVED in str(exc):
            return None
        raise
    return result["data"]["createCommitOnBranch"]["commit"]["oid"]


def changed_files(worktree: Path) -> dict:
    """Working-tree changes as path -> new bytes (None when deleted)."""
    out = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "-z", "--untracked-files=all"],
        check=True, capture_output=True,
    ).stdout.split(b"\0")
    changes = {}
    i = 0
    while i < len(out):
        entry = out[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2].decode(), entry[3:].decode()
        if "R" in status or "C" in status:
            old = out[i].decode()
            i += 1
            if "R" in status:
                changes[old] = None
        if "D" in status and "R" not in status:
            changes[path] = None
        else:
            changes[path] = (Path(worktree) / path).read_bytes()
    return changes
