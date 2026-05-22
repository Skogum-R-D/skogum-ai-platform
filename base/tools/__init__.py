"""GitHub tools: schemas and function registry for the Mistral tool loop."""

from .github_tools import (
    build_and_test,
    github_add_secret,
    github_create_branch,
    github_create_issue,
    github_create_issue_comment,
    github_create_or_update_file,
    github_create_pr,
    github_create_repo,
    github_get_failed_job_logs,
    github_get_file,
    github_get_issue,
    github_list_issues,
    github_list_repos,
    github_list_workflow_runs,
    github_merge_pr,
)

# Inner function schema dicts — passed to BaseAgent.register_tool().
# Do NOT include the outer {"type": "function"} wrapper; register_tool() adds it.
GITHUB_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "build_and_test",
        "description": (
            "Clone a GitHub repo branch and run `npm run build` to verify the code compiles. "
            "Returns success=True if the build passes, or success=False with the compiler error output. "
            "ALWAYS call this after editing source files and before pushing or reporting task_complete. "
            "Do not push broken code — iterate until build_and_test returns success=True."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "Branch to build (default: main)"},
                "org": {"type": "string", "description": "GitHub org name"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "github_create_repo",
        "description": "Create a GitHub repository in an organization or for the authenticated user.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repository name"},
                "description": {"type": "string", "description": "Short repository description"},
                "private": {"type": "boolean", "description": "Whether the repo is private (default true)"},
                "org": {"type": "string", "description": "GitHub org name; omit to create under authenticated user"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "github_list_repos",
        "description": "List repositories in a GitHub organization or for the authenticated user.",
        "parameters": {
            "type": "object",
            "properties": {
                "org": {"type": "string", "description": "GitHub org name; omit for authenticated user's repos"},
            },
            "required": [],
        },
    },
    {
        "name": "github_create_branch",
        "description": "Create a new branch in a GitHub repository from an existing branch.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "New branch name to create"},
                "from_branch": {"type": "string", "description": "Source branch to branch from (default: main)"},
                "org": {"type": "string", "description": "GitHub org name"},
            },
            "required": ["repo", "branch"],
        },
    },
    {
        "name": "github_create_or_update_file",
        "description": (
            "Create or update a single file in a GitHub repository. "
            "Content is plain text; base64 encoding is handled automatically. "
            "Use this to push code, configs, and workflow files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "File path within the repo, e.g. '.github/workflows/ci.yml'"},
                "content": {"type": "string", "description": "Full plain-text file content"},
                "commit_message": {"type": "string", "description": "Git commit message"},
                "branch": {"type": "string", "description": "Target branch (default: main)"},
                "org": {"type": "string", "description": "GitHub org name"},
            },
            "required": ["repo", "path", "content", "commit_message"],
        },
    },
    {
        "name": "github_get_file",
        "description": "Read the decoded text content of a file from a GitHub repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string", "description": "File path within the repo"},
                "branch": {"type": "string", "description": "Branch to read from (default: main)"},
                "org": {"type": "string"},
            },
            "required": ["repo", "path"],
        },
    },
    {
        "name": "github_create_pr",
        "description": "Create a pull request from a feature branch into a base branch. Set closes_issue to auto-link and close an issue when the PR is merged.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "PR description in Markdown"},
                "head_branch": {"type": "string", "description": "Branch that contains the changes"},
                "base_branch": {"type": "string", "description": "Branch to merge into (default: main)"},
                "org": {"type": "string"},
                "closes_issue": {"type": "integer", "description": "Issue number this PR fixes — appends 'Closes #N' to link and auto-close on merge"},
            },
            "required": ["repo", "title", "body", "head_branch"],
        },
    },
    {
        "name": "github_merge_pr",
        "description": "Merge a pull request using squash merge.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "pr_number": {"type": "integer", "description": "Pull request number"},
                "org": {"type": "string"},
            },
            "required": ["repo", "pr_number"],
        },
    },
    {
        "name": "github_add_secret",
        "description": "Add or update a GitHub Actions secret in a repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "secret_name": {"type": "string", "description": "Secret name in UPPER_SNAKE_CASE"},
                "secret_value": {"type": "string", "description": "Plain-text secret value to encrypt and store"},
                "org": {"type": "string"},
            },
            "required": ["repo", "secret_name", "secret_value"],
        },
    },
    {
        "name": "github_create_issue",
        "description": "Create a GitHub issue to report a bug or track a task.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "Issue description in Markdown"},
                "org": {"type": "string"},
            },
            "required": ["repo", "title"],
        },
    },
    {
        "name": "github_list_issues",
        "description": "List issues in a GitHub repository (pull requests are excluded).",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "org": {"type": "string"},
                "state": {"type": "string", "description": "open, closed, or all (default: open)"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "github_get_issue",
        "description": "Get the full details of a specific GitHub issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
                "org": {"type": "string"},
            },
            "required": ["repo", "issue_number"],
        },
    },
    {
        "name": "github_create_issue_comment",
        "description": "Post a comment on a GitHub issue.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
                "body": {"type": "string", "description": "Comment text in Markdown"},
                "org": {"type": "string"},
            },
            "required": ["repo", "issue_number", "body"],
        },
    },
]

# name → callable, used with register_tool()
GITHUB_TOOL_FNS: dict[str, callable] = {
    "build_and_test": build_and_test,
    "github_create_repo": github_create_repo,
    "github_list_repos": github_list_repos,
    "github_create_branch": github_create_branch,
    "github_create_or_update_file": github_create_or_update_file,
    "github_get_file": github_get_file,
    "github_create_pr": github_create_pr,
    "github_merge_pr": github_merge_pr,
    "github_add_secret": github_add_secret,
    "github_create_issue": github_create_issue,
    "github_list_issues": github_list_issues,
    "github_get_issue": github_get_issue,
    "github_create_issue_comment": github_create_issue_comment,
    "github_list_workflow_runs": github_list_workflow_runs,
    "github_get_failed_job_logs": github_get_failed_job_logs,
}
