"""
The main entry point and orchestration logic for the OctoAgent application.

This script parses command-line arguments and orchestrates the flow of agents
to triage a GitHub issue, identify a target file, propose a solution,
review the code, and finally commit the fix and post a summary.
"""
import argparse
import asyncio
import os
import sys

from typing import Any, Dict, List, Optional
from agents import Runner, ToolCallItem, ToolCallOutputItem
from .agents import (
    BranchCreatorAgent,
    CodeCommitterAgent,
    CodeProposerAgent,
    CodeReviewerAgent,
    CommentPosterAgent,
    IssueTriagerAgent,
    FileIdentifierAgent,
)
from .github_client import GitHubClient
from .tools import extract_code_from_markdown, parse_github_issue_url


async def solve_github_issue_flow(
    issue_url: str,
    repo_owner_override: Optional[str] = None,
    repo_name_override: Optional[str] = None,
    target_file_override: Optional[str] = None,
):
    """
    Orchestrates the end-to-end flow of agents to solve a GitHub issue.

    This function coordinates a sequence of agents to perform the following steps:
    1.  Triage the issue to understand its details.
    2.  Identify the target file to be fixed (or use a user-provided override).
    3.  Propose a code solution.
    4.  Review the proposed code for technical correctness and style.
    5.  Create a new branch for the fix.
    6.  Commit the finalized code to the new branch.
    7.  Post a summary comment on the original GitHub issue.

    Parameters
    ----------
    issue_url : str
        The full URL of the GitHub issue to be solved.
    repo_owner_override : str, optional
        The GitHub repository owner. If not provided, it's parsed from the URL.
    repo_name_override : str, optional
        The GitHub repository name. If not provided, it's parsed from the URL.
    target_file_override : str, optional
        If provided, this file path will be used directly, skipping the
        FileIdentifierAgent step. Defaults to None.
    """
    print(f"\n🚀 Starting GitHub Issue Solver for: {issue_url}\n" + "=" * 50)

    repo_owner = repo_owner_override
    repo_name = repo_name_override

    if not repo_owner or not repo_name:
        parsed_info = parse_github_issue_url(issue_url)
        if parsed_info:
            repo_owner = repo_owner or parsed_info[0]
            repo_name = repo_name or parsed_info[1]
        else:
            print(
                f"❌ Error: Could not parse repository owner and name from issue URL: {issue_url}"
            )
            return
    print(f"Target Repository: {repo_owner}/{repo_name}")

    github_client = GitHubClient()
    print("📋 Fetching default branch name...")
    default_branch_name = await github_client.get_default_branch(repo_owner, repo_name)
    if not default_branch_name:
        print(f"❌ Error: Could not determine the default branch for {repo_owner}/{repo_name}.")
        return
    print(f"Default branch is '{default_branch_name}'.\n")


    triager = IssueTriagerAgent()
    file_identifier = FileIdentifierAgent()
    code_proposer = CodeProposerAgent()
    technical_reviewer = CodeReviewerAgent(
        review_aspect="technical correctness and efficiency"
    )
    style_reviewer = CodeReviewerAgent(review_aspect="code style and readability")
    branch_creator = BranchCreatorAgent()
    committer = CodeCommitterAgent()
    comment_poster = CommentPosterAgent()
    runner = Runner()

    # --- Step 1: Triaging Issue ---
    print("\n🔍 Step 1: Triaging Issue...")
    triage_result_run = await runner.run(
        triager, input=f"Please triage the GitHub issue at {issue_url}"
    )
    triage_output_summary = triage_result_run.final_output
    print(f"Triager Output Summary:\n{triage_output_summary}\n")

    issue_details_from_tool: Optional[Dict[str, Any]] = None
    tool_call_item_index_for_download = -1

    new_items_triage: Optional[List[Any]] = getattr(triage_result_run, "new_items", None)
    if new_items_triage:
        for i, item in enumerate(new_items_triage):
            item_type_name = type(item).__name__
            is_tool_call_item = (
                (ToolCallItem and isinstance(item, ToolCallItem))
                or (item_type_name == "ToolCallItem")
            )
            if is_tool_call_item:
                raw_item_from_tool_call = getattr(item, "raw_item", None)
                current_tool_id = None
                current_tool_name = None
                if raw_item_from_tool_call:
                    current_tool_id = getattr(raw_item_from_tool_call, "id", None)
                    current_tool_name = getattr(raw_item_from_tool_call, "name", None)
                    if not current_tool_name:
                        tool_function_attr = getattr(
                            raw_item_from_tool_call, "function", None
                        )
                        if tool_function_attr and hasattr(tool_function_attr, "name"):
                            current_tool_name = getattr(tool_function_attr, "name", None)

                if current_tool_name == "download_github_issue" and current_tool_id:
                    tool_call_item_index_for_download = i
                    break

        if tool_call_item_index_for_download != -1 and (
            tool_call_item_index_for_download + 1
        ) < len(new_items_triage):
            output_item_candidate = new_items_triage[
                tool_call_item_index_for_download + 1
            ]
            output_item_type_name = type(output_item_candidate).__name__
            is_tool_call_output_item = (
                ToolCallOutputItem
                and isinstance(output_item_candidate, ToolCallOutputItem)
            ) or (output_item_type_name == "ToolCallOutputItem")
            if is_tool_call_output_item:
                content = getattr(output_item_candidate, "output", None)
                if content is None:
                    content = getattr(output_item_candidate, "content", None)
                if content is None and hasattr(output_item_candidate, "raw_item"):
                    raw_item_of_output = getattr(output_item_candidate, "raw_item")
                    if isinstance(raw_item_of_output, dict):
                        content = raw_item_of_output

                if isinstance(content, dict):
                    issue_details_from_tool = content
                elif isinstance(content, str):
                    try:
                        import json

                        issue_details_from_tool = json.loads(content)
                    except json.JSONDecodeError:
                        issue_details_from_tool = {
                            "error": "Failed to parse tool output JSON",
                            "raw_content": content,
                        }
                else:
                    issue_details_from_tool = {
                        "error": f"Unexpected tool output type: {type(content)}",
                        "raw_content": str(content),
                    }

    if not issue_details_from_tool or "error" in issue_details_from_tool:
        print(
            f"❌ Error: Could not retrieve valid issue details. Details: {issue_details_from_tool}"
        )
        return

    issue_number = issue_details_from_tool.get("number")
    issue_title = issue_details_from_tool.get("title", "Unknown Title")
    issue_body = issue_details_from_tool.get("body", "No body provided.")
    issue_labels_data = issue_details_from_tool.get("labels", [])
    issue_labels = [
        label.get("name") if isinstance(label, dict) else label
        for label in issue_labels_data
        if (isinstance(label, dict) and label.get("name")) or isinstance(label, str)
    ]

    if not issue_number:
        print("❌ Error: Issue number not found in triaged details.")
        return
    print(f"Successfully processed issue #{issue_number}: '{issue_title}'")

    # --- Step 1.5: Identify Target File or Use Override ---
    target_file_path: Optional[str] = None
    if target_file_override:
        target_file_path = target_file_override
        print(f"\n✅ User-specified target file: {target_file_path}. Skipping file identification step.\n")
    else:
        print(f"\n📑 Step 1.5: Identifying Target File for issue #{issue_number}...")
        identifier_input = (
            f"Based on the following GitHub issue, identify the single file that needs to be modified.\n"
            f"Repository: {repo_owner}/{repo_name}\n"
            f"Default Branch: {default_branch_name}\n"
            f"Issue Title: {issue_title}\n"
            f"Issue Body:\n{issue_body}\n\n"
            f"Labels: {', '.join(issue_labels)}\n"
        )
        identifier_run = await runner.run(file_identifier, input=identifier_input)
        target_file_path = identifier_run.final_output.strip()
        print(f"File Identifier Agent identified target file: {target_file_path}\n")

    # CORRECTED a bug here: The check was too strict and did not allow for files in the root directory.
    if not target_file_path:
        print(f"❌ Error: Could not determine a valid target file. Path was: '{target_file_path}'")
        return

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
        print(
            "⚠️ Code Proposer did not provide a usable code block. Cannot proceed with review or commit.\n"
        )
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
            technical_review_run = await runner.run(
                technical_reviewer, input=review_task_input
            )
            tech_feedback = technical_review_run.final_output
            print(f"Technical Reviewer Output:\n{tech_feedback}\n")

            print("🎨 Requesting Style Review...")
            style_review_run = await runner.run(
                style_reviewer, input=review_task_input
            )
            style_feedback = style_review_run.final_output
            print(f"Style Reviewer Output:\n{style_feedback}\n")

            tech_ok = any(
                s in tech_feedback.lower()
                for s in ["lgtm", "satisfactory", "looks good", "approved"]
            )
            style_ok = any(
                s in style_feedback.lower()
                for s in ["lgtm", "satisfactory", "looks good", "approved"]
            )

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
                proposer_run = await runner.run(
                    code_proposer, input=revision_proposer_input
                )
                revised_solution_markdown = proposer_run.final_output
                revised_code = extract_code_from_markdown(revised_solution_markdown)
                print(
                    f"Code Proposer Output (Revised Markdown):\n{revised_solution_markdown}"
                )

                if revised_code and revised_code != final_code_to_commit:
                    final_code_to_commit = revised_code
                    print(
                        f"Updated Code Solution after revision:\n{final_code_to_commit}\n"
                    )
                elif revised_code == final_code_to_commit:
                    print(
                        "Code Proposer returned the same code. Assuming no further changes possible based on feedback."
                    )
                    solution_satisfactory = tech_ok and style_ok
                    break
                else:
                    print(
                        "⚠️ Code Proposer did not provide a new code block in its revision. Using last valid code."
                    )
                    break
            else:
                print(
                    f"⚠️ Maximum review cycles ({max_review_cycles}) reached. Proceeding with the last proposed solution."
                )
                solution_satisfactory = tech_ok and style_ok
    else:
        print(
            "⚠️ No initial code proposed by CodeProposer. Skipping review and commit steps for code."
        )
        final_code_to_commit = None

    # --- Step 3: Creating/Ensuring Branch ---
    print("\n🌿 Step 3: Creating/Ensuring Branch...")
    branch_prefix = "fix"
    if any("enhancement" in label.lower() for label in issue_labels):
        branch_prefix = "feature"
    elif any("chore" in label.lower() for label in issue_labels):
        branch_prefix = "chore"

    target_branch_name_ideal = f"{branch_prefix}/issue-{issue_number}"

    branch_run = await runner.run(
        branch_creator,
        input=f"Ensure branch for {repo_owner}/{repo_name} issue {issue_number}, prefix {branch_prefix}, base {default_branch_name}.",
    )
    branch_agent_summary = branch_run.final_output
    print(f"Branch Creator Agent Output: {branch_agent_summary}\n")

    actual_branch_name_from_tool = target_branch_name_ideal
    branch_op_success = False

    new_items_branch_check = getattr(branch_run, "new_items", None)
    if new_items_branch_check:
        tool_call_item_idx_branch = -1
        for i_br, item_br in enumerate(new_items_branch_check):
            if type(item_br).__name__ == "ToolCallItem":
                raw_item_br = getattr(item_br, "raw_item", None)
                tool_name_br = None
                if raw_item_br:
                    tool_name_br = getattr(raw_item_br, "name", None)
                    if not tool_name_br:
                        func_br = getattr(raw_item_br, "function", None)
                        if func_br:
                            tool_name_br = getattr(func_br, "name", None)
                if tool_name_br == "create_pr_branch":
                    tool_call_item_idx_branch = i_br
                    break

        if tool_call_item_idx_branch != -1 and (
            tool_call_item_idx_branch + 1
        ) < len(new_items_branch_check):
            output_item_branch = new_items_branch_check[
                tool_call_item_idx_branch + 1
            ]
            if type(output_item_branch).__name__ == "ToolCallOutputItem":
                content_br = getattr(
                    output_item_branch, "output", getattr(output_item_branch, "content", None)
                )
                if content_br is None and hasattr(output_item_branch, "raw_item"):
                    raw_br_item = output_item_branch.raw_item
                    if isinstance(raw_br_item, dict):
                        content_br = raw_br_item

                if isinstance(content_br, dict):
                    if "error" not in content_br:
                        branch_op_success = True
                        actual_branch_name_from_tool = content_br.get(
                            "branch_name", target_branch_name_ideal
                        )
                        if (
                            content_br.get("status") == "already_exists"
                            or content_br.get("already_exists") is True
                        ):
                            print(
                                f"INFO: Branch '{actual_branch_name_from_tool}' already exists."
                            )
                        else:
                            print(
                                f"INFO: Branch '{actual_branch_name_from_tool}' creation/check successful."
                            )
                    else:
                        print(
                            f"ERROR: Branch tool reported error: {content_br.get('error')}"
                        )
                        branch_agent_summary = content_br.get(
                            "error", branch_agent_summary
                        )

    final_target_branch = actual_branch_name_from_tool

    # --- Step 4: Committing Code ---
    commit_status = "Commit skipped: No code available or solution not satisfactory."
    if final_code_to_commit:
        if branch_op_success:
            print(f"\n💾 Step 4: Committing Code to branch '{final_target_branch}'...")
            commit_message_text = (
                f"Propose solution for issue #{issue_number}: {issue_title}"
            )
            committer_input = (
                f"Commit the following code to repository {repo_owner}/{repo_name} on branch {final_target_branch}. "
                f"File path: {target_file_path}. Commit message: '{commit_message_text}'\n\n"
                f"Code Content:\n{final_code_to_commit}"
            )
            committer_run = await runner.run(committer, input=committer_input)
            commit_status_agent_summary = committer_run.final_output  # Agent's summary
            print(f"Code Committer Agent Output:\n{commit_status_agent_summary}\n")

            commit_tool_output_content = None
            new_items_commit_check = getattr(committer_run, "new_items", None)
            if new_items_commit_check:
                commit_tool_call_idx = -1
                for i_c, item_c in enumerate(new_items_commit_check):
                    if type(item_c).__name__ == "ToolCallItem":
                        raw_item_c = getattr(item_c, "raw_item", None)
                        tool_name_c = None
                        if raw_item_c:
                            tool_name_c = getattr(raw_item_c, "name", None)
                            if not tool_name_c:
                                func_c = getattr(raw_item_c, "function", None)
                                if func_c:
                                    tool_name_c = getattr(func_c, "name", None)
                        if tool_name_c == "commit_code_to_branch":
                            commit_tool_call_idx = i_c
                            break
                if commit_tool_call_idx != -1 and (commit_tool_call_idx + 1) < len(
                    new_items_commit_check
                ):
                    output_item_commit = new_items_commit_check[
                        commit_tool_call_idx + 1
                    ]
                    if type(output_item_commit).__name__ == "ToolCallOutputItem":
                        commit_tool_output_content = getattr(
                            output_item_commit,
                            "output",
                            getattr(output_item_commit, "content", None),
                        )
                        if commit_tool_output_content is None and hasattr(
                            output_item_commit, "raw_item"
                        ):
                            raw_c_item = output_item_commit.raw_item
                            if isinstance(raw_c_item, dict):
                                commit_tool_output_content = raw_c_item

            if (
                isinstance(commit_tool_output_content, dict)
                and "error" not in commit_tool_output_content
            ):
                commit_status = commit_tool_output_content.get(
                    "message", "Commit successful (details in tool output)."
                )
                print(
                    f"INFO: Commit to '{target_file_path}' on branch '{final_target_branch}' reported by tool: {commit_status}"
                )
                if commit_tool_output_content.get("commit_url"):
                    commit_status += f" View commit: {commit_tool_output_content.get('commit_url')}"
            elif (
                isinstance(commit_tool_output_content, dict)
                and "error" in commit_tool_output_content
            ):
                commit_status = f"Commit failed (tool error): {commit_tool_output_content.get('error')}"
                print(
                    f"ERROR: Commit tool reported error: {commit_tool_output_content.get('error')}"
                )
            else:  # If tool output wasn't clear, rely on agent's summary
                commit_status = commit_status_agent_summary

        else:
            commit_status = f"Commit skipped: Branch '{final_target_branch}' not successfully created or ensured."
            print(f"⚠️ {commit_status}")
    else:
        print(f"⚠️ {commit_status}")

    # --- Step 5: Posting Summary Comment ---
    print("\n💬 Step 5: Posting Summary Comment...")
    summary_comment_parts = [
        f"Automated processing for issue #{issue_number} ('{issue_title}'):"
    ]
    summary_comment_parts.append(f"\n**Triage Summary:**\n{triage_output_summary}")

    if target_file_override:
        summary_comment_parts.append(f"\n**File Identification:**\nUser specified the target file: `{target_file_path}`.")
    else:
        summary_comment_parts.append(f"\n**File Identification:**\nAn agent identified `{target_file_path}` as the target file for the fix.")


    if proposed_solution_markdown:
        summary_comment_parts.append(
            f"\n**Initial Code Proposal Attempt:**\n{proposed_solution_markdown}"
        )
    else:
        summary_comment_parts.append(
            "\n**Initial Code Proposal Attempt:** CodeProposer did not provide an initial solution."
        )

    summary_comment_parts.append(f"\n**Technical Review:**\n{tech_feedback}")
    summary_comment_parts.append(f"\n**Style Review:**\n{style_feedback}")

    if (
        final_code_to_commit
        and final_code_to_commit
        != extract_code_from_markdown(proposed_solution_markdown)
    ):
        summary_comment_parts.append(
            f"\n**Final (Revised) Code Solution (for {target_file_path}):**\n```\n{final_code_to_commit}\n```"
        )
    elif final_code_to_commit:
        summary_comment_parts.append(
            f"\n**Final Code Solution (for {target_file_path}):**\n```\n{final_code_to_commit}\n```"
        )
    else:
        summary_comment_parts.append(
            "\n**Final Code Solution:** No code was finalized for commit."
        )

    if branch_op_success:
        summary_comment_parts.append(
            f"\n**Branch:** `{final_target_branch}` (Created/Ensured)"
        )
        summary_comment_parts.append(
            f"\n**Code Commit Status to '{target_file_path}':**\n{commit_status}"
        )
    else:
        summary_comment_parts.append(
            f"\n**Branch Creation Attempt Summary:** {branch_agent_summary}"
        )
        summary_comment_parts.append(
            f"\n**Code Commit Status:** {commit_status}"
        )  # Reflect commit status even if branch failed

    final_summary_comment = "\n".join(summary_comment_parts)
    comment_run = await runner.run(
        comment_poster,
        input=f"Post the following comment to {issue_url}: \n\n{final_summary_comment}",
    )
    print(f"Comment Poster Agent Output: {comment_run.final_output}\n")

    print("=" * 50 + "\n✅ GitHub Issue Solver Flow Completed!\n")


