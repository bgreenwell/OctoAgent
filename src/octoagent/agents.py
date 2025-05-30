"""
Defines the agent classes used in the OctoAgent workflow.

Each agent is a specialized class inheriting from a reusable base agent,
with specific instructions and tools tailored to its role (e.g., triaging
issues, proposing code, reviewing code).
"""
from agents import Agent as BaseAgent, Runner
from .tools import download_github_issue, create_pr_branch, commit_code_to_branch, post_comment_to_github, list_repository_files


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

class FileIdentifierAgent(ReusableAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="FileIdentifierAgent",
            instructions=(
                "You are an expert software architect. Your task is to identify which single file in a repository "
                "most likely needs to be modified to address a given GitHub issue. "
                "You will be provided with the issue details (title, body, labels). "
                "Use the `list_repository_files` tool to see the repository's file structure. "
                "Based on the issue description and the list of files, determine the single most relevant file path. "
                "Output ONLY the full file path as a string, and nothing else."
            ),
            tools=[list_repository_files],
            **kwargs
        )

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
    """
    An agent that reviews proposed code solutions.

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
