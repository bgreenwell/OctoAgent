"""
Defines the agent classes used in the OctoAgent workflow.

Each agent is a specialized class inheriting from a reusable base agent,
with specific instructions and tools tailored to its role (e.g., triaging
issues, proposing code, reviewing code).
"""
import logging
from agents import Agent as BaseAgent, Runner
from .tools import (
    download_github_issue,
    create_pr_branch,
    post_comment_to_github,
    list_repository_files,
    commit_files_to_branch,
    delete_file_from_branch
    # get_file_content is used by main.py now, not directly by CodeProposerAgent's tools
)

logger = logging.getLogger(__name__)


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
        Additional keyword arguments to pass to the base `agents.Agent` class,
        including `model` if specified.

    Attributes
    ----------
    runner : agents.Runner
        An instance of the agent runner for synchronous and asynchronous execution.
    """
    def __init__(self, name: str, instructions: str = "You are a helpful assistant.", **kwargs):
        super().__init__(name=name, instructions=instructions, **kwargs)
        self.runner = Runner()
        logger.debug(f"ReusableAgent '{name}' initialized with model '{kwargs.get('model', 'default')}'.")

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
        super().__init__(
            name="IssueTriager", 
            instructions="Triage GitHub issues using the download_github_issue tool. Provide a detailed summary including title, URL, author, state, labels, comment count, creation/update dates, a concise description of the issue, your analysis of the issue type (e.g., bug, feature, documentation), and a suggested priority (e.g., Low, Medium, High) with a brief justification.", 
            tools=[download_github_issue], 
            **kwargs
        )


class PlannerAgent(ReusableAgent):
    """
    An agent that analyzes a triaged GitHub issue and creates a
    high-level, step-by-step plan to address it.
    The plan should consider operations like file creation, modification, deletion, and renames.
    """
    def __init__(self, **kwargs):
        super().__init__(
            name="PlannerAgent",
            instructions=(
                "You are an expert software project planner. Based on the provided GitHub issue details "
                "(title, body, labels, triage summary), your task is to create a concise, actionable, "
                "step-by-step plan to guide the resolution of this issue. "
                "The plan should outline the logical sequence of actions needed, considering operations like "
                "file identification, code changes (creations, modifications), file deletions (e.g., for renames), reviews, branching, and committing. "
                "For example: '1. Identify original file `old_path/file.py` and target `new_path/file.py` for rename operation.', "
                "'2. Plan deletion of `old_path/file.py`.', '3. Plan content for `new_path/file.py`.', etc. "
                "Focus on a high-level strategy. Output the plan as a numbered list."
            ),
            **kwargs
        )


class FileIdentifierAgent(ReusableAgent):
    """An agent that identifies the target file(s) to fix for an issue, considering renames."""
    def __init__(self, **kwargs):
        super().__init__(
            name="FileIdentifierAgent",
            instructions=(
                "You are an expert software architect. Your task is to analyze a GitHub issue and the overall plan, "
                "then identify all relevant file paths for the required operations. "
                "Consider creations, modifications, and especially renames or deletions implied by the issue or plan.\n"
                "VERY IMPORTANT: Your output MUST be ONLY a list of file paths, each on a new line. Do NOT include any other text, descriptions, or bullet points. "
                "For example, if 'file1.py' and 'src/file2.py' are identified, your output should be:\n"
                "file1.py\n"
                "src/file2.py\n\n"
                "- For modifications to existing files, list their current paths.\n"
                "- For new files to be created (e.g., for a new feature suggested in the plan, like test files), suggest a suitable new file path.\n"
                "- If a file is to be renamed or moved (e.g., from `old/path.py` to `new/path.py`), "
                "list BOTH the old path (as a source for deletion/reference) AND the new path (as a target for new content).\n"
                "- If a directory rename (e.g. `old_dir/` to `new_dir/`) affects files within, list relevant files using their "
                "OLD paths (e.g., `old_dir/file.py`) if they are sources for deletion/move, and their NEW paths "
                "(e.g., `new_dir/file.py`) if they are targets for new/modified content.\n"
                "If no specific files are involved, output the exact string 'None'."
            ),
            tools=[list_repository_files],
            **kwargs
        )

class CodeProposerAgent(ReusableAgent):
    """An agent that proposes code solutions, expecting original content for modifications,
    and outputting the full modified file content."""
    def __init__(self, **kwargs):
        super().__init__(
            name="CodeProposer",
            instructions=(
                "You are an expert software developer. You will be given GitHub issue details, an overall plan, "
                "a list of relevant file paths, and for each of these files, its **original content** (or a note if it's a new file or content couldn't be fetched).\n"
                "Your task is to propose all necessary file operations.\n\n"
                "**VERY IMPORTANT - HOW TO MODIFY EXISTING FILES:**\n"
                "If a file is being modified (meaning 'Original content for `filename.ext`:' is provided and is not empty or indicating a new file):\n"
                "1. You are given the **entire original content** of that file.\n"
                "2. You MUST determine where your new code (e.g., a new function, a fix to an existing line) should be placed within that original content.\n"
                "3. Your output for that file MUST be the **ENTIRE, COMPLETE, MODIFIED content of the file.** This means you include ALL of the original, unchanged code, plus your additions/modifications in the correct places. \n"
                "   For example, if original content is `def func_a():\\n  pass` and you need to add `func_b`, your output for that file should be something like:\n"
                "   `def func_a():\\n  pass\\n\\ndef func_b():\\n  # new code here\\n`\n"
                "   **DO NOT output only the new function or the changed lines. Output the WHOLE file's new content.**\n\n"
                "**For NEW files** (where 'Original content for `filename.ext`:' indicates it's new or content wasn't available):\n"
                "- Generate the complete initial content for this new file.\n\n"
                "**Assumptions:** If the issue or plan is vague (e.g., 'add a utility function'), make a reasonable, simple choice for the "
                "implementation and **explicitly state your choice and any assumptions made in a section titled 'Assumptions Made:'** "
                "before presenting any file operations.\n\n"
                "**Output Operations (after 'Assumptions Made:' section, if any):**\n"
                "- **To Modify/Create a File:** State 'Changes for `path/to/file.ext`:' "
                "followed by the **ENTIRE NEW FILE CONTENT** (as described above) in a single markdown code block "
                "with the appropriate language identifier.\n"
                "- **To Delete a File:** State 'Delete file: `path/to/file.ext`'. "
                "(Do not provide a code block for deletions).\n"
                "- **For No Change:** If a file needs no changes, state 'No changes needed for `path/to/file.ext`.'\n\n"
                "Ensure your response clearly lists all intended operations for all relevant files. "
                "If revising based on feedback, re-apply the same principles using the original content as your base."
            ),
            # No 'get_file_content' tool as orchestrator provides content
            **kwargs
        )

class ChangeExplainerAgent(ReusableAgent):
    """An agent that explains code changes."""
    def __init__(self, **kwargs):
        super().__init__(
            name="ChangeExplainerAgent",
            instructions=(
                "You are a technical writer AI. Your task is to explain a code change clearly and concisely for a GitHub comment or Pull Request description. "
                "You will be given:\n"
                "1. The original GitHub issue title and body (for context on the *why*).\n"
                "2. The overall plan for the issue (for more context on the *why*).\n"
                "3. The file path that was changed.\n"
                "4. The original code snippet (or 'This is a new file.' if it was a new file creation, or 'Content before deletion was not available or file was new.' if it was a deletion of a possibly new/unfetched file).\n"
                "5. The new code snippet (or 'This file was deleted.' if it was a deletion).\n"
                "Your explanation should briefly describe WHAT was changed (e.g., 'Added a new function `foo` to handle X.', "
                "'Modified the logic in `bar` to correctly process Y.', 'Deleted the unused variable `z`.', 'Created new file `alpha.py` to implement Z functionality.', 'Deleted file `beta.py` as part of refactoring to Z.') "
                "and WHY this change was made, linking it back to the issue requirements or the overall plan. "
                "Focus on the functional impact and intent of the change. Keep the explanation for this single file/operation concise (1-3 sentences). "
                "Output only the explanation text for this specific change."
            ),
            **kwargs
        )


class CodeReviewerAgent(ReusableAgent):
    """
    An agent that reviews proposed file operations (creations, modifications, deletions).
    """
    def __init__(self, review_aspect: str = "general code quality", **kwargs):
        super().__init__(
            name=f"{review_aspect.replace(' ', '')}Reviewer",
            instructions=(
                f"You are a meticulous code reviewer specializing in {review_aspect}. "
                "You will be given GitHub issue details, an overall plan, and a list of proposed file operations "
                "(creations/modifications with code, or deletions). The proposer may have stated some assumptions. "
                "Provide a concise review for EACH proposed operation. Consider the assumptions and focus on: "
                f"- {review_aspect.capitalize()} for any code changes.\n"
                "- Correctness of deletions or renames in context of the issue and plan.\n"
                "- Whether proposed changes correctly integrate with existing code, preserving unrelated functionality.\n"
                "- Completeness of the solution regarding the issue and its intended files.\n"
                "- Potential bugs or edge cases from code changes.\n"
                "- Adherence to coding best practices and style guidelines for the inferred language.\n"
                "- Clarity and maintainability.\n"
                "If all proposed operations are satisfactory, state ONLY 'LGTM!' or 'Satisfactory' or 'Approved'. "
                "If changes are needed for ANY operation, state 'Needs revision.' as the first part of your response, "
                "then for each operation needing changes, clearly list the file path and the required revisions "
                "(e.g., for code changes, or if a deletion is inappropriate/missing, or if an assumption made by the proposer is incorrect)."
            ),
            **kwargs
        )

class CodeCommitterAgent(ReusableAgent):
    """An agent that commits file changes (creations, updates, deletions) to a branch."""
    def __init__(self, **kwargs):
        super().__init__(
            name="CodeCommitter",
            instructions=(
                "You are a Git assistant. Your task is to apply a list of file operations to a specified branch. "
                "You will receive the repository owner, repository name, branch name, a base commit message, "
                "and a list of file operations. Each operation will specify a 'file_path', an 'action' "
                "('modify', 'create', 'delete'), and 'file_content' (if action is 'modify' or 'create').\n"
                "- For 'delete' actions, use the `delete_file_from_branch` tool for each specific file. Provide a commit message like '[Base Commit Message] - delete old_file.py'.\n"
                "- For 'modify' or 'create' actions, use the `commit_files_to_branch` tool. You can batch "
                "multiple creations/modifications into a single call to this tool if they share the same base commit message, or commit them one by one. The tool itself handles per-file commit messages if batching.\n"
                "Perform deletions before creations/modifications if they involve a rename (e.g., delete an old path then create/modify a new path). "
                "Summarize the result of all commit/deletion attempts based on the tools' outputs."
            ),
            tools=[commit_files_to_branch, delete_file_from_branch],
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
