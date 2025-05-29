import asyncio
import base64 
import json 
import os
import re 
import requests 

from agents.items import ToolCallItem, ToolCallOutputItem 
from agents import Agent as BaseAgent, Runner, function_tool 
from typing import Any, Dict, List, Optional, Tuple


class ReusableAgent(BaseAgent):
    """
    A reusable base agent class that can be extended for specific use cases.

    This class inherits from the `agents.Agent` class and provides a
    foundation for building more specialized agents. It integrates the
    proxy configuration.

    Parameters
    ----------
    name : str
        The name of the agent.
    instructions : str, optional
        The system prompt or instructions for the agent.
        Can be a string or a callable that returns a string.
    **kwargs : dict
        Additional keyword arguments to pass to the base `agents.Agent` class.

    Attributes
    ----------
    runner : agents.Runner
        An instance of the agent runner.
    """

    def __init__(self, name: str, instructions: str = "You are a helpful assistant.", **kwargs):
        """
        Initialize a ReusableAgent.

        Parameters
        ----------
        name : str
            The name of the agent.
        instructions : str, optional
            The system prompt or instructions for the agent.
            Default is "You are a helpful assistant.".
        **kwargs : dict
            Additional keyword arguments passed to the base `agents.Agent`.
        """
        super().__init__(name=name, instructions=instructions, **kwargs)
        self.runner = Runner()

    async def run_agent(self, user_input: str, **kwargs):
        """
        Run the agent with the given user input.

        Parameters
        ----------
        user_input : str
            The input string from the user.
        **kwargs : dict
            Additional keyword arguments to pass to the `Runner.run` method.

        Returns
        -------
        Any
            The final output from the agent.
        """
        result = await self.runner.run(self, input=user_input, **kwargs)
        return result.final_output

    def run_agent_sync(self, user_input: str, **kwargs):
        """
        Run the agent synchronously with the given user input.

        Parameters
        ----------
        user_input : str
            The input string from the user.
        **kwargs : dict
            Additional keyword arguments to pass to the `Runner.run_sync` method.

        Returns
        -------
        Any
            The final output from the agent.
        """
        result = self.runner.run_sync(self, input=user_input, **kwargs)
        return result.final_output

class SpecialistAgent(ReusableAgent):
    """
    An example of a specialized agent that inherits from ReusableAgent.
    """
    def __init__(self, name: str = "Specialist", expertise: str = "general tasks", **kwargs):
        """
        Initialize a SpecialistAgent.

        Parameters
        ----------
        name : str, optional
            The name of the agent.
        expertise : str, optional
            The area of expertise for this agent. This will be added to its instructions.
        **kwargs : dict
            Additional keyword arguments passed to the ReusableAgent.
        """
        instructions = f"You are a helpful assistant specializing in {expertise}."
        super().__init__(name=name, instructions=instructions, **kwargs)

