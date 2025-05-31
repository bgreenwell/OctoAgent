"""
A client for interacting with the GitHub REST API.

This module provides the GitHubClient class, which encapsulates the logic for
making authenticated requests to the GitHub API to perform actions like
fetching issue details, creating branches, and committing files.
"""
import asyncio
import base64
import json
import os
import requests
from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class GitHubClient:
    """
    A client to handle interactions with the GitHub REST API.

    Parameters
    ----------
    token : str, optional
        The GitHub Personal Access Token. If not provided, it will be read
        from the `GITHUB_TOKEN` environment variable.
    base_url : str, optional
        The base URL for the GitHub API, by default "https://api.github.com".

    Attributes
    ----------
    base_url : str
        The base URL for the GitHub API.
    token : str or None
        The GitHub token used for authentication.
    headers : dict
        The headers to include in all API requests.
    """
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
            logger.warning("GitHubClient initialized without a GITHUB_TOKEN. Authenticated operations will fail.")

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        A private helper method to make a request to the GitHub API.

        Parameters
        ----------
        method : str
            The HTTP method to use (e.g., 'GET', 'POST', 'PUT').
        endpoint : str
            The API endpoint to target (e.g., '/repos/owner/repo').
        **kwargs : dict
            Additional keyword arguments to pass to `requests.request`.

        Returns
        -------
        requests.Response
            The response object from the API request. Returns a mock response
            in case of a network error.
        """
        url = f"{self.base_url}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            return response
        except requests.exceptions.RequestException as e:
            logger.error(f"GitHub API RequestException for {method} {url}: {e}")
            mock_response = requests.Response()
            mock_response.status_code = 503
            mock_response.reason = "Network Error"
            try:
                mock_response.json = lambda: {"error": str(e), "message": "Network request to GitHub failed."}
            except: # pylint: disable=bare-except
                mock_response._content = b'{"error": "Network request to GitHub failed."}' # type: ignore
            return mock_response

    async def get_default_branch(self, owner: str, repo: str) -> Optional[str]:
        """
        Gets the default branch name for a repository.

        Parameters
        ----------
        owner : str
            The owner of the repository.
        repo : str
            The name of the repository.

        Returns
        -------
        str or None
            The name of the default branch, or None if an error occurs.
        """
        endpoint = f"/repos/{owner}/{repo}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status()
            return response.json().get("default_branch")
        except Exception as e:
            logger.error(f"Error getting default branch for {owner}/{repo}: {e}")
            return None

    async def get_issue_details(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """
        Retrieves the details for a specific GitHub issue.

        Parameters
        ----------
        owner : str
            The owner of the repository.
        repo : str
            The name of the repository.
        issue_number : int
            The number of the issue to retrieve.

        Returns
        -------
        dict
            A dictionary containing the issue details, or an error payload.
        """
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTPError getting issue details for {owner}/{repo}#{issue_number}: {e.response.status_code} {e.response.reason} - {e.response.text[:100]}")
            error_payload = {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_payload["details_json"] = e.response.json()
            except ValueError: pass
            return error_payload
        except Exception as e:
            logger.error(f"Failed to get issue details for {owner}/{repo}#{issue_number}: {str(e)}")
            return {"error": f"Failed to get issue details for {owner}/{repo}#{issue_number}: {str(e)}"}

    async def get_latest_commit_sha(self, owner: str, repo: str, branch: str) -> Optional[str]:
        """
        Gets the SHA of the latest commit on a specific branch.

        Parameters
        ----------
        owner : str
            The owner of the repository.
        repo : str
            The name of the repository.
        branch : str
            The name of the branch.

        Returns
        -------
        str or None
            The SHA of the latest commit, or None if an error occurs.
        """
        endpoint = f"/repos/{owner}/{repo}/branches/{branch}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status()
            return response.json().get("commit", {}).get("sha")
        except Exception as e:
            logger.error(f"Error getting latest commit SHA for {owner}/{repo}/{branch}: {e}")
            return None

    async def create_branch(self, owner: str, repo: str, new_branch_name: str, base_branch_name: str) -> Dict[str, Any]:
        """
        Creates a new branch in a repository from a base branch.
        """
        if not self.token:
            logger.error("GitHub token is required to create a branch.")
            return {"error": "GitHub token is required to create a branch."}

        latest_sha = await self.get_latest_commit_sha(owner, repo, base_branch_name)
        if not latest_sha:
            logger.error(f"Could not get SHA for base branch '{base_branch_name}' in {owner}/{repo}.")
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
            logger.info(f"Branch '{new_branch_name}' created successfully in {owner}/{repo}.")
            return response_data
        elif response.status_code == 422:
            message_from_response = response_data.get("message", "") if isinstance(response_data, dict) else response.text
            errors_from_response = response_data.get("errors", []) if isinstance(response_data, dict) else []

            if "Reference already exists" in message_from_response or \
               any(err.get("code") == "already_exists" for err in errors_from_response):
                msg = f"Branch '{new_branch_name}' already exists in {owner}/{repo}."
                logger.info(msg)
                return {"message": msg, "ref": f"refs/heads/{new_branch_name}", "object": {"sha": latest_sha}, "already_exists": True}
            else:
                logger.error(f"422 Unprocessable Entity creating branch {new_branch_name}: {message_from_response}")
                return {"error": f"422 Unprocessable Entity: {message_from_response}",
                        "details_json": response_data if isinstance(response_data, dict) else {"text_response": response.text}}
        else:
            try:
                response.raise_for_status()
                return response_data
            except requests.exceptions.HTTPError as e_http:
                 logger.error(f"HTTPError creating branch {new_branch_name}: {e_http.response.status_code} {e_http.response.reason} - {e_http.response.text[:100]}")
                 error_payload = {"error": f"HTTPError: {e_http.response.status_code} {e_http.response.reason}", "details_text": e_http.response.text}
                 if isinstance(response_data, dict): error_payload["details_json"] = response_data
                 return error_payload
            except Exception as e_generic:
                 logger.error(f"Unexpected error after branch creation attempt for {new_branch_name}: {e_generic}")
                 return {"error": f"An unexpected error occurred after branch creation attempt: {str(e_generic)}"}

    async def add_comment_to_issue(self, owner: str, repo: str, issue_number: int, comment_body: str) -> Dict[str, Any]:
        """
        Adds a comment to a GitHub issue.
        """
        if not self.token:
            logger.error("GitHub token is required to post a comment.")
            return {"error": "GitHub token is required to post a comment."}
        endpoint = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        payload = {"body": comment_body}
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("POST", endpoint, json=payload))
            response.raise_for_status()
            logger.info(f"Comment posted successfully to {owner}/{repo}#{issue_number}.")
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTPError posting comment to {owner}/{repo}#{issue_number}: {e.response.status_code} {e.response.reason} - {e.response.text[:100]}")
            error_payload = {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_payload["details_json"] = e.response.json()
            except ValueError: pass
            return error_payload
        except Exception as e:
            logger.error(f"Failed to post comment to {owner}/{repo}#{issue_number}: {str(e)}")
            return {"error": f"Failed to post comment: {str(e)}"}

    async def get_file_sha(self, owner: str, repo: str, file_path: str, branch_name: str) -> Optional[str]:
        """
        Gets the SHA of an existing file on a branch.
        """
        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch_name}"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            if response.status_code == 200:
                return response.json().get("sha")
            elif response.status_code == 404:
                logger.debug(f"File {file_path} not found on branch {branch_name} in {owner}/{repo} during SHA lookup.")
                return None 
            response.raise_for_status()
            return None # Should not be reached if raise_for_status works
        except Exception as e:
            logger.error(f"Error getting file SHA for {owner}/{repo}/{file_path} on branch {branch_name}: {e}")
            return None

    async def get_file_content_from_repo(self, owner: str, repo: str, file_path: str, branch: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves the content of a specific file from a repository.
        """
        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}?ref={branch}"
        logger.debug(f"GitHubClient: Fetching content for {owner}/{repo}/{file_path} on branch {branch}")
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status() 
            
            response_json = response.json()
            if isinstance(response_json, list): 
                logger.warning(f"Path '{file_path}' on {owner}/{repo} is a directory, not a file.")
                return {"error": "Path is a directory, not a file.", "status": "is_directory"}

            if response_json.get("type") != "file":
                logger.warning(f"Path '{file_path}' on {owner}/{repo} is not a file (type: {response_json.get('type')}).")
                return {"error": f"Path is not a file (type: {response_json.get('type')}).", "status": "not_a_file"}

            content_base64 = response_json.get("content")
            if content_base64:
                decoded_content = base64.b64decode(content_base64).decode('utf-8')
                return {
                    "file_path": file_path,
                    "content": decoded_content,
                    "sha": response_json.get("sha"),
                    "status": "success"
                }
            else: 
                logger.warning(f"File content for '{file_path}' on {owner}/{repo} is empty or not available.")
                return {"error": "File content is empty or not available.", "status": "empty_content", "sha": response_json.get("sha")}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTPError fetching file {file_path}: {e.response.status_code} {e.response.reason} - {e.response.text[:100]}")
            if e.response.status_code == 404:
                return {"error": f"File not found: {file_path}", "status": "not_found"}
            return {"error": f"HTTPError fetching file: {e.response.status_code} {e.response.reason}", "details_text": e.response.text, "status": "http_error"}
        except Exception as e:
            logger.error(f"Unexpected error fetching content for {file_path}: {e}")
            return {"error": f"Unexpected error fetching file content: {str(e)}", "status": "unknown_error"}


    async def create_commit_on_branch(self, owner: str, repo: str, branch_name: str, commit_message: str, file_path: str, file_content: str) -> Dict[str, Any]:
        """
        Creates or updates a file in a branch and commits it.
        """
        if not self.token:
            logger.error("GitHub token is required to commit files.")
            return {"error": "GitHub token is required to commit files."}

        logger.info(f"GitHubClient: Committing to {owner}/{repo} on branch '{branch_name}', file '{file_path}'")

        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}"

        try:
            content_bytes = file_content.encode('utf-8')
            encoded_content = base64.b64encode(content_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to encode file content for {file_path}: {e}")
            return {"error": f"Failed to encode file content: {str(e)}"}

        payload: Dict[str, Any] = {
            "message": commit_message,
            "content": encoded_content,
            "branch": branch_name
        }

        existing_file_sha = await self.get_file_sha(owner, repo, file_path, branch_name)
        if existing_file_sha:
            payload["sha"] = existing_file_sha
            logger.debug(f"  Updating existing file '{file_path}' (SHA: {existing_file_sha}).")
        else:
            logger.debug(f"  Creating new file '{file_path}'.")

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("PUT", endpoint, json=payload))
            response.raise_for_status()

            response_json = response.json()
            commit_details = response_json.get("commit", {})
            content_details = response_json.get("content", {})
            logger.info(f"File '{file_path}' committed successfully to {branch_name}. SHA: {commit_details.get('sha')}")
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
            logger.error(f"HTTPError committing file {file_path}: {e.response.status_code} {e.response.reason} - {e.response.text[:100]}")
            error_details = {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_details["details_json"] = e.response.json()
            except ValueError: pass
            return error_details
        except Exception as e:
            logger.error(f"An unexpected error occurred during commit of {file_path}: {str(e)}")
            return {"error": f"An unexpected error occurred during commit: {str(e)}"}

    async def delete_file_on_branch(self, owner: str, repo: str, branch_name: str, file_path: str, commit_message: str, sha: str) -> Dict[str, Any]:
        """
        Deletes a file from a specific branch.
        """
        if not self.token:
            logger.error("GitHub token is required to delete files.")
            return {"error": "GitHub token is required to delete files."}

        endpoint = f"/repos/{owner}/{repo}/contents/{file_path}"
        payload = {
            "message": commit_message,
            "sha": sha,
            "branch": branch_name
        }
        logger.info(f"GitHubClient: Deleting file {owner}/{repo}/{file_path} on branch '{branch_name}' (SHA: {sha})")
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("DELETE", endpoint, json=payload))
            response.raise_for_status()
            logger.info(f"File '{file_path}' deleted successfully from {branch_name}.")
            return response.json() 
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTPError deleting file {file_path}: {e.response.status_code} {e.response.reason} - {e.response.text[:100]}")
            error_details = {"error": f"HTTPError deleting file: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
            try: error_details["details_json"] = e.response.json()
            except ValueError: pass
            return error_details
        except Exception as e:
            logger.error(f"An unexpected error occurred during file deletion for {file_path}: {str(e)}")
            return {"error": f"An unexpected error occurred during file deletion: {str(e)}"}

    async def list_files_in_repo(self, owner: str, repo: str, branch: str) -> Dict[str, Any]:
        """
        Lists all files in a repository recursively for a given branch.
        """
        latest_sha = await self.get_latest_commit_sha(owner, repo, branch)
        if not latest_sha:
            logger.error(f"Could not get latest commit SHA for branch '{branch}' in {owner}/{repo} to list files.")
            return {"error": f"Could not get latest commit SHA for branch '{branch}'."}

        endpoint = f"/repos/{owner}/{repo}/git/trees/{latest_sha}?recursive=true"
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._make_request("GET", endpoint))
            response.raise_for_status()
            response_json = response.json()
            files = [item['path'] for item in response_json.get('tree', []) if item.get('type') == 'blob']
            logger.debug(f"Found {len(files)} files in {owner}/{repo} on branch {branch}.")
            return {"files": files}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTPError listing files for {owner}/{repo} on branch {branch}: {e.response.status_code} {e.response.reason} - {e.response.text[:100]}")
            return {"error": f"HTTPError: {e.response.status_code} {e.response.reason}", "details_text": e.response.text}
        except Exception as e:
            logger.error(f"Failed to list files for {owner}/{repo} on branch {branch}: {str(e)}")
            return {"error": f"Failed to list files for {owner}/{repo} on branch {branch}: {str(e)}"}
    