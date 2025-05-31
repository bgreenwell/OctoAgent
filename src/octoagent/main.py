import argparse
import asyncio
import os
import sys
import re
import json
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
    PlannerAgent,
)
from .github_client import GitHubClient
from .tools import extract_code_from_markdown, parse_github_issue_url


def parse_file_operations(markdown_text: Optional[str]) -> List[Dict[str, str]]:
    """
    Parses the CodeProposerAgent's output for multiple file operations.

    Expected formats:
    Changes for `path/to/file1.py`:
    (optional intermediary text)
    ```python
    # code for file1
    ```
    Delete file: `path/to/file2.py`
    No changes needed for `path/to/file3.py`.

    Returns
    -------
    list of dict
        Each dict contains "file_path", "action" ('modify', 'delete', 'no_change'),
        and "code" (if action is 'modify').
    """
    if not markdown_text:
        return []

    operations = []
    # Regex for modifications/creations - updated to allow text between header and code block
    modify_pattern = re.compile(
        r"(?:### )?Changes for `([^`]+?\.[\w./-]+)`:.*?\s*```(?:[a-zA-Z0-9\+\-\#\.]*?)?\s*\n(.*?)\n```",
        re.DOTALL | re.MULTILINE
    )
    # Regex for deletions
    delete_pattern = re.compile(
        r"Delete file: `([^`]+?\.[\w./-]+)`",
        re.MULTILINE
    )
    # Regex for no changes
    no_change_pattern = re.compile(
        r"No changes needed for `([^`]+?\.[\w./-]+)`\.",
        re.MULTILINE
    )

    all_matches = []
    for match_type, pattern_obj in [("modify", modify_pattern), ("delete", delete_pattern), ("no_change", no_change_pattern)]:
        for match in pattern_obj.finditer(markdown_text):
            all_matches.append({"type": match_type, "match_obj": match, "start_pos": match.start()})

    all_matches.sort(key=lambda x: x["start_pos"])

    for item in all_matches:
        match_type = item["type"]
        match_obj = item["match_obj"]
        if match_type == "modify":
            file_path, code_content = match_obj.groups()
            operations.append({
                "file_path": file_path.strip(),
                "code": code_content.strip(),
                "action": "modify"
            })
        elif match_type == "delete":
            file_path = match_obj.groups()[0]
            operations.append({
                "file_path": file_path.strip(),
                "action": "delete"
            })
        elif match_type == "no_change":
            file_path = match_obj.groups()[0]
            operations.append({
                "file_path": file_path.strip(),
                "action": "no_change"
            })
    return operations