def main():
    """
    Parses command-line arguments and initiates the issue-solving workflow.
    """
    parser = argparse.ArgumentParser(
        description="Solve a GitHub issue using OctoAgent."
    )
    parser.add_argument(
        "repo_name", help="The name of the repository (e.g., 'octoragent')."
    )
    parser.add_argument("issue_number", type=int, help="The issue number.")
    parser.add_argument(
        "--user_id",
        default="bgreenwell",
        help="The GitHub user ID or organization. Defaults to 'bgreenwell'.",
    )
    parser.add_argument(
        '--target_file',
        '-f',
        default=None,
        help='(Optional) The target file path to fix. If not provided, an agent will identify it.'
    )
    args = parser.parse_args()

    issue_url = (
        f"https://github.com/{args.user_id}/{args.repo_name}/issues/{args.issue_number}"
    )

    if not os.environ.get("GITHUB_TOKEN"):
        print("🚨 WARNING: GITHUB_TOKEN not set.")
    if not os.environ.get("OPENAI_API_KEY"):
        print("🚨 WARNING: OpenAI API key not set.")

    print(f"--- Starting GitHub Issue Solver ---\nTargeting issue: {issue_url}")
    asyncio.run(
        solve_github_issue_flow(
            issue_url=issue_url,
            repo_owner_override=args.user_id,
            repo_name_override=args.repo_name,
            target_file_override=args.target_file,
        )
    )


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(src_dir)

    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    main()