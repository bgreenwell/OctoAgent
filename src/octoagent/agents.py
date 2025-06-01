"""
Defines the agent classes used in the OctoAgent workflow.
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
    """
    def __init__(self, name: str, instructions: str = "You are a helpful assistant.", **kwargs):
        super().__init__(name=name, instructions=instructions, **kwargs)
        self.runner = Runner()
        logger.debug(f"ReusableAgent '{name}' initialized with model '{kwargs.get('model', 'default')}'.")

    async def run_agent(self, user_input: str, **kwargs):
        result = await self.runner.run(self, input=user_input, **kwargs)
        return result.final_output

    def run_agent_sync(self, user_input: str, **kwargs):
        result = self.runner.run_sync(self, input=user_input, **kwargs)
        return result.final_output


class SpecialistAgent(ReusableAgent):
    """
    An example of a specialized agent that inherits from ReusableAgent.
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
    """
    def __init__(self, **kwargs):
        super().__init__(
            name="PlannerAgent",
            instructions=(
                "You are an expert software project planner. Based on the provided GitHub issue details "
                "(title, body, labels, triage summary), your task is to create a concise, actionable, "
                "step-by-step plan to guide the resolution of this issue. "
                "The plan should focus on the core requirements of the issue. "
                "Prioritize using or modifying EXISTING relevant files before suggesting the creation of new ones, unless a new file is clearly necessary for the primary task (e.g., a new module for a significant new feature).\n"
                "**Regarding Tests:** Do NOT include steps for creating new test files or writing new tests UNLESS the GitHub issue description *explicitly asks for test creation or modification*, OR if existing test files are directly affected by the proposed code changes and thus *must* be updated. If the issue is silent on tests, your plan should also be silent on creating new tests.\n"
                "Outline the logical sequence of actions needed for the core task, considering operations like "
                "file identification (specifying if a file should be checked for existence, modified, or created based on the issue), code changes, file deletions (e.g., for renames implied by the issue), reviews, branching, and committing. "
                "Output the plan as a numbered list."
            ),
            **kwargs
        )


class FileIdentifierAgent(ReusableAgent):
    """An agent that identifies the target file(s) to fix for an issue, considering renames."""
    def __init__(self, **kwargs):
        super().__init__(
            name="FileIdentifierAgent",
            instructions=(
                "You are an expert software architect. Your task is to analyze a GitHub issue, the overall plan, "
                "and the repository's file structure on a specified default branch to identify all relevant file paths for required operations, focusing *only on what the original issue asks for*.\n"
                "You will be provided with: the issue details, the overall plan, and the **Default Branch** name.\n"
                "1. First, use the `list_repository_files` tool with the provided **Default Branch** name to understand the existing file structure.\n"
                "2. Based *primarily on the original GitHub issue description* and then the plan (if it directly supports the issue's core request):\n"
                "   - If the issue requires modifying an existing functionality, identify the existing file path from the tool's output.\n"
                "   - If the issue requires adding a new, distinct feature that logically fits into an existing file, identify that existing file.\n"
                "   - Only if the issue *explicitly states or makes it absolutely necessary* to create a brand new file for a new module to fulfill its primary request, suggest a new file path.\n"
                "   - If a file rename or move is *explicitly requested by the issue*, list BOTH the old path AND the new path.\n"
                "   - **CRITICAL FOR TESTS:** Do NOT suggest creating or modifying test files (e.g., `tests/test_...`) UNLESS the original GitHub issue description *explicitly asks for test creation or modification*, OR if an *existing test file directly conflicts* with the proposed code changes for the primary issue.\n"
                "Output MUST be ONLY a list of actual existing file paths to be modified, or new file paths to be created if explicitly and absolutely necessary based on the issue's core tasks. Each path on a new line. "
                "If no files need changes to address the issue's core request, output the exact string 'None'."
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
                "Your task is to propose all necessary file operations strictly based on the GitHub issue's requirements and the provided plan.\n\n"
                "**VERY IMPORTANT - HOW TO MODIFY EXISTING FILES:**\n"
                "If a file is being modified (meaning 'Original content for `filename.ext`:' is provided and is not empty or indicating a new file):\n"
                "1. You are given the **entire original content** of that file.\n"
                "2. You MUST determine where your new code (e.g., a new function, a fix to an existing line) should be placed within that original content to address the issue.\n"
                "3. Your output for that file MUST be the **ENTIRE, COMPLETE, MODIFIED content of the file.** This means you include ALL of the original, unchanged code, plus your additions/modifications in the correct places. \n"
                "   For example, if original content is `def func_a():\\n  pass` and the issue is 'add func_b', your output for that file should be something like:\n"
                "   `def func_a():\\n  pass\\n\\ndef func_b():\\n  # new code here\\n`\n"
                "   **DO NOT output only the new function or the changed lines. Output the WHOLE file's new content.**\n\n"
                "**For NEW files** (where 'Original content for `filename.ext`:' indicates it's new, AND the FileIdentifierAgent explicitly listed this as a new file path necessary for the issue's core tasks):\n"
                "- Generate the complete initial content for this new file to fulfill the issue's requirements.\n\n"
                "**Assumptions:** If the issue or plan is vague (e.g., 'add a utility function'), make a reasonable, simple choice for the "
                "implementation directly related to the issue's request and **explicitly state your choice and any assumptions made in a section titled 'Assumptions Made:'** "
                "before presenting any file operations.\n\n"
                "**Output Operations (after 'Assumptions Made:' section, if any):**\n"
                "- **To Modify/Create a File:** State 'Changes for `path/to/file.ext`:' "
                "followed by the **ENTIRE NEW FILE CONTENT** (as described above) in a single markdown code block "
                "with the appropriate language identifier.\n"
                "- **To Delete a File (only if explicitly part of a rename described in the issue/plan):** State 'Delete file: `path/to/file.ext`'.\n"
                "- **For No Change:** If a file from the identified list needs no changes for the core issue, state 'No changes needed for `path/to/file.ext`.'\n\n"
                "Ensure your response clearly lists all intended operations for all relevant files. "
                "If revising based on feedback, re-apply the same principles using the original content as your base."
            ),
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
                "- Correctness of deletions or renames in context of the issue and plan (were they explicitly asked for or absolutely necessary for the issue?).\n"
                "- Whether proposed changes correctly integrate with existing code, preserving unrelated functionality.\n"
                "- Completeness of the solution regarding the issue's core requirements.\n"
                "- Potential bugs or edge cases from code changes.\n"
                "- Adherence to coding best practices and style guidelines for the inferred language.\n"
                "- Clarity and maintainability.\n"
                "If all proposed operations are satisfactory in addressing the specific issue, state ONLY 'LGTM!' or 'Satisfactory' or 'Approved'. "
                "If changes are needed for ANY operation (e.g., code issues, unnecessary file creation/deletion like unrequested test files), state 'Needs revision.' as the first part of your response, "
                "then for each operation needing changes, clearly list the file path and the required revisions."
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