class GitHubClient:
    def __init__(self, token: Optional[str] = None, base_url: str = "https://api.github.com"):
        self.base_url = base_url
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        if self.token:
            self.headers["Authorization"] = f"token {self.token}"
        else:
            print("Warning: GitHubClient initialized without a GITHUB_TOKEN. Authenticated operations will fail.")

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            return response 
        except requests.exceptions.RequestException as e:
            print(f"GitHub API RequestException for {method} {url}: {e}")
            mock_response = requests.Response()
            mock_response.status_code = 503 
            mock_response.reason = "Network Error"
            try:
                mock_response.json = lambda: {"error": str(e), "message": "Network request to GitHub failed."}
            except: # pylint: disable=bare-except
                mock_response._content = b'{"error": "Network request to GitHub failed."}' # type: ignore
            return mock_response

    async def get_issue_details(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status() 
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_payload = {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_payload["details_json"] = e.response.json()
            except ValueError: pass 
            return error_payload
        except Exception as e:
            return {"error": f"Failed to get issue details for {owner}/{repo}#{issue_number}: {str(e)}"}

    async def get_latest_commit_sha(self, owner: str, repo: str, branch: str = "main") -> Optional[str]:
        endpoint = f"/repos/{owner}/{repo}/branches/{branch}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status()
            return response.json().get("commit", {}).get("sha")
        except Exception as e:
            print(f"Error getting latest commit SHA for {owner}/{repo}/{branch}: {e}")
            return None

    async def create_branch(self, owner: str, repo: str, new_branch_name: str, base_branch_name: str = "main") -> Dict[str, Any]:
        if not self.token:
            return {"error": "GitHub token is required to create a branch."}

        latest_sha = await self.get_latest_commit_sha(owner, repo, base_branch_name)
        if not latest_sha:
            return {"error": f"Could not get SHA for base branch '{base_branch_name}' in {owner}/{repo}."}

        endpoint = f"/repos/{owner}/{repo}/git/refs"
        payload = {"ref": f"refs/heads/{new_branch_name}", "sha": latest_sha}
        
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: self._make_request("POST", endpoint, json=payload))

        response_data = {}
        try:
            response_data = response.json()
        except (json.JSONDecodeError, ValueError): 
            response_data = {"text_response": response.text}

        if response.status_code == 201: 
            return response_data 
        elif response.status_code == 422: 
            message_from_response = response_data.get("message", "") if isinstance(response_data, dict) else response.text
            errors_from_response = response_data.get("errors", []) if isinstance(response_data, dict) else []

            if "Reference already exists" in message_from_response or \
               any(err.get("code") == "already_exists" for err in errors_from_response):
                msg = f"Branch '{new_branch_name}' already exists in {owner}/{repo}."
                print(f"INFO (agents.py): {msg}")
                return {"message": msg, "ref": f"refs/heads/{new_branch_name}", "object": {"sha": latest_sha}, "already_exists": True}
            else: 
                return {"error": f"422 Unprocessable Entity: {message_from_response}", 
                        "details_json": response_data if isinstance(response_data, dict) else {"text_response": response.text}}
        else: 
            try:
                response.raise_for_status() 
                return response_data 
            except requests.exceptions.HTTPError as e_http:
                 error_payload = {"error": f"HTTPError: {e_http.response.status_code} {e_http.response.reason}", "details_text": e_http.response.text}
                 if isinstance(response_data, dict): error_payload["details_json"] = response_data
                 return error_payload
            except Exception as e_generic: 
                 return {"error": f"An unexpected error occurred after branch creation attempt: {str(e_generic)}"}

    async def add_comment_to_issue(self, owner: str, repo: str, issue_number: int, comment_body: str) -> Dict[str, Any]:
        if not self.token:
            return {"error": "GitHub token is required to post a comment."}
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        payload = {"body": comment_body}
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("POST", endpoint, json=payload))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_payload = {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_payload["details_json"] = e.response.json()
            except ValueError: pass
            return error_payload
        except Exception as e:
            return {"error": f"Failed to post comment: {str(e)}"}

    async def get_file_sha(self, owner: str, repo: str, file_path: str, branch_name: str) -> Optional[str]:
        """Gets the SHA of an existing file on a branch, returns None if not found."""
        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch_name}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            if response.status_code == 200:
                return response.json().get("sha")
            elif response.status_code == 404:
                return None # File does not exist
            response.raise_for_status() 
            return None 
        except Exception as e:
            print(f"Error getting file SHA for {owner}/{repo}/{file_path} on branch {branch_name}: {e}")
            return None

    async def create_commit_on_branch(self, owner: str, repo: str, branch_name: str, commit_message: str, file_path: str, file_content: str) -> Dict[str, Any]:
        """
        Creates or updates a file in a branch and commits it.
        Uses GitHub API: PUT /repos/{owner}/{repo}/contents/{path}
        """
        if not self.token:
            return {"error": "GitHub token is required to commit files."}

        print(f"GitHubClient: Committing to {owner}/{repo} on branch '{branch_name}', file '{file_path}'")
        
        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}"
        
        try:
            content_bytes = file_content.encode('utf-8')
            encoded_content = base64.b64encode(content_bytes).decode('utf-8')
        except Exception as e:
            return {"error": f"Failed to encode file content: {str(e)}"}

        payload: Dict[str, Any] = {
            "message": commit_message,
            "content": encoded_content,
            "branch": branch_name
        }

        existing_file_sha = await self.get_file_sha(owner, repo, file_path, branch_name)
        if existing_file_sha:
            payload["sha"] = existing_file_sha
            print(f"  Updating existing file '{file_path}' (SHA: {existing_file_sha}).")
        else:
            print(f"  Creating new file '{file_path}'.")
        
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("PUT", endpoint, json=payload))
            response.raise_for_status() 
            
            response_json = response.json()
            commit_details = response_json.get("commit", {})
            content_details = response_json.get("content", {})
            
            return {
                "message": "File committed successfully.",
                "commit_sha": commit_details.get("sha"),
                "commit_url": commit_details.get("html_url"),
                "file_sha": content_details.get("sha"),
                "file_url": content_details.get("html_url"),
                "branch": branch_name,
                "file_path": file_path,
                "details": response_json 
            }
        except requests.exceptions.HTTPError as e:
            error_details = {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_details["details_json"] = e.response.json()
            except ValueError: pass
            return error_details
        except Exception as e:
            return {"error": f"An unexpected error occurred during commit: {str(e)}"}


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


class IssueTriagerAgent(ReusableAgent):
    def __init__(self, **kwargs):
        super().__init__(name="IssueTriager", instructions="Triage GitHub issues using the download_github_issue tool. Provide a detailed summary including title, URL, author, state, labels, comment count, creation/update dates, a concise description of the issue, your analysis of the issue type (e.g., bug, feature, documentation), and a suggested priority (e.g., Low, Medium, High) with a brief justification.", tools=[download_github_issue], **kwargs)

class CodeProposerAgent(ReusableAgent): 
    def __init__(self, **kwargs):
        super().__init__(
            name="CodeProposer",
            instructions=(
                "You are an expert software developer. Based on the provided GitHub issue details "
                "(title, body, labels, target file path, programming language context if available), "
                "propose a code solution to address the issue for the specified target file. "
                "Format your response clearly, with the code solution enclosed in a single markdown code block "
                "with the appropriate language identifier (e.g., ```python ... ``` or ```r ... ```). "
                "If the issue is unclear or lacks enough information to propose a specific code fix, "
                "state what additional information is needed. "
                "If you are revising based on feedback, clearly state that and incorporate the feedback into the new code block."
            ),
            **kwargs 
        )

class CodeReviewerAgent(ReusableAgent):
    def __init__(self, review_aspect: str = "general code quality", **kwargs):
        super().__init__(
            name=f"{review_aspect.replace(' ', '')}Reviewer",
            instructions=(
                f"You are a meticulous code reviewer specializing in {review_aspect}. "
                "You will be given GitHub issue details and a proposed code solution for a specific file. "
                "Provide a concise review. Focus on: "
                f"- {review_aspect.capitalize()}\n"
                "- Correctness and completeness of the solution regarding the issue and its intended file.\n"
                "- Potential bugs or edge cases.\n"
                "- Adherence to coding best practices and style guidelines for the inferred language.\n"
                "- Clarity and maintainability.\n"
                "If the solution is satisfactory, state ONLY 'LGTM!' or 'Satisfactory' or 'Approved'. " # Made stricter
                "If changes are needed, clearly list them and state 'Needs revision.' as the first part of your response."
            ),
            **kwargs
        )

class CodeCommitterAgent(ReusableAgent): 
    def __init__(self, **kwargs):
        super().__init__(
            name="CodeCommitter",
            instructions=(
                "You are a Git assistant. Your task is to commit a given code solution to a specified branch. "
                "You will receive the repository owner, repository name, branch name, commit message, "
                "the file path for the code, and the code content itself. "
                "Use the `commit_code_to_branch` tool to perform this action. "
                "Summarize the result of the commit attempt based on the tool's output."
            ),
            tools=[commit_code_to_branch],
            **kwargs
        )

class BranchCreatorAgent(ReusableAgent): 
    def __init__(self, **kwargs):
        super().__init__(
            name="BranchCreator",
            instructions=(
                "You are a Git assistant. Your task is to create or ensure a new branch exists for a given GitHub issue. "
                "You will be given the repository owner, repository name, issue number, and optionally a branch prefix and base branch. "
                "Use the `create_pr_branch` tool to perform this action. "
                "Confirm the branch creation or existence based on the tool's output."
            ),
            tools=[create_pr_branch],
            **kwargs
        )

class CommentPosterAgent(ReusableAgent):
    def __init__(self, **kwargs):
        super().__init__(name="CommentPoster", instructions="Post comments to GitHub issues using the post_comment_to_github tool.", tools=[post_comment_to_github], **kwargs)

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

async def solve_github_issue_flow(
    issue_url: str,
    repo_owner_override: Optional[str] = None, 
    repo_name_override: Optional[str] = None,  
    target_file_path: str = "src/solution.py" # Still passed, but agents are more aware
    ):
    print(f"\n🚀 Starting GitHub Issue Solver for: {issue_url}\n" + "="*50)

    repo_owner = repo_owner_override
    repo_name = repo_name_override

    if not repo_owner or not repo_name:
        parsed_info = parse_github_issue_url(issue_url)
        if parsed_info:
            repo_owner = repo_owner or parsed_info[0]
            repo_name = repo_name or parsed_info[1]
        else:
            print(f"❌ Error: Could not parse repository owner and name from issue URL: {issue_url}")
            return
    print(f"Target Repository: {repo_owner}/{repo_name}")
    print(f"Target File Path for fix: {target_file_path}")


    triager = IssueTriagerAgent()
    code_proposer = CodeProposerAgent() 
    technical_reviewer = CodeReviewerAgent(review_aspect="technical correctness and efficiency")
    style_reviewer = CodeReviewerAgent(review_aspect="code style and readability")
    branch_creator = BranchCreatorAgent()
    committer = CodeCommitterAgent()   
    comment_poster = CommentPosterAgent()
    runner = Runner()

    # --- Step 1: Triaging Issue ---
    print("\n🔍 Step 1: Triaging Issue...")
    triage_result_run = await runner.run(triager, input=f"Please triage the GitHub issue at {issue_url}")
    triage_output_summary = triage_result_run.final_output
    print(f"Triager Output Summary:\n{triage_output_summary}\n")

    issue_details_from_tool: Optional[Dict[str, Any]] = None
    tool_call_item_index_for_download = -1
    
    new_items_triage: Optional[List[Any]] = getattr(triage_result_run, 'new_items', None)
    if new_items_triage:
        for i, item in enumerate(new_items_triage):
            item_type_name = type(item).__name__
            is_tool_call_item = (ToolCallItem and isinstance(item, ToolCallItem)) or (item_type_name == 'ToolCallItem')
            if is_tool_call_item:
                raw_item_from_tool_call = getattr(item, 'raw_item', None)
                current_tool_id = None
                current_tool_name = None
                if raw_item_from_tool_call:
                    current_tool_id = getattr(raw_item_from_tool_call, 'id', None)
                    current_tool_name = getattr(raw_item_from_tool_call, 'name', None) 
                    if not current_tool_name: 
                        tool_function_attr = getattr(raw_item_from_tool_call, 'function', None)
                        if tool_function_attr and hasattr(tool_function_attr, 'name'):
                            current_tool_name = getattr(tool_function_attr, 'name', None)
                
                if current_tool_name == 'download_github_issue' and current_tool_id:
                    tool_call_item_index_for_download = i
                    break 
        
        if tool_call_item_index_for_download != -1 and (tool_call_item_index_for_download + 1) < len(new_items_triage):
            output_item_candidate = new_items_triage[tool_call_item_index_for_download + 1]
            output_item_type_name = type(output_item_candidate).__name__
            is_tool_call_output_item = (ToolCallOutputItem and isinstance(output_item_candidate, ToolCallOutputItem)) or \
                                       (output_item_type_name == 'ToolCallOutputItem')
            if is_tool_call_output_item:
                content = getattr(output_item_candidate, 'output', None) 
                if content is None: content = getattr(output_item_candidate, 'content', None)
                if content is None and hasattr(output_item_candidate, 'raw_item'): 
                    raw_item_of_output = getattr(output_item_candidate, 'raw_item')
                    if isinstance(raw_item_of_output, dict): content = raw_item_of_output 
                
                if isinstance(content, dict): issue_details_from_tool = content
                elif isinstance(content, str):
                    try:
                        import json
                        issue_details_from_tool = json.loads(content)
                    except json.JSONDecodeError: issue_details_from_tool = {"error": "Failed to parse tool output JSON", "raw_content": content}
                else: issue_details_from_tool = {"error": f"Unexpected tool output type: {type(content)}", "raw_content": str(content)}
    
    if not issue_details_from_tool or "error" in issue_details_from_tool:
        print(f"❌ Error: Could not retrieve valid issue details. Details: {issue_details_from_tool}")
        return

    issue_number = issue_details_from_tool.get("number")
    issue_title = issue_details_from_tool.get("title", "Unknown Title")
    issue_body = issue_details_from_tool.get("body", "No body provided.")
    issue_labels_data = issue_details_from_tool.get("labels", []) 
    issue_labels = [label.get("name") if isinstance(label, dict) else label for label in issue_labels_data if (isinstance(label, dict) and label.get("name")) or isinstance(label, str)]

    if not issue_number:
        print(f"❌ Error: Issue number not found in triaged details.")
        return
    print(f"Successfully processed issue #{issue_number}: '{issue_title}'")

    # --- Step 2: Propose Initial Code Solution ---
    print(f"\n💡 Step 2: Proposing Initial Code Solution for issue #{issue_number}...")
    proposer_input = (
        f"Based on the following GitHub issue, please propose a code solution.\n"
        f"Issue Title: {issue_title}\n"
        f"Issue Body:\n{issue_body}\n\n"
        f"Labels: {', '.join(issue_labels)}\n"
        f"The code should be for the file path: '{target_file_path}'. " 
        "Infer the programming language from the issue context or file path. "
        "Format your response with the code solution in a single markdown code block."
    )
    proposer_run = await runner.run(code_proposer, input=proposer_input)
    proposed_solution_markdown = proposer_run.final_output
    current_proposed_code = extract_code_from_markdown(proposed_solution_markdown)
    print(f"Code Proposer Output (Markdown):\n{proposed_solution_markdown}")
    if current_proposed_code:
        print(f"Extracted Code Solution:\n{current_proposed_code}\n")
    else:
        print("⚠️ Code Proposer did not provide a usable code block. Cannot proceed with review or commit.\n")
        current_proposed_code = None 
        
    # --- Step 2.5: Review and Revision Loop ---
    max_review_cycles = 3
    tech_feedback = "No feedback yet."
    style_feedback = "No feedback yet."
    solution_satisfactory = False
    final_code_to_commit = current_proposed_code 

    if current_proposed_code: 
        for cycle in range(max_review_cycles):
            print(f"\n🔄 Review Cycle {cycle + 1}/{max_review_cycles} 🔄")
            
            review_task_input = (
                f"Issue Title: {issue_title}\nIssue Number: {issue_number}\nIssue Body:\n{issue_body}\n\n"
                f"Labels: {', '.join(issue_labels)}\n\n"
                f"Proposed Solution Code (for file '{target_file_path}'):\n{final_code_to_commit}\n" 
            )
            
            print("🕵️‍♂️ Requesting Technical Review...")
            technical_review_run = await runner.run(technical_reviewer, input=review_task_input)
            tech_feedback = technical_review_run.final_output
            print(f"Technical Reviewer Output:\n{tech_feedback}\n")

            print("🎨 Requesting Style Review...")
            style_review_run = await runner.run(style_reviewer, input=review_task_input)
            style_feedback = style_review_run.final_output
            print(f"Style Reviewer Output:\n{style_feedback}\n")

            tech_ok = any(s in tech_feedback.lower() for s in ["lgtm", "satisfactory", "looks good", "approved"])
            style_ok = any(s in style_feedback.lower() for s in ["lgtm", "satisfactory", "looks good", "approved"])

            if tech_ok and style_ok:
                print("✅ Both reviewers are satisfied. Proceeding with the current solution.")
                solution_satisfactory = True
                break 
            
            if cycle < max_review_cycles - 1: 
                print("⚠️ Revision needed. Requesting CodeProposer to revise...")
                revision_proposer_input = (
                    f"The following code was proposed for GitHub issue #{issue_number} ('{issue_title}') to be placed in file '{target_file_path}':\n"
                    f"```\n{final_code_to_commit}\n```\n\n" 
                    f"It received the following feedback:\n"
                    f"Technical Review: {tech_feedback}\n"
                    f"Style Review: {style_feedback}\n\n"
                    "Please provide a revised code solution addressing this feedback for the same target file. "
                    "Output only the new code block."
                )
                proposer_run = await runner.run(code_proposer, input=revision_proposer_input)
                revised_solution_markdown = proposer_run.final_output
                revised_code = extract_code_from_markdown(revised_solution_markdown)
                print(f"Code Proposer Output (Revised Markdown):\n{revised_solution_markdown}")

                if revised_code and revised_code != final_code_to_commit :
                    final_code_to_commit = revised_code 
                    print(f"Updated Code Solution after revision:\n{final_code_to_commit}\n")
                elif revised_code == final_code_to_commit:
                    print("Code Proposer returned the same code. Assuming no further changes possible based on feedback.")
                    solution_satisfactory = tech_ok and style_ok 
                    break 
                else: 
                    print("⚠️ Code Proposer did not provide a new code block in its revision. Using last valid code.")
                    break 
            else: 
                print(f"⚠️ Maximum review cycles ({max_review_cycles}) reached. Proceeding with the last proposed solution.")
                solution_satisfactory = tech_ok and style_ok 
    else: 
        print("⚠️ No initial code proposed by CodeProposer. Skipping review and commit steps for code.")
        final_code_to_commit = None 

    # --- Step 3: Creating/Ensuring Branch ---
    print("\n🌿 Step 3: Creating/Ensuring Branch...")
    branch_prefix = "fix" 
    if any("enhancement" in label.lower() for label in issue_labels): branch_prefix = "feature"
    elif any("chore" in label.lower() for label in issue_labels): branch_prefix = "chore"
    
    target_branch_name_ideal = f"{branch_prefix}/issue-{issue_number}"
    
    branch_run = await runner.run(branch_creator, input=f"Ensure branch for {repo_owner}/{repo_name} issue {issue_number}, prefix {branch_prefix}, base main.")
    branch_agent_summary = branch_run.final_output 
    print(f"Branch Creator Agent Output: {branch_agent_summary}\n")
    
    actual_branch_name_from_tool = target_branch_name_ideal 
    branch_op_success = False

    new_items_branch_check = getattr(branch_run, 'new_items', None)
    if new_items_branch_check:
        tool_call_item_idx_branch = -1
        for i_br, item_br in enumerate(new_items_branch_check):
            if type(item_br).__name__ == 'ToolCallItem':
                raw_item_br = getattr(item_br, 'raw_item', None)
                tool_name_br = None
                if raw_item_br:
                    tool_name_br = getattr(raw_item_br, 'name', None)
                    if not tool_name_br:
                         func_br = getattr(raw_item_br, 'function', None)
                         if func_br: tool_name_br = getattr(func_br, 'name', None)
                if tool_name_br == 'create_pr_branch':
                    tool_call_item_idx_branch = i_br
                    break
        
        if tool_call_item_idx_branch != -1 and (tool_call_item_idx_branch + 1) < len(new_items_branch_check):
            output_item_branch = new_items_branch_check[tool_call_item_idx_branch + 1]
            if type(output_item_branch).__name__ == 'ToolCallOutputItem':
                content_br = getattr(output_item_branch, 'output', getattr(output_item_branch, 'content', None))
                if content_br is None and hasattr(output_item_branch, 'raw_item'):
                    raw_br_item = output_item_branch.raw_item
                    if isinstance(raw_br_item, dict): content_br = raw_br_item 
                
                # print(f"DEBUG: Branch tool output content from new_items: {content_br}")
                if isinstance(content_br, dict):
                    if "error" not in content_br:
                        branch_op_success = True
                        actual_branch_name_from_tool = content_br.get("branch_name", target_branch_name_ideal)
                        if content_br.get("status") == "already_exists" or content_br.get("already_exists") is True:
                             print(f"INFO: Branch '{actual_branch_name_from_tool}' already exists.")
                        else:
                             print(f"INFO: Branch '{actual_branch_name_from_tool}' creation/check successful.")
                    else:
                        print(f"ERROR: Branch tool reported error: {content_br.get('error')}")
                        branch_agent_summary = content_br.get('error', branch_agent_summary) 
                # else:
                    # print(f"WARNING: Branch tool output content was not a dict: {content_br}")
    
    final_target_branch = actual_branch_name_from_tool

    # --- Step 4: Committing Code ---
    commit_status = "Commit skipped: No code available or solution not satisfactory."
    if final_code_to_commit : 
        if branch_op_success:
            print(f"\n💾 Step 4: Committing Code to branch '{final_target_branch}'...")
            commit_message_text = f"Propose solution for issue #{issue_number}: {issue_title}"
            committer_input = (
                f"Commit the following code to repository {repo_owner}/{repo_name} on branch {final_target_branch}. "
                f"File path: {target_file_path}. Commit message: '{commit_message_text}'\n\n"
                f"Code Content:\n{final_code_to_commit}"
            )
            committer_run = await runner.run(committer, input=committer_input)
            commit_status_agent_summary = committer_run.final_output # Agent's summary
            print(f"Code Committer Agent Output:\n{commit_status_agent_summary}\n")
            
            commit_tool_output_content = None
            new_items_commit_check = getattr(committer_run, 'new_items', None)
            if new_items_commit_check:
                commit_tool_call_idx = -1
                for i_c, item_c in enumerate(new_items_commit_check):
                    if type(item_c).__name__ == 'ToolCallItem':
                        raw_item_c = getattr(item_c, 'raw_item', None)
                        tool_name_c = None
                        if raw_item_c:
                            tool_name_c = getattr(raw_item_c, 'name', None)
                            if not tool_name_c:
                                func_c = getattr(raw_item_c, 'function', None)
                                if func_c: tool_name_c = getattr(func_c, 'name', None)
                        if tool_name_c == 'commit_code_to_branch':
                            commit_tool_call_idx = i_c
                            break
                if commit_tool_call_idx != -1 and (commit_tool_call_idx + 1) < len(new_items_commit_check):
                    output_item_commit = new_items_commit_check[commit_tool_call_idx + 1]
                    if type(output_item_commit).__name__ == 'ToolCallOutputItem':
                        commit_tool_output_content = getattr(output_item_commit, 'output', getattr(output_item_commit, 'content', None))
                        if commit_tool_output_content is None and hasattr(output_item_commit, 'raw_item'):
                            raw_c_item = output_item_commit.raw_item
                            if isinstance(raw_c_item, dict): commit_tool_output_content = raw_c_item
            
            # print(f"DEBUG: Commit tool output content: {commit_tool_output_content}")
            if isinstance(commit_tool_output_content, dict) and "error" not in commit_tool_output_content:
                commit_status = commit_tool_output_content.get("message", "Commit successful (details in tool output).") 
                print(f"INFO: Commit to '{target_file_path}' on branch '{final_target_branch}' reported by tool: {commit_status}")
                if commit_tool_output_content.get("commit_url"):
                    commit_status += f" View commit: {commit_tool_output_content.get('commit_url')}"
            elif isinstance(commit_tool_output_content, dict) and "error" in commit_tool_output_content:
                commit_status = f"Commit failed (tool error): {commit_tool_output_content.get('error')}"
                print(f"ERROR: Commit tool reported error: {commit_tool_output_content.get('error')}")
            else: # If tool output wasn't clear, rely on agent's summary
                commit_status = commit_status_agent_summary


        else:
            commit_status = f"Commit skipped: Branch '{final_target_branch}' not successfully created or ensured."
            print(f"⚠️ {commit_status}")
    else:
         print(f"⚠️ {commit_status}") 

    # --- Step 5: Posting Summary Comment ---
    print("\n💬 Step 5: Posting Summary Comment...")
    summary_comment_parts = [f"Automated processing for issue #{issue_number} ('{issue_title}'):"]
    summary_comment_parts.append(f"\n**Triage Summary:**\n{triage_output_summary}")
    if proposed_solution_markdown: 
        summary_comment_parts.append(f"\n**Initial Code Proposal Attempt:**\n{proposed_solution_markdown}")
    else:
        summary_comment_parts.append(f"\n**Initial Code Proposal Attempt:** CodeProposer did not provide an initial solution.")

    summary_comment_parts.append(f"\n**Technical Review:**\n{tech_feedback}")
    summary_comment_parts.append(f"\n**Style Review:**\n{style_feedback}")
    
    if final_code_to_commit and final_code_to_commit != extract_code_from_markdown(proposed_solution_markdown): 
         summary_comment_parts.append(f"\n**Final (Revised) Code Solution (for {target_file_path}):**\n```\n{final_code_to_commit}\n```")
    elif final_code_to_commit: 
         summary_comment_parts.append(f"\n**Final Code Solution (for {target_file_path}):**\n```\n{final_code_to_commit}\n```")
    else:
        summary_comment_parts.append(f"\n**Final Code Solution:** No code was finalized for commit.")

    if branch_op_success:
        summary_comment_parts.append(f"\n**Branch:** `{final_target_branch}` (Created/Ensured)")
        summary_comment_parts.append(f"\n**Code Commit Status to '{target_file_path}':**\n{commit_status}")
    else:
        summary_comment_parts.append(f"\n**Branch Creation Attempt Summary:** {branch_agent_summary}")
        summary_comment_parts.append(f"\n**Code Commit Status:** {commit_status}") # Reflect commit status even if branch failed

    final_summary_comment = "\n".join(summary_comment_parts)
    comment_run = await runner.run(comment_poster, input=f"Post the following comment to {issue_url}: \n\n{final_summary_comment}")
    print(f"Comment Poster Agent Output: {comment_run.final_output}\n")

    print("="*50 + "\n✅ GitHub Issue Solver Flow Completed!\n")

async def main_test_flow():
    test_issue_url = "https://github.com/bgreenwell/statlingua/issues/12" 
    target_file_for_fix = "R/explain.R" 
    
    if not os.environ.get("GITHUB_TOKEN"): print("🚨 WARNING: GITHUB_TOKEN not set.")
    if not (os.environ.get("PROXY_API_KEY") and os.environ.get("PROXY_API_URL")) and not os.environ.get("OPENAI_API_KEY"):
        print("🚨 WARNING: OpenAI API key not set.")
    
    print(f"--- Starting GitHub Issue Solver Test Flow ---\nTargeting issue: {test_issue_url}")
    await solve_github_issue_flow(
        issue_url=test_issue_url,
        target_file_path=target_file_for_fix
    )

if __name__ == "__main__":
    import sys
    current_dir = os.path.dirname(os.path.abspath(__file__)) 
    examples_dir = os.path.dirname(current_dir) 
    project_root = os.path.dirname(examples_dir) 
    
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    
    asyncio.run(main_test_flow())
