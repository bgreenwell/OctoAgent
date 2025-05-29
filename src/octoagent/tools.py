import re
from typing import Any, Dict, Optional, Tuple
from agents import function_tool
from .github_client import GitHubClient

def parse_github_issue_url(issue_url: str) -> Optional[Tuple[str, str, int]]:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", issue_url)
    if match:
        owner, repo, issue_number_str = match.groups()
        return owner, repo, int(issue_number_str)
    return None

github_client = GitHubClient()

@function_tool
async def download_github_issue(issue_url: str) -> Dict[str, Any]:
    parsed_url = parse_github_issue_url(issue_url)
    if not parsed_url: return {"error": "Invalid GitHub issue URL format."}
    owner, repo, issue_number = parsed_url
    print(f"Tool (agents.py): Fetching issue details for {owner}/{repo}#{issue_number}...")
    return await github_client.get_issue_details(owner, repo, issue_number)

@function_tool
async def create_pr_branch(repo_owner: str, repo_name: str, issue_number: int, branch_prefix: str = "fix", base_branch: str = "main") -> Dict[str, Any]:
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
    print(f"Tool (agents.py): Attempting to commit to {repo_owner}/{repo_name}, branch '{branch_name}', file '{file_path}'")
    if not github_client.token: return {"error": "GITHUB_TOKEN is required."}
    return await github_client.create_commit_on_branch(repo_owner, repo_name, branch_name, commit_message, file_path, file_content)

@function_tool
async def post_comment_to_github(issue_url: str, comment_body: str) -> Dict[str, Any]:
    if not github_client.token: return {"error": "GITHUB_TOKEN not set."}
    parsed_url = parse_github_issue_url(issue_url)
    if not parsed_url: return {"error": "Invalid GitHub issue URL format."}
    owner, repo, issue_number = parsed_url
    print(f"Tool (agents.py): Posting comment to {owner}/{repo}#{issue_number}...")
    result = await github_client.add_comment_to_issue(owner, repo, issue_number, comment_body)
    if "error" in result: return result
    return {"message": "Comment posted successfully.", "details": result}

def extract_code_from_markdown(markdown_text: Optional[str]) -> Optional[str]:
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
