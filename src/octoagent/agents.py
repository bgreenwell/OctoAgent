"""
Defines the agent classes used in the OctoAgent workflow.

Each agent is a specialized class inheriting from a reusable base agent,
with specific instructions and tools tailored to its role (e.g., triaging
issues, proposing code, reviewing code).
"""
from agents import Agent as BaseAgent, Runner
from .tools import (
    download_github_issue,
    create_pr_branch,
    # commit_code_to_branch, # This was the old tool, now removed
    post_comment_to_github,
    list_repository_files,
    commit_files_to_branch   # This is the new tool for multi-file commits
)


class ReusableAgent(BaseAgent):
    """
    A reusable base agent class that can be extended for specific use cases.

    This class provides a foundation for building more specialized agents.

    Parameters
    ----------
    name : str
        The name of the agent.
    instructions : str, optional
        The system prompt or instructions for the agent.
        Defaults to "You are a helpful assistant.".
    **kwargs : dict
        Additional keyword arguments to pass to the base `agents.Agent` class.

    Attributes
    ----------
    runner : agents.Runner
        An instance of the agent runner for synchronous and asynchronous execution.
    """
    def __init__(self, name: str, instructions: str = "You are a helpful assistant.", **kwargs):
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
            Additional keyword arguments to pass to `Runner.run_sync`.

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

    Parameters
    ----------
    name : str, optional
        The name of the agent. Defaults to "Specialist".
    expertise : str, optional
        The area of expertise for this agent, added to its instructions.
        Defaults to "general tasks".
    **kwargs : dict
        Additional keyword arguments passed to the ReusableAgent.
    """
    def __init__(self, name: str = "Specialist", expertise: str = "general tasks", **kwargs):
        instructions = f"You are a helpful assistant specializing in {expertise}."
        super().__init__(name=name, instructions=instructions, **kwargs)


class IssueTriagerAgent(ReusableAgent):
    """An agent that triages GitHub issues."""
    def __init__(self, **kwargs):
        super().__init__(name="IssueTriager", instructions="Triage GitHub issues using the download_github_issue tool. Provide a detailed summary including title, URL, author, state, labels, comment count, creation/update dates, a concise description of the issue, your analysis of the issue type (e.g., bug, feature, documentation), and a suggested priority (e.g., Low, Medium, High) with a brief justification.", tools=[download_github_issue], **kwargs)


class PlannerAgent(ReusableAgent):
    """
    An agent that analyzes a triaged GitHub issue and creates a
    high-level, step-by-step plan to address it.
    """
    def __init__(self, **kwargs):
        super().__init__(
            name="PlannerAgent",
            instructions=(
                "You are an expert software project planner. Based on the provided GitHub issue details "
                "(title, body, labels, triage summary), your task is to create a concise, actionable, "
                "step-by-step plan to guide the resolution of this issue. "
                "The plan should outline the logical sequence of actions needed, such as "
                "'1. Identify affected file(s).', '2. Draft code changes for each affected file.', '3. Review solution.', etc. "
                "Focus on a high-level strategy. Output the plan as a numbered list."
            ),
            **kwargs
        )


class FileIdentifierAgent(ReusableAgent):
    """An agent that identifies the target file(s) to fix for an issue."""
    def __init__(self, **kwargs):
        super().__init__(
            name="FileIdentifierAgent",
            instructions=(
                "You are an expert software architect. Your task is to identify which file(s) in a repository "
                "most likely need to be modified to address a given GitHub issue. "
                "You will be provided with the issue details (title, body, labels) and the overall plan. "
                "Use the `list_repository_files` tool to see the repository's file structure. "
                "Based on the issue description, the plan, and the list of files, determine the relevant file paths. "
                "Output ONLY the full file paths, each on a new line. If no files seem to need changes, output 'None'."
            ),
            tools=[list_repository_files],
            **kwargs
        )


class CodeProposerAgent(ReusableAgent):
    """An agent that proposes code solutions for GitHub issues across multiple files."""
    def __init__(self, **kwargs):
        super().__init__(
            name="CodeProposer",
            instructions=(
                "You are an expert software developer. Based on the provided GitHub issue details, "
                "the overall plan, and a list of target file paths, propose code solutions. "
                "For each file in the provided list that requires changes, clearly state 'Changes for `path/to/file.ext`:' "
                "followed by the complete proposed code for that file in a single markdown code block "
                "with the appropriate language identifier (e.g., ```python ... ``` or ```r ... ```). "
                "If a file in the list does not need changes, state 'No changes needed for `path/to/file.ext`.' "
                "If the issue is unclear or lacks enough information to propose a specific code fix for any file, "
                "state what additional information is needed for those files. "
                "If you are revising based on feedback, clearly state that and incorporate the feedback into the new code proposals for the specified files."
            ),
            **kwargs
        )


class CodeReviewerAgent(ReusableAgent):
    """
    An agent that reviews proposed code solutions for multiple files.

    Parameters
    ----------
    review_aspect : str, optional
        The specific aspect of the code to focus on (e.g., "code style").
        Defaults to "general code quality".
    **kwargs : dict
        Additional keyword arguments passed to the ReusableAgent.
    """
    def __init__(self, review_aspect: str = "general code quality", **kwargs):
        super().__init__(
            name=f"{review_aspect.replace(' ', '')}Reviewer",
            instructions=(
                f"You are a meticulous code reviewer specializing in {review_aspect}. "
                "You will be given GitHub issue details, an overall plan, and a list of proposed code changes, "
                "each for a specific file. Provide a concise review for EACH file's proposed changes. Focus on: "
                f"- {review_aspect.capitalize()} for each file.\n"
                "- Correctness and completeness of the solution regarding the issue and its intended file.\n"
                "- Potential bugs or edge cases.\n"
                "- Adherence to coding best practices and style guidelines for the inferred language.\n"
                "- Clarity and maintainability.\n"
                "If all proposed changes across all files are satisfactory, state ONLY 'LGTM!' or 'Satisfactory' or 'Approved'. "
                "If changes are needed for ANY file, state 'Needs revision.' as the first part of your response, "
                "then for each file needing changes, clearly list the file path and the required revisions for that specific file."
            ),
            **kwargs
        )


class CodeCommitterAgent(ReusableAgent):
    """An agent that commits multiple files to a branch using a tool."""
    def __init__(self, **kwargs):
        # The tool 'commit_files_to_branch' is now imported at the module level.
        super().__init__(
            name="CodeCommitter",
            instructions=(
                "You are a Git assistant. Your task is to commit a list of file changes to a specified branch. "
                "You will receive the repository owner, repository name, branch name, commit message, "
                "and a list of file changes (each with a file path and code content). "
                "Use the `commit_files_to_branch` tool to perform this action. "
                "Summarize the result of the commit attempts based on the tool's output."
            ),
            tools=[commit_files_to_branch],
            **kwargs
        )


class BranchCreatorAgent(ReusableAgent):
    """An agent that creates a branch for a pull request."""
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
    """An agent that posts comments to GitHub issues."""
    def __init__(self, **kwargs):
        super().__init__(name="CommentPoster", instructions="Post comments to GitHub issues using the post_comment_to_github tool.", tools=[post_comment_to_github], **kwargs)
        