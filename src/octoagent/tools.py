"""
Agent tools and utility functions for interacting with GitHub.

This module defines the functions that agents can call to perform actions,
such as downloading issues, creating branches, and listing files. It also
contains helper utilities for parsing data.
"""
import re

from typing import Any, Dict, Optional, Tuple
from agents import function_tool
from .github_client import GitHubClient


def parse_github_issue_url(issue_url: str) -> Optional[Tuple[str, str, int]]:
    """
    Parses a GitHub issue URL to extract owner, repo, and issue number.

    Parameters
    ----------
    issue_url : str
        The full URL of the GitHub issue.
        e.g., "https://github.com/owner/repo/issues/123"

    Returns
    -------
    tuple of (str, str, int) or None
        A tuple containing the owner, repository name, and issue number,
        or None if the URL format is invalid.
    """
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url)
    if match:
        owner, repo, issue_number_str = match.groups()
        return owner, repo, int(issue_number_str)
    return None

github_client = GitHubClient()

@function_tool
async def list_repository_files(repo_owner: str, repo_name: str, branch: str = "main") -> Dict[str, Any]:
    """
    Lists all file paths in a given repository and branch.

    Useful for understanding the repository structure before identifying a file to modify.

    Parameters
    ----------
    repo_owner : str
        The owner of the repository.
    repo_name : str
        The name of the repository.
    branch : str
        The branch to list files from.

    Returns
    -------
    dict
        A dictionary containing a list of file paths or an error message.
    """
    print(f"Tool (tools.py): Listing files for {repo_owner}/{repo_name} on branch {branch}...")
    return await github_client.list_files_in_repo(repo_owner, repo_name, branch)

@function_tool
async def download_github_issue(issue_url: str) -> Dict[str, Any]:
    """
    Fetches the details of a GitHub issue from its URL.

    Parameters
    ----------
    issue_url : str
        The full URL of the GitHub issue to download.

    Returns
    -------
    dict
        A dictionary containing the issue details or an error message.
    """
    parsed_url = parse_github_issue_url(issue_url)
    if not parsed_url: return {"error": "Invalid GitHub issue URL format."}
    owner, repo, issue_number = parsed_url
    print(f"Tool (agents.py): Fetching issue details for {owner}/{repo}#{issue_number}...")
    return await github_client.get_issue_details(owner, repo, issue_number)

@function_tool
async def create_pr_branch(repo_owner: str, repo_name: str, issue_number: int, branch_prefix: str = "fix", base_branch: str = "main") -> Dict[str, Any]:
    """
    Ensures a branch exists for a pull request, creating it if necessary.

    Parameters
    ----------
    repo_owner : str
        The owner of the repository.
    repo_name : str
        The name of the repository.
    issue_number : int
        The issue number to associate with the branch.
    base_branch : str
        The name of the base branch to branch from.
    branch_prefix : str, optional
        The prefix for the new branch name (e.g., 'fix', 'feature'),
        by default "fix".

    Returns
    -------
    dict
        A dictionary containing the branch creation status or an error message.
    """
    if not github_client.token: return {"error": "GITHUB_TOKEN not set."}
    new_branch_name = f"{branch_prefix}/issue-{issue_number}"
    print(f"Tool (agents.py): Creating/Ensuring branch '{new_branch_name}' in {repo_owner}/{repo_name} from {base_branch}...")
    result = await github_client.create_branch(repo_owner, repo_name, new_branch_name, base_branch)
    
    if "error" in result:
        return result
    elif result.get("already_exists"):
        return {"message": f"Branch '{new_branch_name}' already exists.", "branch_name": new_branch_name, "status": "already_exists", "details": result}
    elif result.get("ref"):
        return {"message": f"Branch '{new_branch_name}' created successfully.", "branch_name": new_branch_name, "status": "created", "details": result}
    else:
        return {"error": "Branch creation status unclear from API response.", "details": result}


@function_tool
async def commit_code_to_branch(repo_owner: str, repo_name: str, branch_name: str, commit_message: str, file_path: str, file_content: str) -> Dict[str, Any]:
    """
    Commits a file (new or updated) to a specified branch.

    Parameters
    ----------
    repo_owner : str
        The owner of the repository.
    repo_name : str
        The name of the repository.
    branch_name : str
        The name of the branch to commit to.
    commit_message : str
        The message for the commit.
    file_path : str
        The path of the file to be committed.
    file_content : str
        The full content of the file.

    Returns
    -------
    dict
        A dictionary containing the commit details or an error message.
    """
    print(f"Tool (agents.py): Attempting to commit to {repo_owner}/{repo_name}, branch '{branch_name}', file '{file_path}'")
    if not github_client.token: return {"error": "GITHUB_TOKEN is required."}
    return await github_client.create_commit_on_branch(repo_owner, repo_name, branch_name, commit_message, file_path, file_content)

@function_tool
async def post_comment_to_github(issue_url: str, comment_body: str) -> Dict[str, Any]:
    """
    Posts a comment to a specified GitHub issue.

    Parameters
    ----------
    issue_url : str
        The full URL of the GitHub issue to comment on.
    comment_body : str
        The markdown content of the comment.

    Returns
    -------
    dict
        A dictionary containing the new comment details or an error message.
    """
    if not github_client.token: return {"error": "GITHUB_TOKEN not set."}
    parsed_url = parse_github_issue_url(issue_url)
    if not parsed_url: return {"error": "Invalid GitHub issue URL format."}
    owner, repo, issue_number = parsed_url
    print(f"Tool (agents.py): Posting comment to {owner}/{repo}#{issue_number}...")
    result = await github_client.add_comment_to_issue(owner, repo, issue_number, comment_body)
    if "error" in result: return result
    return {"message": "Comment posted successfully.", "details": result}

def extract_code_from_markdown(markdown_text: Optional[str]) -> Optional[str]:
    """
    Extracts a code block from a markdown string.

    Searches for a code block enclosed in triple backticks. If not found, it
    performs a heuristic check for common code keywords.

    Parameters
    ----------
    markdown_text : str or None
        The markdown text to parse.

    Returns
    -------
    str or None
        The extracted code as a string, or None if no code block is found.
    """
    if not markdown_text:
        return None
    match = re.search(r"```(?:[a-zA-Z0-9\+\-\#\.]*?)?\s*\n(.*?)\n```", markdown_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    stripped_text = markdown_text.strip()
    if not stripped_text.startswith("```") and \
       any(kw in stripped_text for kw in ["library(", "function(", "<-", "#'", "@param", "@return", "@examples", "if (", "else {", "for (", "while (", "def ", "class "]):
        return stripped_text
    return None
