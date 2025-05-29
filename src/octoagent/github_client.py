import asyncio
import base64
import json
import os
import requests
from typing import Any, Dict, Optional

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
