"""
Defines the agent classes used in the OctoAgent workflow.
"""
import logging
import os

from typing import Optional
from agents import Agent as BaseAgent, Runner
from .tools import (
    download_github_issue,
    create_pr_branch,
    post_comment_to_github,
    list_repository_files,
    commit_files_to_branch,
    delete_file_from_branch,
)


logger = logging.getLogger(__name__)

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")

def load_prompt(file_name: str) -> str:
    """Loads a prompt from the prompts directory."""
    file_path = os.path.join(PROMPTS_DIR, file_name)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.error(f"Prompt file not found: {file_path}")
        # Return a generic error or a very basic default instruction
        return "Error: Critical prompt file not found. Please check installation."
    except Exception as e:
        logger.error(f"Error loading prompt {file_path}: {e}")
        return "Error: Could not load prompt due to an unexpected error."


class ReusableAgent(BaseAgent):
    """
    A reusable base agent class that can be extended for specific use cases.
    """
    DEFAULT_INSTRUCTIONS = "You are a helpful assistant."
    
    def __init__(self, name: str, instructions: Optional[str] = None, **kwargs):
        final_instructions = instructions if instructions is not None else self.DEFAULT_INSTRUCTIONS
        super().__init__(name=name, instructions=final_instructions, **kwargs)
        self.runner = Runner()
        logger.debug(f"ReusableAgent '{name}' initialized with model '{kwargs.get('model', 'default')}'. Instructions loaded: {'Yes' if instructions else 'No (using default)'}")

    async def run_agent(self, user_input: str, **kwargs):
        result = await self.runner.run(self, input=user_input, **kwargs)
        return result.final_output

    def run_agent_sync(self, user_input: str, **kwargs):
        result = self.runner.run_sync(self, input=user_input, **kwargs)
        return result.final_output


class IssueTriagerAgent(ReusableAgent):
    """An agent that triages GitHub issues."""
    def __init__(self, **kwargs):
        instructions = load_prompt("issue_triager_agent.md")
        super().__init__(name="IssueTriager", instructions=instructions, tools=[download_github_issue], **kwargs)


class PlannerAgent(ReusableAgent):
    """
    An agent that analyzes a triaged GitHub issue and creates a plan.
    """
    def __init__(self, **kwargs):
        instructions = load_prompt("planner_agent.md")
        super().__init__(name="PlannerAgent", instructions=instructions, **kwargs)


class FileIdentifierAgent(ReusableAgent):
    """An agent that identifies the target file(s) to fix for an issue."""
    def __init__(self, **kwargs):
        instructions = load_prompt("file_identifier_agent.md")
        super().__init__(name="FileIdentifierAgent", instructions=instructions, tools=[list_repository_files], **kwargs)

class CodeProposerAgent(ReusableAgent):
    """An agent that proposes code solutions."""
    def __init__(self, **kwargs):
        instructions = load_prompt("code_proposer_agent.md")
        super().__init__(name="CodeProposer", instructions=instructions, **kwargs)

class ChangeExplainerAgent(ReusableAgent):
    """An agent that explains code changes."""
    def __init__(self, **kwargs):
        instructions = load_prompt("change_explainer_agent.md")
        super().__init__(name="ChangeExplainerAgent", instructions=instructions, **kwargs)


class CodeReviewerAgent(ReusableAgent):
    """
    An agent that reviews proposed file operations.
    """
    def __init__(self, review_aspect: str = "general code quality", **kwargs):
        template = load_prompt("code_reviewer_agent_template.md")
        formatted_instructions = template.format(
            review_aspect=review_aspect,
            review_aspect_capitalized=review_aspect.capitalize()
        )
        super().__init__(
            name=f"{review_aspect.replace(' ', '')}Reviewer",
            instructions=formatted_instructions,
            **kwargs
        )


class CodeCommitterAgent(ReusableAgent):
    """An agent that commits file changes to a branch."""
    def __init__(self, **kwargs):
        instructions = load_prompt("code_committer_agent.md")
        super().__init__(
            name="CodeCommitter",
            instructions=instructions,
            tools=[commit_files_to_branch, delete_file_from_branch],
            **kwargs
        )


class BranchCreatorAgent(ReusableAgent):
    """An agent that creates a branch for a pull request."""
    def __init__(self, **kwargs):
        instructions = load_prompt("branch_creator_agent.md")
        super().__init__(
            name="BranchCreator",
            instructions=instructions,
            tools=[create_pr_branch],
            **kwargs
        )


class CommentPosterAgent(ReusableAgent):
    """An agent that posts comments to GitHub issues."""
    def __init__(self, **kwargs):
        instructions = load_prompt("comment_poster_agent.md")
        super().__init__(name="CommentPoster", instructions=instructions, tools=[post_comment_to_github], **kwargs)
