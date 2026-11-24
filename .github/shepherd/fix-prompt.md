You are repairing a failing Dependabot pull request in a Helm chart repository.

Dependabot bumped the primary image tag of the chart `{{CHART_DIR}}`. The shepherd already bumped the chart `appVersion` and `version` to match. The PR's checks (`helm` validation and the `install` gate) now fail. Your job is to make them pass with the smallest correct change to the chart.

Everything is checked out at `{{WORKTREE}}` (the PR head). Use absolute paths under it. The failing CI logs are in `{{LOGS}}`; read them first.

Allowed changes (anything else is rejected and the whole fix is thrown away):
- files inside `{{CHART_DIR}}/`
- `tests/test_*.py`
- `tests/chart_artifacts.py`

Forbidden:
- anything under `.github/` or `tests/e2e/`, and any other path
- deleting or weakening tests: do not reduce the number of `def test_` functions in any file, and do not add `skip`, `skipIf` or `expectedFailure`
- git and gh commands, committing, pushing, or touching branches. The shepherd commits for you.

Do not change the image tag or `appVersion`; fix what the new version needs (values, templates, probes, env, args, tests that encode the old version's behaviour).

Before finishing, run `python3 -m unittest discover -s {{WORKTREE}}/tests -t {{WORKTREE}}` and, if you touched templates, `helm dependency build {{WORKTREE}}/{{CHART_DIR}}` and `helm template {{WORKTREE}}/{{CHART_DIR}}`. Do not commit. If you cannot fix it within the allowed paths, change nothing and say why.