async def solve_github_issue_flow(
    issue_url: str,
    repo_owner_override: Optional[str] = None,
    repo_name_override: Optional[str] = None,
    target_file_override: Optional[str] = None,
    max_review_cycles_override: int = 3,
):
    """
    Orchestrates the end-to-end flow of agents to solve a GitHub issue.
    Now handles multiple file identification, proposal, review, and commit.

    Parameters
    ----------
    issue_url : str
        The full URL of the GitHub issue to be solved.
    repo_owner_override : str, optional
        The GitHub repository owner. If not provided, it's parsed from the URL.
    repo_name_override : str, optional
        The GitHub repository name. If not provided, it's parsed from the URL.
    target_file_override : str, optional
        If provided, this comma-separated string of file paths will be used
        directly, skipping the FileIdentifierAgent step. Defaults to None.
    max_review_cycles_override : int, optional
        The maximum number of review cycles for code proposals. Defaults to 3.
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
            print(f"❌ Error: Could not parse repository owner and name from issue URL: {issue_url}")
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
    planner = PlannerAgent()
    file_identifier = FileIdentifierAgent()
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
    
    issue_details_from_tool: Optional[Dict[str, Any]] = None
    new_items_triage: Optional[List[Any]] = getattr(triage_result_run, "new_items", None)
    if new_items_triage:
        for item in new_items_triage:
            if type(item).__name__ == 'ToolCallOutputItem':
                content = getattr(item, 'output', getattr(item, 'content', None))
                if isinstance(content, dict) and 'number' in content:
                    issue_details_from_tool = content
                    break
    if not issue_details_from_tool:
        print(f"❌ Error: Could not get issue details from triage step. Last agent output: {triage_output_summary}")
        return

    issue_number = issue_details_from_tool.get("number")
    issue_title = issue_details_from_tool.get("title", "Unknown Title")
    issue_body = issue_details_from_tool.get("body", "No body provided.")
    issue_labels_data = issue_details_from_tool.get("labels", [])
    issue_labels = [label.get("name") if isinstance(label, dict) else label for label in issue_labels_data if (isinstance(label, dict) and label.get("name")) or isinstance(label, str)]

    if not issue_number:
        print("❌ Error: Issue number not found in triaged details.")
        return
    print(f"Triager Output Summary:\n{triage_output_summary}\n")
    print(f"Successfully processed issue #{issue_number}: '{issue_title}'")

    # --- Step 1.2: Generating Plan ---
    print(f"\n📝 Step 1.2: Generating Plan for issue #{issue_number}...")
    planner_input = (
        f"Based on the following triaged GitHub issue, create a step-by-step plan for resolution:\n"
        f"Issue Title: {issue_title}\n"
        f"Issue Body:\n{issue_body}\n\n"
        f"Labels: {', '.join(issue_labels)}\n"
        f"Triage Summary:\n{triage_output_summary}\n"
    )
    planner_run = await runner.run(planner, input=planner_input)
    generated_plan = planner_run.final_output
    print(f"Generated Plan:\n{generated_plan}\n")

    # --- Step 1.5: Identify Target Files or Use Override ---
    identified_file_paths: List[str] = []
    if target_file_override:
        identified_file_paths = [f.strip() for f in target_file_override.split(',') if f.strip()]
        print(f"\n✅ User-specified target file(s): {', '.join(identified_file_paths)}. Skipping file identification step.\n")
    else:
        print(f"\n📑 Step 1.5: Identifying Target Files for issue #{issue_number}...")
        identifier_input = (
            f"Based on the following GitHub issue, identify the file(s) that need to be modified, created, or are relevant to a rename/delete operation.\n"
            f"Repository: {repo_owner}/{repo_name}\n"
            f"Default Branch: {default_branch_name}\n"
            f"Issue Title: {issue_title}\n"
            f"Issue Body:\n{issue_body}\n\n"
            f"Labels: {', '.join(issue_labels)}\n"
            f"Overall Plan: {generated_plan}\n"
        )
        identifier_run = await runner.run(file_identifier, input=identifier_input)
        file_output = identifier_run.final_output.strip()
        if file_output.lower() != 'none':
            identified_file_paths = [path.strip() for path in file_output.split('\n') if path.strip()]
        print(f"File Identifier Agent identified target file(s): {', '.join(identified_file_paths) if identified_file_paths else 'None'}\n")

    if not identified_file_paths:
        print(f"ℹ️ No target files identified or specified by user. Assuming issue does not require code changes or cannot be addressed by file modification.")

    # --- Step 2: Propose Initial File Operations ---
    current_proposed_operations: List[Dict[str, str]] = []
    if identified_file_paths:
        print(f"\n💡 Step 2: Proposing Initial File Operations for issue #{issue_number} for files: {', '.join(identified_file_paths)}...")
        proposer_input = (
            f"Based on the following GitHub issue, overall plan, and list of target files, "
            f"please propose all necessary file operations (creations, modifications, deletions for renames).\n"
            f"Overall Plan:\n{generated_plan}\n\n"
            f"Issue Title: {issue_title}\n"
            f"Issue Body:\n{issue_body}\n\n"
            f"Labels: {', '.join(issue_labels)}\n"
            f"Relevant File Paths Identified: {', '.join(identified_file_paths)}\n"
            "For each operation:\n"
            "- If creating or modifying a file: State 'Changes for `path/to/file.ext`:' followed by the code in a markdown block.\n"
            "- If deleting a file: State 'Delete file: `path/to/file.ext`'.\n"
            "- If a file from the identified list needs no changes: State 'No changes needed for `path/to/file.ext`.'."
            "If the issue is vague (e.g., 'add a math function'), make a reasonable choice for a simple implementation "
            "(e.g., an `exponentiation` function if `calculator.py` is targeted, or a simple `return \"Good morning!\"` "
            "if `greeter.py` is targeted). Clearly state any assumptions made."
        )
        proposer_run = await runner.run(code_proposer, input=proposer_input)
        proposed_solution_markdown = proposer_run.final_output
        
        print(f"DEBUG: Code Proposer Raw Output:\n---\n{proposed_solution_markdown}\n---\n")

        current_proposed_operations = parse_file_operations(proposed_solution_markdown)
        print(f"Code Proposer Output (Parsed Operations):")
        if current_proposed_operations:
            for op in current_proposed_operations:
                print(f"  File: {op['file_path']}, Action: {op.get('action')}")
                if op.get('action') == 'modify':
                    print(f"    Code (first 100 chars):\n{op['code'][:100]}...\n")
        else:
             print("⚠️ Code Proposer did not provide usable changes (output was empty or not parsable).\n")
    else:
        print("⚠️ No files identified for proposal. Skipping code proposal and review steps.\n")

    # --- Step 2.5: Review and Revision Loop ---
    max_review_cycles = max_review_cycles_override
    final_operations_to_commit: List[Dict[str, str]] = []
    tech_feedback = "N/A (No operations to review)" 
    style_feedback = "N/A (No operations to review)"
    
    if current_proposed_operations and any(p.get('action') == 'modify' or p.get('action') == 'delete' for p in current_proposed_operations) :
        temp_proposed_operations = current_proposed_operations
        for cycle in range(max_review_cycles):
            print(f"\n🔄 Review Cycle {cycle + 1}/{max_review_cycles} 🔄")
            
            review_input_parts = [
                f"Issue Title: {issue_title}\nIssue Number: {issue_number}\nIssue Body:\n{issue_body}\n",
                f"Labels: {', '.join(issue_labels)}\n",
                f"Overall Plan:\n{generated_plan}\n\nProposed File Operations:"
            ]
            has_operations_to_review = False
            for op in temp_proposed_operations:
                if op.get('action') == 'modify':
                    review_input_parts.append(f"\n--- Modify/Create File: `{op['file_path']}` ---\n```\n{op['code']}\n```")
                    has_operations_to_review = True
                elif op.get('action') == 'delete':
                    review_input_parts.append(f"\n--- Delete File: `{op['file_path']}` ---")
                    has_operations_to_review = True
                else: 
                    review_input_parts.append(f"\n--- File: `{op['file_path']}` ---\nNo changes proposed.")
            
            if not has_operations_to_review:
                print("ℹ️ No actual modifications or deletions proposed for review in this cycle.")
                final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']
                break

            review_task_input = "\n".join(review_input_parts)

            print("🕵️‍♂️ Requesting Technical Review...")
            technical_review_run = await runner.run(technical_reviewer, input=review_task_input)
            tech_feedback = technical_review_run.final_output
            print(f"Technical Reviewer Output:\n{tech_feedback}\n")

            print("🎨 Requesting Style Review...")
            style_review_run = await runner.run(style_reviewer, input=review_task_input)
            style_feedback = style_review_run.final_output
            print(f"Style Reviewer Output:\n{style_feedback}\n")

            tech_ok = any(s in tech_feedback.lower() for s in ["lgtm", "satisfactory", "approved"])
            style_ok = any(s in style_feedback.lower() for s in ["lgtm", "satisfactory", "approved"])

            if tech_ok and style_ok:
                print("✅ Both reviewers are satisfied. Proceeding with the current operations.")
                final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']
                break
            
            if cycle < max_review_cycles - 1:
                print("⚠️ Revision needed. Requesting CodeProposer to revise...")
                revision_proposer_input_parts = [
                    f"The following file operations for GitHub issue #{issue_number} ('{issue_title}') received feedback.",
                    f"Overall Plan:\n{generated_plan}\n",
                    "Current Proposed Operations:"
                ]
                for op in temp_proposed_operations:
                     if op.get('action') == 'modify':
                        revision_proposer_input_parts.append(f"\n--- File: `{op['file_path']}` (Modify/Create) ---\n```\n{op['code']}\n```")
                     elif op.get('action') == 'delete':
                        revision_proposer_input_parts.append(f"\n--- File: `{op['file_path']}` (Delete) ---")
                     else:
                         revision_proposer_input_parts.append(f"\n--- File: `{op['file_path']}` (No Changes) ---")

                revision_proposer_input_parts.append(f"\nFeedback:\nTechnical Review: {tech_feedback}\nStyle Review: {style_feedback}\n")
                revision_proposer_input_parts.append(
                    "Please provide a revised set of file operations. "
                    "For each operation:\n"
                    "- If creating or modifying a file: State 'Changes for `path/to/file.ext`:' followed by the code.\n"
                    "- If deleting a file: State 'Delete file: `path/to/file.ext`'.\n"
                    "- If a file no longer needs changes: State 'No changes needed for `path/to/file.ext`.'."
                    "If the issue is vague (e.g., 'add a math function'), make a reasonable choice for a simple implementation. Clearly state any assumptions made."
                )
                proposer_run = await runner.run(code_proposer, input="\n".join(revision_proposer_input_parts))
                revised_solution_markdown = proposer_run.final_output
                print(f"DEBUG: Code Proposer Revised Raw Output:\n---\n{revised_solution_markdown}\n---\n")
                revised_operations = parse_file_operations(revised_solution_markdown)
                
                if revised_operations:
                    temp_proposed_operations = revised_operations
                    print(f"Updated File Operations after revision (Parsed):")
                    for op_rev in temp_proposed_operations: print(f"  File: {op_rev['file_path']}, Action: {op_rev.get('action')}")
                else:
                    print("⚠️ Code Proposer did not provide a new set of operations in its revision. Using last valid proposals.")
                    final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']
                    break
            else:
                print(f"⚠️ Maximum review cycles ({max_review_cycles}) reached. Proceeding with the last proposed operations.")
                final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']
                break
        
        if not final_operations_to_commit and temp_proposed_operations and any(p.get('action') != 'no_change' for p in temp_proposed_operations):
            print("⚠️ Review cycles completed, but solution not fully approved. Committing last valid operations with modifications or deletions.")
            final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']

    elif current_proposed_operations: 
        print("ℹ️ No actual modifications or deletions were proposed (e.g., all 'no_change'). Skipping review loop.")
        final_operations_to_commit = []
    else:
        print("⚠️ No file operations proposed. Skipping review and commit steps for code.")
        final_operations_to_commit = []

    # --- Step 3: Creating/Ensuring Branch ---
    print("\n🌿 Step 3: Creating/Ensuring Branch...")
    branch_prefix = "fix"
    if any("enhancement" in label.lower() for label in issue_labels): branch_prefix = "feature"
    elif any("chore" in label.lower() for label in issue_labels): branch_prefix = "chore"
    target_branch_name_ideal = f"{branch_prefix}/issue-{issue_number}"
    branch_run = await runner.run(branch_creator, input=f"Ensure branch for {repo_owner}/{repo_name} issue {issue_number}, prefix {branch_prefix}, base {default_branch_name}.")
    branch_agent_summary = branch_run.final_output
    
    actual_branch_name_from_tool = target_branch_name_ideal
    branch_op_success = False
    new_items_branch_check = getattr(branch_run, 'new_items', None)
    if new_items_branch_check:
        for i_br, item_br in enumerate(new_items_branch_check):
            if type(item_br).__name__ == 'ToolCallOutputItem': 
                content_br = getattr(item_br, 'output', getattr(item_br, 'content', None))
                if content_br is None and hasattr(item_br, 'raw_item'):
                    raw_br_item = item_br.raw_item
                    if isinstance(raw_br_item, dict): content_br = raw_br_item
                
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
                        branch_agent_summary = content_br.get('error', branch_agent_summary) + f" (Tool Output: {content_br})"
                break 
    
    if not branch_op_success:
        print(f"Branch Creator Agent Output (Summary): {branch_agent_summary}\n")
        if "error" not in branch_agent_summary.lower() and ("created" in branch_agent_summary.lower() or "exists" in branch_agent_summary.lower() or "successful" in branch_agent_summary.lower()):
            match_bn = re.search(r"(?:branch|')\s*`?([^'`]+)`?\s*(?:has been successfully created|already exists|creation/check successful)", branch_agent_summary, re.IGNORECASE)
            if match_bn: actual_branch_name_from_tool = match_bn.group(1)
            branch_op_success = True
            print(f"Branch operation likely successful based on summary. Target branch: {actual_branch_name_from_tool}")
        else:
             print(f"❌ Branch operation failed or status unclear based on summary.")
    
    final_target_branch = actual_branch_name_from_tool

    # --- Step 4: Committing Code ---
    commit_status_summary = "Commit skipped: No operations to commit or branch operation failed."
    if final_operations_to_commit and branch_op_success:
        print(f"\n💾 Step 4: Applying File Operations to branch '{final_target_branch}'...")
        commit_message_base = f"Fix issue #{issue_number}: {issue_title}"
        
        committer_input_payload = {
            "repo_owner": repo_owner,
            "repo_name": repo_name,
            "branch_name": final_target_branch,
            "commit_message_base": commit_message_base,
            "operations": final_operations_to_commit
        }
        committer_input_str = (
            f"Apply the following file operations to repository {repo_owner}/{repo_name} on branch {final_target_branch}. "
            f"Base commit message: '{commit_message_base}'.\n\n"
            f"Operations: {json.dumps(committer_input_payload['operations'])}"
        )
        committer_run = await runner.run(committer, input=committer_input_str)
        commit_status_summary = committer_run.final_output
        print(f"Code Committer Agent Output:\n{commit_status_summary}\n")
    elif not final_operations_to_commit:
         commit_status_summary = "Commit skipped: No approved file operations to commit."
         print(f"⚠️ {commit_status_summary}")
    else:
        commit_status_summary = f"Commit skipped due to branch operation failure ({branch_agent_summary})."
        print(f"⚠️ {commit_status_summary}")


    # --- Step 5: Posting Summary Comment ---
    print("\n💬 Step 5: Posting Summary Comment...")
    summary_comment_parts = [
        f"🤖 **OctoAgent Report** for Issue #{issue_number}: {issue_title}"
    ]
    summary_comment_parts.append(f"\n**Triage Summary:**\n{triage_output_summary}")
    summary_comment_parts.append(f"\n**Generated Plan:**\n{generated_plan}")

    if target_file_override:
        summary_comment_parts.append(f"\n**File Identification:**\nUser specified target file(s): `{', '.join(identified_file_paths)}`.")
    elif identified_file_paths:
        summary_comment_parts.append(f"\n**File Identification:**\nAgent identified target file(s): `{', '.join(identified_file_paths)}`.")
    else:
        summary_comment_parts.append(f"\n**File Identification:**\nNo specific files were identified for modification.")

    if final_operations_to_commit:
        summary_comment_parts.append(f"\n**Finalized File Operations:**")
        for op in final_operations_to_commit:
            if op.get('action') == 'modify':
                summary_comment_parts.append(f"\n*Modify/Create file `{op['file_path']}`:*\n```\n{op['code']}\n```")
            elif op.get('action') == 'delete':
                 summary_comment_parts.append(f"\n*Delete file `{op['file_path']}`*")
    elif current_proposed_operations and any(p.get('action') != 'no_change' for p in current_proposed_operations):
         summary_comment_parts.append(f"\n**Code Proposal Attempt:**\nOperations were proposed (see logs for details) but not finalized/approved after review.")
    else:
        summary_comment_parts.append(f"\n**Code Proposal:** No file operations were proposed or committed.")

    summary_comment_parts.append(f"\n**Technical Review:**\n{tech_feedback}")
    summary_comment_parts.append(f"\n**Style Review:**\n{style_feedback}")

    if branch_op_success:
        summary_comment_parts.append(f"\n**Branch:** `{final_target_branch}` (Created/Ensured)")
        summary_comment_parts.append(f"\n**Commit Status:**\n{commit_status_summary}")
    else:
        summary_comment_parts.append(f"\n**Branch Creation Attempt Summary:** {branch_agent_summary}")
        summary_comment_parts.append(f"\n**Commit Status:** {commit_status_summary}")

    summary_comment_parts.append("\n---\n*This comment was automatically generated by OctoAgent, an experimental AI-powered issue-solving assistant.*")

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
        help='(Optional) Comma-separated list of target file paths to fix. If not provided, an agent will identify them.'
    )
    parser.add_argument(
        '--max_review_cycles',
        type=int,
        default=3,
        help='The maximum number of review cycles for code proposals. Defaults to 3.'
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
            max_review_cycles_override=args.max_review_cycles,
        )
    )


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(src_dir)

    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    main()
    