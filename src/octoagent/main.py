import argparse
import asyncio
import os
import sys
import re
import json
from typing import Any, Dict, List, Optional
import logging

# Logger for this module
logger = logging.getLogger(__name__)

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
    ChangeExplainerAgent
)
from .github_client import GitHubClient
from .tools import parse_github_issue_url


def parse_file_operations(markdown_text: Optional[str]) -> List[Dict[str, str]]:
    """
    Parses the CodeProposerAgent's output for multiple file operations.

    Expected formats:
    Changes for `path/to/file1.py`: or ### Changes for `path/to/file1.py`:
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
    modify_pattern = re.compile(
        r"(?:### )?Changes for `([^`]+?\.[\w./-]+)`:.*?\s*```(?:[a-zA-Z0-9\+\-\#\.]*?)?\s*\n(.*?)\n```",
        re.DOTALL | re.MULTILINE
    )
    delete_pattern = re.compile(
        r"Delete file: `([^`]+?\.[\w./-]+)`",
        re.MULTILINE
    )
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
    show_token_summary: bool = True,
    model_to_use: str = "gpt-4o",
):
    """
    Orchestrates the end-to-end flow of agents to solve a GitHub issue.
    """
    logger.info(f"🚀 Starting GitHub Issue Solver for: {issue_url}")
    logger.info(f"Using model for agent instantiations: {model_to_use}")

    total_prompt_tokens = 0
    total_completion_tokens = 0
    actual_model_name_reported = model_to_use 

    runner = Runner()

    async def run_agent_and_track_usage(agent_instance, input_text, **kwargs):
        nonlocal total_prompt_tokens, total_completion_tokens, actual_model_name_reported
        agent_name_for_log = agent_instance.name if hasattr(agent_instance, 'name') else "UnknownAgent"
        logger.debug(f"Running agent: {agent_name_for_log}, Input (first 100 chars): {input_text[:100]}...")
        run_result = await runner.run(agent_instance, input=input_text, **kwargs)
        
        logger.debug(f"[{agent_name_for_log}] --- RunResult Details ---")
        logger.debug(f"[{agent_name_for_log}] type(run_result): {type(run_result)}")
        try:
            logger.debug(f"[{agent_name_for_log}] dir(run_result): {dir(run_result)}")
        except Exception as e:
            logger.debug(f"[{agent_name_for_log}] Error inspecting run_result with dir(): {e}")

        found_tokens_for_this_run = False
        direct_model_from_response = None
        
        if hasattr(run_result, 'raw_responses') and run_result.raw_responses:
            logger.debug(f"[{agent_name_for_log}] Found 'raw_responses' on RunResult. Count: {len(run_result.raw_responses)}")
            for i, model_response_item in enumerate(run_result.raw_responses):
                logger.debug(f"[{agent_name_for_log}] Processing raw_responses[{i}], type: {type(model_response_item)}")
                if hasattr(model_response_item, 'usage') and model_response_item.usage is not None:
                    usage_stats = model_response_item.usage
                    logger.debug(f"[{agent_name_for_log}] Found usage object on raw_responses[{i}].usage: {usage_stats}, type: {type(usage_stats)}")
                    
                    if hasattr(usage_stats, 'input_tokens') and hasattr(usage_stats, 'output_tokens'):
                        prompt_t = int(getattr(usage_stats, 'input_tokens', 0))
                        completion_t = int(getattr(usage_stats, 'output_tokens', 0))
                        
                        logger.debug(f"[{agent_name_for_log}] Tokens from raw_responses[{i}].usage: Prompt={prompt_t}, Completion={completion_t}")
                        if prompt_t > 0 or completion_t > 0:
                            total_prompt_tokens += prompt_t
                            total_completion_tokens += completion_t
                            found_tokens_for_this_run = True
                            
                        model_from_this_item_output = None
                        if hasattr(model_response_item, 'output'):
                            output_obj = model_response_item.output
                            if hasattr(output_obj, 'model') and isinstance(output_obj.model, str):
                                model_from_this_item_output = output_obj.model
                            elif isinstance(output_obj, dict) and output_obj.get('model'):
                                model_from_this_item_output = output_obj.get('model')
                        
                        if model_from_this_item_output and isinstance(model_from_this_item_output, str) and \
                           (actual_model_name_reported == model_to_use or actual_model_name_reported == "Unknown" or actual_model_name_reported != model_from_this_item_output):
                            actual_model_name_reported = model_from_this_item_output
                            logger.debug(f"[{agent_name_for_log}] Updated actual_model_name_reported to '{actual_model_name_reported}' from ModelResponse item's output.")
                        
                        if found_tokens_for_this_run: 
                            break 
                    else:
                        logger.debug(f"[{agent_name_for_log}] raw_responses[{i}].usage object missing input_tokens or output_tokens attributes.")
            if not found_tokens_for_this_run:
                 logger.debug(f"[{agent_name_for_log}] Iterated through raw_responses, but no parsable token usage data found on .usage attributes.")
        else:
            logger.debug(f"[{agent_name_for_log}] No 'raw_responses' attribute found on RunResult, or it was empty.")
        
        if not found_tokens_for_this_run: # Fallback check
            if hasattr(run_result, 'input_tokens') and hasattr(run_result, 'output_tokens'):
                prompt_t = int(getattr(run_result, 'input_tokens', 0))
                completion_t = int(getattr(run_result, 'output_tokens', 0))
                logger.debug(f"[{agent_name_for_log}] Found tokens on RunResult directly (Usage dataclass attributes): P={prompt_t}, C={completion_t}")
                if isinstance(prompt_t, int) and isinstance(completion_t, int) and (prompt_t > 0 or completion_t > 0):
                    total_prompt_tokens += prompt_t
                    total_completion_tokens += completion_t
                    found_tokens_for_this_run = True
                    model_attr_on_runresult = getattr(run_result, 'model', getattr(run_result, 'model_name', None))
                    if isinstance(model_attr_on_runresult, str) and (actual_model_name_reported == model_to_use or actual_model_name_reported == "Unknown"):
                        actual_model_name_reported = model_attr_on_runresult
                        logger.debug(f"[{agent_name_for_log}] Model from RunResult direct attribute: {actual_model_name_reported}")
            else:
                logger.debug(f"[{agent_name_for_log}] RunResult does not have direct input_tokens/output_tokens attributes.")

        if (actual_model_name_reported == model_to_use or actual_model_name_reported == "Unknown") and \
            (direct_model_from_response is None) and \
            hasattr(run_result, 'new_items') and run_result.new_items:
            for item in run_result.new_items: 
                model_from_item_raw = None
                if hasattr(item, 'raw_item'):
                    raw_item_obj = getattr(item, 'raw_item')
                    if hasattr(raw_item_obj, 'model') and isinstance(getattr(raw_item_obj, 'model'), str):
                        model_from_item_raw = getattr(raw_item_obj, 'model')
                    elif isinstance(raw_item_obj, dict):
                        model_from_item_raw = raw_item_obj.get('model')
                if model_from_item_raw and isinstance(model_from_item_raw, str):
                    actual_model_name_reported = model_from_item_raw
                    logger.debug(f"[{agent_name_for_log}] Updated actual_model_name_reported from item.raw_item (fallback) to '{actual_model_name_reported}'")
                    break
        
        if not found_tokens_for_this_run:
            logger.debug(f"[{agent_name_for_log}] Ultimately, no token usage data was successfully extracted for this agent run.")
            
        return run_result

    repo_owner = repo_owner_override
    repo_name = repo_name_override

    if not repo_owner or not repo_name:
        parsed_info = parse_github_issue_url(issue_url)
        if parsed_info:
            repo_owner = repo_owner or parsed_info[0]
            repo_name = repo_name or parsed_info[1]
        else:
            logger.error(f"Could not parse repository owner and name from issue URL: {issue_url}")
            return
    logger.info(f"Target Repository: {repo_owner}/{repo_name}")

    github_client = GitHubClient()
    logger.info("📋 Fetching default branch name...")
    default_branch_name = await github_client.get_default_branch(repo_owner, repo_name)
    if not default_branch_name:
        logger.error(f"Could not determine the default branch for {repo_owner}/{repo_name}.")
        if show_token_summary:
            overall_total_tokens_err = total_prompt_tokens + total_completion_tokens
            logger.info("\n--- Token Usage Summary (Partial) ---")
            logger.info(f"Model Used: {actual_model_name_reported}")
            logger.info(f"Total Prompt Tokens: {total_prompt_tokens}")
            logger.info(f"Total Completion Tokens: {total_completion_tokens}")
            logger.info(f"Overall Total Tokens: {overall_total_tokens_err}")
            logger.info("-------------------------------------\n")
        return
    logger.info(f"Default branch is '{default_branch_name}'.\n")

    triager = IssueTriagerAgent(model=model_to_use)
    planner = PlannerAgent(model=model_to_use)
    file_identifier = FileIdentifierAgent(model=model_to_use)
    code_proposer = CodeProposerAgent(model=model_to_use)
    change_explainer = ChangeExplainerAgent(model=model_to_use)
    technical_reviewer = CodeReviewerAgent(model=model_to_use, review_aspect="technical correctness and efficiency")
    style_reviewer = CodeReviewerAgent(model=model_to_use, review_aspect="code style and readability")
    branch_creator = BranchCreatorAgent(model=model_to_use)
    committer = CodeCommitterAgent(model=model_to_use)
    comment_poster = CommentPosterAgent(model=model_to_use)
    
    # --- Step 1: Triaging Issue ---
    logger.info("\n🔍 Step 1: Triaging Issue...")
    triage_result_run = await run_agent_and_track_usage(triager, f"Please triage the GitHub issue at {issue_url}")
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
        model_response_item_content = None
        if new_items_triage:
            for item in new_items_triage:
                if hasattr(item, 'raw_item') and isinstance(getattr(item, 'raw_item'), dict):
                    raw_item_dict = getattr(item, 'raw_item')
                    if 'choices' in raw_item_dict and isinstance(raw_item_dict['choices'], list) and raw_item_dict['choices']:
                        message_content_str = raw_item_dict['choices'][0].get('message', {}).get('content')
                        if message_content_str:
                            try: 
                                model_response_item_content = json.loads(message_content_str)
                                if isinstance(model_response_item_content, dict) and 'number' in model_response_item_content:
                                    issue_details_from_tool = model_response_item_content
                                    break
                            except json.JSONDecodeError: pass 
        if not issue_details_from_tool:
            try: 
                potential_details = json.loads(triage_output_summary) 
                if isinstance(potential_details, dict) and 'number' in potential_details:
                    issue_details_from_tool = potential_details
            except (json.JSONDecodeError, TypeError):
                logger.error(f"Could not get structured issue details from triage step. Last agent output: {triage_output_summary}")
                if show_token_summary: 
                    overall_total_tokens_err = total_prompt_tokens + total_completion_tokens
                    logger.info("\n--- Token Usage Summary (Partial) ---") 
                    logger.info(f"Model Used: {actual_model_name_reported}")
                    logger.info(f"Total Prompt Tokens: {total_prompt_tokens}")
                    logger.info(f"Total Completion Tokens: {total_completion_tokens}")
                    logger.info(f"Overall Total Tokens: {overall_total_tokens_err}")
                    logger.info("-------------------------------------\n")
                return

    issue_number = issue_details_from_tool.get("number")
    issue_title = issue_details_from_tool.get("title", "Unknown Title")
    issue_body = issue_details_from_tool.get("body", "No body provided.")
    issue_labels_data = issue_details_from_tool.get("labels", [])
    issue_labels = [label.get("name") if isinstance(label, dict) else label for label in issue_labels_data if (isinstance(label, dict) and label.get("name")) or isinstance(label, str)]

    if not issue_number:
        logger.error("Issue number not found in triaged details.")
        if show_token_summary: 
            overall_total_tokens_err = total_prompt_tokens + total_completion_tokens
            logger.info("\n--- Token Usage Summary (Partial) ---")
            logger.info(f"Model Used: {actual_model_name_reported}")
            logger.info(f"Total Prompt Tokens: {total_prompt_tokens}")
            logger.info(f"Total Completion Tokens: {total_completion_tokens}")
            logger.info(f"Overall Total Tokens: {overall_total_tokens_err}")
            logger.info("-------------------------------------\n")
        return
    logger.info(f"Triager Output Summary:\n{triage_output_summary}\n")
    logger.info(f"Successfully processed issue #{issue_number}: '{issue_title}'")

    # --- Step 1.2: Generating Plan ---
    logger.info(f"\n📝 Step 1.2: Generating Plan for issue #{issue_number}...")
    planner_input = (
        f"Based on the following triaged GitHub issue, create a step-by-step plan for resolution:\n"
        f"Issue Title: {issue_title}\nIssue Body:\n{issue_body}\n\n"
        f"Labels: {', '.join(issue_labels)}\nTriage Summary:\n{triage_output_summary}\n"
    )
    planner_run = await run_agent_and_track_usage(planner, planner_input)
    generated_plan = planner_run.final_output
    logger.info(f"Generated Plan:\n{generated_plan}\n")

    # --- Step 1.5: Identify Target Files or Use Override ---
    identified_file_paths_raw: List[str] = []
    if target_file_override:
        identified_file_paths_raw = [f.strip() for f in target_file_override.split(',') if f.strip()]
        logger.info(f"\n✅ User-specified target file(s): {', '.join(identified_file_paths_raw)}. Skipping file identification step.\n")
    else:
        logger.info(f"\n📑 Step 1.5: Identifying Target Files for issue #{issue_number}...")
        identifier_input = (
            f"Based on the following GitHub issue, identify the file(s) that need to be modified, created, or are relevant to a rename/delete operation.\n"
            f"Repository: {repo_owner}/{repo_name}\nDefault Branch: {default_branch_name}\n"
            f"Issue Title: {issue_title}\nIssue Body:\n{issue_body}\n\n"
            f"Labels: {', '.join(issue_labels)}\nOverall Plan: {generated_plan}\n"
        )
        identifier_run = await run_agent_and_track_usage(file_identifier, identifier_input)
        file_output_raw_agent = identifier_run.final_output.strip()
        logger.debug(f"DEBUG: File Identifier Agent Raw Output:\n---\n{file_output_raw_agent}\n---")
        
        if file_output_raw_agent.lower() != 'none':
            path_candidates = re.findall(r"`([^`]+\.[\w.-]+)`|([\w./-]+\.[\w.-]+)", file_output_raw_agent)
            temp_paths = []
            for backticked_path, plain_path in path_candidates:
                path = backticked_path if backticked_path else plain_path
                if path and path.strip(): temp_paths.append(path.strip())
            if not temp_paths and file_output_raw_agent:
                 potential_paths = [line.strip() for line in file_output_raw_agent.split('\n') if '.' in line.strip() and not line.strip().startswith(('-', '*'))]
                 for p_path in potential_paths:
                     cleaned_path = re.sub(r"^- \*\*(?:Current Path|Suggested New Path for .*?):\*\* `(.*?)`$", r"\1", p_path.strip())
                     cleaned_path = re.sub(r"^- (?:Current Path|Suggested New Path for .*?): `(.*?)`$", r"\1", cleaned_path.strip())
                     cleaned_path = cleaned_path.strip().replace('`', '')
                     if '.' in cleaned_path and not any(c in cleaned_path for c in [' ', '(', ')', ':']) and '/' in cleaned_path or '.' in cleaned_path.split('/')[-1]:
                        temp_paths.append(cleaned_path)
            if temp_paths: identified_file_paths_raw = sorted(list(set(temp_paths)))
        logger.info(f"File Identifier Agent identified target file(s) (parsed): {', '.join(identified_file_paths_raw) if identified_file_paths_raw else 'None'}\n")

    if not identified_file_paths_raw:
        logger.warning(f"No target files identified or specified by user. Cannot proceed with code proposals.")
        summary_comment_parts_init = [
            f"🤖 **OctoAgent Report** for Issue #{issue_number}: {issue_title}",
            f"\n**Triage Summary:**\n{triage_output_summary}",
            f"\n**Generated Plan:**\n{generated_plan}",
            "\n**File Identification:**\nNo files were identified for modification by the agent, and no target file was specified by the user. Unable to proceed with code changes.",
        ]
        footer_parts_init = ["\n\n---\n*This comment was automatically generated by OctoAgent, an experimental AI-powered issue-solving assistant.*"]
        if show_token_summary:
            overall_total_tokens_init = total_prompt_tokens + total_completion_tokens
            footer_parts_init.append(f"*Model used: {actual_model_name_reported}, Total tokens: {overall_total_tokens_init} (Prompt: {total_prompt_tokens}, Completion: {total_completion_tokens})*")
        summary_comment_parts_init.extend(footer_parts_init)
        final_summary_comment_init = "\n".join(summary_comment_parts_init)
        await run_agent_and_track_usage(comment_poster, f"Post the following comment to {issue_url}: \n\n{final_summary_comment_init}")
        if show_token_summary: 
            overall_total_tokens_err = total_prompt_tokens + total_completion_tokens
            logger.info("\n--- Token Usage Summary (Partial) ---")
            logger.info(f"Model Used: {actual_model_name_reported}")
            logger.info(f"Total Prompt Tokens: {total_prompt_tokens}")
            logger.info(f"Total Completion Tokens: {total_completion_tokens}")
            logger.info(f"Overall Total Tokens: {overall_total_tokens_err}")
            logger.info("-------------------------------------\n")
        return

    original_file_contents: Dict[str, Optional[str]] = {}
    if identified_file_paths_raw:
        logger.info(f"\nℹ️ Fetching original content for identified files: {', '.join(identified_file_paths_raw)}...")
        for fp in identified_file_paths_raw:
            content_data = await github_client.get_file_content_from_repo(repo_owner, repo_name, fp, default_branch_name)
            if content_data and content_data.get("status") == "success":
                original_file_contents[fp] = content_data["content"]
            else: original_file_contents[fp] = None 
        logger.info("\n")
    
    # --- Step 2: Propose Initial File Operations ---
    current_proposed_operations: List[Dict[str, str]] = []
    proposer_input_parts = [
        f"Based on the following GitHub issue, overall plan, list of relevant files, and their original content (if existing), "
        f"please propose all necessary file operations (creations, modifications, deletions for renames).\n",
        f"Overall Plan:\n{generated_plan}\n",
        f"Issue Title: {issue_title}\n",
        f"Issue Body:\n{issue_body}\n",
        f"Labels: {', '.join(issue_labels)}\n",
        f"Relevant File Paths Identified: {', '.join(identified_file_paths_raw)}\n\n"
    ]
    for fp in identified_file_paths_raw: 
        content = original_file_contents.get(fp)
        if content is not None: proposer_input_parts.append(f"Original content for `{fp}`:\n```\n{content}\n```\n")
        else: proposer_input_parts.append(f"Original content for `{fp}`: This file is new, could not be fetched, or is intended for deletion based on plan.\n")
    proposer_input_parts.append(
        "For each operation:\n"
        "- If creating or modifying a file: State 'Changes for `path/to/file.ext`:' followed by the COMPLETE NEW file content in a markdown block.\n"
        "- If deleting a file: State 'Delete file: `path/to/file.ext`'.\n"
        "- If a file from the identified list needs no changes: State 'No changes needed for `path/to/file.ext`.'\n"
        "If the issue is vague, make a reasonable choice for a simple implementation and state your assumptions in an 'Assumptions Made:' section."
    )
    proposer_input = "".join(proposer_input_parts)
    logger.info(f"\n💡 Step 2: Proposing Initial File Operations for issue #{issue_number}...")
    proposer_run = await run_agent_and_track_usage(code_proposer, proposer_input)
    proposed_solution_markdown = proposer_run.final_output
    logger.debug(f"DEBUG: Code Proposer Raw Output:\n---\n{proposed_solution_markdown}\n---\n")
    current_proposed_operations = parse_file_operations(proposed_solution_markdown)
    logger.info(f"Code Proposer Output (Parsed Operations):")
    if current_proposed_operations:
        for op in current_proposed_operations:
            logger.info(f"  File: {op['file_path']}, Action: {op.get('action')}")
            if op.get('action') == 'modify': logger.debug(f"    Code (first 100 chars):\n{op['code'][:100]}...\n")
    else: logger.warning("Code Proposer did not provide usable changes (output was empty or not parsable).\n")
    
    # Step 2.5: Review and Revision Loop
    max_review_cycles = max_review_cycles_override
    final_operations_to_commit: List[Dict[str, str]] = []
    tech_feedback = "N/A (No operations to review)" 
    style_feedback = "N/A (No operations to review)"
    if current_proposed_operations and any(p.get('action') == 'modify' or p.get('action') == 'delete' for p in current_proposed_operations) :
        temp_proposed_operations = current_proposed_operations
        for cycle in range(max_review_cycles):
            logger.info(f"\n🔄 Review Cycle {cycle + 1}/{max_review_cycles} 🔄")
            review_input_parts = [
                f"Issue Title: {issue_title}\nIssue Number: {issue_number}\nIssue Body:\n{issue_body}\n",
                f"Labels: {', '.join(issue_labels)}\n",
                f"Overall Plan:\n{generated_plan}\n\nProposed File Operations:"
            ]
            has_operations_to_review = False
            for op in temp_proposed_operations: 
                if op.get('action') == 'modify': review_input_parts.append(f"\n--- Modify/Create File: `{op['file_path']}` ---\n```\n{op['code']}\n```"); has_operations_to_review = True
                elif op.get('action') == 'delete': review_input_parts.append(f"\n--- Delete File: `{op['file_path']}` ---"); has_operations_to_review = True
                else: review_input_parts.append(f"\n--- File: `{op['file_path']}` ---\nNo changes proposed.")
            if not has_operations_to_review: final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']; break
            review_task_input = "\n".join(review_input_parts)
            logger.info("🕵️‍♂️ Requesting Technical Review...")
            technical_review_run = await run_agent_and_track_usage(technical_reviewer, review_task_input)
            tech_feedback = technical_review_run.final_output; logger.info(f"Technical Reviewer Output:\n{tech_feedback}\n")
            logger.info("🎨 Requesting Style Review...")
            style_review_run = await run_agent_and_track_usage(style_reviewer, review_task_input)
            style_feedback = style_review_run.final_output; logger.info(f"Style Reviewer Output:\n{style_feedback}\n")
            tech_ok = any(s in tech_feedback.lower() for s in ["lgtm", "satisfactory", "approved"])
            style_ok = any(s in style_feedback.lower() for s in ["lgtm", "satisfactory", "approved"])
            if tech_ok and style_ok: logger.info("✅ Both reviewers are satisfied."); final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']; break
            if cycle < max_review_cycles_override - 1:
                logger.warning("⚠️ Revision needed. Requesting CodeProposer to revise...")
                revision_proposer_input_parts = [
                    f"The following file operations for GitHub issue #{issue_number} ('{issue_title}') received feedback.",
                    f"Overall Plan:\n{generated_plan}\n",
                    "Current Proposed Operations (including original content for context if available):"
                ]
                for op in temp_proposed_operations: 
                    original_content_for_op = original_file_contents.get(op['file_path'], "This file may be new, or its original content was not fetched/available.")
                    if op.get('action') == 'modify': revision_proposer_input_parts.append(f"\n--- File: `{op['file_path']}` (Modify/Create) ---\nOriginal Content (or status):\n```\n{original_content_for_op}\n```\nProposed Code:\n```\n{op['code']}\n```")
                    elif op.get('action') == 'delete': revision_proposer_input_parts.append(f"\n--- File: `{op['file_path']}` (Delete) ---\nOriginal Content (or status):\n```\n{original_content_for_op}\n```\n")
                    else: revision_proposer_input_parts.append(f"\n--- File: `{op['file_path']}` (No Changes) ---")
                revision_proposer_input_parts.append(f"\nFeedback:\nTechnical Review: {tech_feedback}\nStyle Review: {style_feedback}\n")
                revision_proposer_input_parts.append(
                    "Please provide a revised set of file operations. Remember to use the provided original content as the base for modifications. "
                    "For each operation:\n"
                    "- If creating or modifying a file: State 'Changes for `path/to/file.ext`:' followed by the ENTIRE NEW file content.\n"
                    "- If deleting a file: State 'Delete file: `path/to/file.ext`'.\n"
                    "- If a file no longer needs changes: State 'No changes needed for `path/to/file.ext`.'."
                    "If the issue is vague, make a reasonable choice for a simple implementation. Clearly state any assumptions made."
                )
                proposer_run_revised = await run_agent_and_track_usage(code_proposer, "\n".join(revision_proposer_input_parts))
                revised_solution_markdown = proposer_run_revised.final_output
                logger.debug(f"DEBUG: Code Proposer Revised Raw Output:\n---\n{revised_solution_markdown}\n---\n")
                revised_operations = parse_file_operations(revised_solution_markdown)
                if revised_operations: 
                    temp_proposed_operations = revised_operations
                    logger.info(f"Updated File Operations after revision (Parsed):")
                    for op_rev in temp_proposed_operations: logger.info(f"  File: {op_rev['file_path']}, Action: {op_rev.get('action')}")
                else: 
                    logger.warning("Code Proposer did not provide a new set of operations in its revision. Using last valid proposals.")
                    final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']; break
            else: 
                logger.warning(f"Maximum review cycles ({max_review_cycles}) reached. Proceeding with the last proposed operations.")
                final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']
        if not final_operations_to_commit and temp_proposed_operations and any(p.get('action') != 'no_change' for p in temp_proposed_operations):
            logger.warning("Review cycles completed, but solution not fully approved. Committing last valid operations with modifications or deletions.")
            final_operations_to_commit = [p for p in temp_proposed_operations if p.get('action') != 'no_change']
    
    # --- Step 3: Creating/Ensuring Branch ---
    logger.info("\n🌿 Step 3: Creating/Ensuring Branch...")
    branch_prefix = "fix" 
    if any("enhancement" in label.lower() for label in issue_labels): branch_prefix = "feature"
    elif any("chore" in label.lower() for label in issue_labels): branch_prefix = "chore"
    target_branch_name_ideal = f"{branch_prefix}/issue-{issue_number}"
    branch_run = await run_agent_and_track_usage(branch_creator, f"Ensure branch for {repo_owner}/{repo_name} issue {issue_number}, prefix {branch_prefix}, base {default_branch_name}.")
    branch_agent_summary = branch_run.final_output; actual_branch_name_from_tool = target_branch_name_ideal; branch_op_success = False
    new_items_branch_check = getattr(branch_run, 'new_items', None)
    if new_items_branch_check:
        for _, item_br in enumerate(new_items_branch_check):
            if type(item_br).__name__ == 'ToolCallOutputItem': 
                content_br = getattr(item_br, 'output', getattr(item_br, 'content', None))
                if content_br is None and hasattr(item_br, 'raw_item'):
                    raw_br_item = item_br.raw_item; 
                    if isinstance(raw_br_item, dict): content_br = raw_br_item
                if isinstance(content_br, dict):
                    if "error" not in content_br:
                        branch_op_success = True; actual_branch_name_from_tool = content_br.get("branch_name", target_branch_name_ideal)
                        if content_br.get("status") == "already_exists" or content_br.get("already_exists") is True: logger.info(f"Branch '{actual_branch_name_from_tool}' already exists.")
                        else: logger.info(f"Branch '{actual_branch_name_from_tool}' creation/check successful.")
                    else: 
                        logger.error(f"Branch tool reported error: {content_br.get('error')}")
                        branch_agent_summary = content_br.get('error', branch_agent_summary) + f" (Tool Output: {content_br})"
                break 
    if not branch_op_success:
        logger.info(f"Branch Creator Agent Output (Summary): {branch_agent_summary}\n")
        if "error" not in branch_agent_summary.lower() and ("created" in branch_agent_summary.lower() or "exists" in branch_agent_summary.lower() or "successful" in branch_agent_summary.lower()):
            match_bn = re.search(r"(?:branch|')\s*`?([^'`]+)`?\s*(?:has been successfully created|already exists|creation/check successful)", branch_agent_summary, re.IGNORECASE)
            if match_bn: actual_branch_name_from_tool = match_bn.group(1)
            branch_op_success = True; logger.info(f"Branch operation likely successful. Target: {actual_branch_name_from_tool}")
        else: logger.error(f"Branch operation failed or status unclear based on summary.")
    final_target_branch = actual_branch_name_from_tool
    
    # --- Step 4: Committing Code ---
    commit_status_summary = "Commit skipped: No operations to commit or branch operation failed."
    change_explanations_for_comment: List[Dict[str,str]] = []
    if final_operations_to_commit and branch_op_success:
        logger.info(f"\n💾 Step 4: Applying File Operations to branch '{final_target_branch}'...")
        commit_message_base = f"Fix issue #{issue_number}: {issue_title}"
        committer_input_payload = {"repo_owner": repo_owner, "repo_name": repo_name, "branch_name": final_target_branch, "commit_message_base": commit_message_base, "operations": final_operations_to_commit}
        committer_input_str = (f"Apply the following file operations to repository {repo_owner}/{repo_name} on branch {final_target_branch}. Base commit message: '{commit_message_base}'.\n\nOperations: {json.dumps(committer_input_payload['operations'])}")
        committer_run = await run_agent_and_track_usage(committer, committer_input_str)
        commit_status_summary = committer_run.final_output
        logger.info(f"Code Committer Agent Output:\n{commit_status_summary}\n")
        if "error" not in commit_status_summary.lower() and "fail" not in commit_status_summary.lower() :
            logger.info("\n✍️ Step 4.5: Generating Explanations for Changes...")
            for op in final_operations_to_commit:
                original_code_for_explainer = original_file_contents.get(op['file_path'])
                new_code_for_explainer = op.get('code')
                if op.get("action") == "delete": original_code_for_explainer = original_code_for_explainer if original_code_for_explainer is not None else "Content before deletion was not available or file was new."; new_code_for_explainer = "This file was deleted."
                elif op.get("action") == "modify": original_code_for_explainer = original_code_for_explainer if original_code_for_explainer is not None else "This is a new file (no original content)."; new_code_for_explainer = new_code_for_explainer if new_code_for_explainer is not None else "# Error: New code not found in operation proposal."
                else: continue
                explainer_input = (f"Original GitHub Issue Title: {issue_title}\nOriginal GitHub Issue Body:\n{issue_body}\n\nOverall Plan:\n{generated_plan}\n\nFile Path: {op['file_path']}\nAction Taken: {op['action']}\nOriginal Code Snippet (or status):\n{original_code_for_explainer}\n\nNew Code Snippet (or status):\n{new_code_for_explainer}\n\nExplain this specific change.")
                explanation_run = await run_agent_and_track_usage(change_explainer, explainer_input)
                change_explanations_for_comment.append({"file_path": op['file_path'], "action": op['action'], "explanation": explanation_run.final_output})
                logger.debug(f"  Explanation for {op['file_path']} ({op['action']}): {explanation_run.final_output}")
    elif not final_operations_to_commit:
         commit_status_summary = "Commit skipped: No approved file operations to commit."
         logger.warning(commit_status_summary)
    else:
        commit_status_summary = f"Commit skipped due to branch operation failure ({branch_agent_summary})."
        logger.warning(commit_status_summary)
    
    # --- Step 5: Posting Summary Comment ---
    logger.info("\n💬 Step 5: Posting Summary Comment...")
    summary_comment_parts = [f"🤖 **OctoAgent Report** for Issue #{issue_number}: {issue_title}"]
    summary_comment_parts.append(f"\n**Triage Summary:**\n{triage_output_summary}")
    summary_comment_parts.append(f"\n**Generated Plan:**\n{generated_plan}")
    if target_file_override: summary_comment_parts.append(f"\n**File Identification:**\nUser specified target file(s): `{', '.join(identified_file_paths_raw)}`.")
    elif identified_file_paths_raw: summary_comment_parts.append(f"\n**File Identification:**\nAgent identified target file(s): `{', '.join(identified_file_paths_raw)}`.")
    else: summary_comment_parts.append(f"\n**File Identification:**\nNo specific files were identified for modification.")
    if change_explanations_for_comment: 
        summary_comment_parts.append(f"\n**Summary of Changes Applied:**")
        for item in change_explanations_for_comment: summary_comment_parts.append(f"\n* **File:** `{item['file_path']}` ({item['action']})\n    * **Explanation:** {item['explanation']}")
    elif final_operations_to_commit: summary_comment_parts.append(f"\n**Finalized File Operations (Commit Attempted but Explanations Skipped/Failed):**") 
    elif current_proposed_operations and any(p.get('action') != 'no_change' for p in current_proposed_operations): summary_comment_parts.append(f"\n**Code Proposal Attempt:**\nOperations were proposed but not finalized.")
    else: summary_comment_parts.append(f"\n**Code Proposal:** No file operations were proposed or committed.")
    summary_comment_parts.append(f"\n**Technical Review:**\n{tech_feedback}")
    summary_comment_parts.append(f"\n**Style Review:**\n{style_feedback}")
    if branch_op_success: summary_comment_parts.append(f"\n**Branch:** `{final_target_branch}` (Created/Ensured)")
    summary_comment_parts.append(f"\n**Commit Status:**\n{commit_status_summary}")
    
    footer_parts = ["\n---\n*This comment was automatically generated by OctoAgent, an experimental AI-powered issue-solving assistant.*"]
    if show_token_summary:
        overall_total_tokens_final = total_prompt_tokens + total_completion_tokens
        footer_parts.append(f"*Model used: {actual_model_name_reported}, Total tokens: {overall_total_tokens_final} (Prompt: {total_prompt_tokens}, Completion: {total_completion_tokens})*")
    summary_comment_parts.extend(footer_parts)
    final_summary_comment = "\n".join(summary_comment_parts)
    comment_poster_run = await run_agent_and_track_usage(comment_poster, f"Post the following comment to {issue_url}: \n\n{final_summary_comment}")
    logger.info(f"Comment Poster Agent Output: {comment_poster_run.final_output}\n")

    if show_token_summary:
        overall_total_tokens = total_prompt_tokens + total_completion_tokens
        logger.info("\n--- Token Usage Summary ---")
        logger.info(f"Model Used: {actual_model_name_reported if actual_model_name_reported and actual_model_name_reported != 'Unknown' else model_to_use}")
        logger.info(f"Total Prompt Tokens: {total_prompt_tokens}")
        logger.info(f"Total Completion Tokens: {total_completion_tokens}")
        logger.info(f"Overall Total Tokens: {overall_total_tokens}")
        logger.info("---------------------------\n")

    logger.info("=" * 50 + "\n✅ GitHub Issue Solver Flow Completed!\n")


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
    parser.add_argument(
        '--no_token_usage',
        action='store_true',
        help='Hide token usage information. Token usage is shown by default.'
    )
    parser.add_argument(
        '--model',
        type=str,
        default='gpt-4o', 
        help='The OpenAI model to use for the agents (e.g., "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"). Defaults to "gpt-4o".'
    )
    parser.add_argument(
        '--log_level',
        type=str,
        default='WARNING', # Default to WARNING
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Set the logging level. Defaults to WARNING.'
    )
    args = parser.parse_args()

    # Configure logging
    numeric_level = getattr(logging, args.log_level.upper(), None)
    if not isinstance(numeric_level, int):
        # Should not happen due to choices, but as a fallback
        logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
        logger.error(f"Invalid log level: {args.log_level}. Defaulting to WARNING.")
    else:
        logging.basicConfig(level=numeric_level, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
    
    logger.info(f"Logging level set to: {args.log_level.upper()}")


    issue_url = (
        f"https://github.com/{args.user_id}/{args.repo_name}/issues/{args.issue_number}"
    )

    if not os.environ.get("GITHUB_TOKEN"):
        logger.warning("GITHUB_TOKEN not set.")
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("OpenAI API key not set.")

    logger.info(f"--- Starting GitHub Issue Solver ---\nTargeting issue: {issue_url}")
    asyncio.run(
        solve_github_issue_flow(
            issue_url=issue_url,
            repo_owner_override=args.user_id,
            repo_name_override=args.repo_name,
            target_file_override=args.target_file,
            max_review_cycles_override=args.max_review_cycles,
            show_token_summary=(not args.no_token_usage),
            model_to_use=args.model
        )
    )


if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(current_dir)
    project_root = os.path.dirname(src_dir)

    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    main()
