# OctoAgent 🐙🕵️ <img src="assets/logo.png" align="right" height="120" alt="octoagent logo" />

[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg?style=flat-square)](https://github.com/bgreenwell/octoagent/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

My personal playground for exploring agentic AI concepts by attempting to tackle GitHub issues. This project uses a team of AI agents, powered by the [OpenAI Agents SDK](https://github.com/openai/openai-agents-python), to triage, propose, review, and commit solutions for GitHub issues.

**Disclaimer:** This is strictly for learning and experimentation, not for serious bug squashing... yet!

## Directory Structure

The project is organized into a Python package within the `src` directory for better modularity and maintainability.

```
.
├── assets/
│   └── logo.png 
├── prompts/  <-- New directory
│   ├── branch_creator_agent.md
│   ├── change_explainer_agent.md
│   ├── code_committer_agent.md
│   ├── code_proposer_agent.md
│   ├── code_reviewer_agent_template.md
│   ├── file_identifier_agent.md
│   ├── issue_triager_agent.md
│   ├── planner_agent.md
│   └── comment_poster_agent.md
└── src/
│   └── octoagent/
│       ├── __init__.py         # Makes 'octoagent' a Python package
│       ├── agents.py           # All agent class definitions
│       ├── github_client.py    # Handles all GitHub API interactions
│       ├── tools.py            # Agent tools and utility functions
│       └── main.py             # Main execution flow and CLI arguments
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

* `agents.py`: Defines the different AI agents (e.g., `FileIdentifierAgent`). Their instructions are loaded from the `prompts/` directory.
* `prompts/`: Contains markdown files with the instructional prompts for each agent.
* `github_client.py`: A dedicated client for making requests to the GitHub REST API, handling tasks like fetching issues, creating branches, and committing files.
* `tools.py`: Contains the functions that agents can use (e.g., `download_github_issue`, `commit_code_to_branch`) and helper utilities.
* `main.py`: The main entry point for the application. It handles command-line argument parsing and orchestrates the agent workflow.

## Required Setup

### 1. Dependencies
First, clone the repository and install the necessary Python dependencies. (It is recommended to do this in a virtual environment.)

```bash
git clone https://github.com/bgreenwell/octoagent.git
cd octoagent
pip install -r requirements.txt
```

### 2. Environment Variables
This application requires API keys for both OpenAI and GitHub to function. These should be stored as environment variables.

* **`OPENAI_API_KEY`**: Your API key from OpenAI to power the agents.
* **`GITHUB_TOKEN`**: A GitHub Personal Access Token (PAT). This token must have `repo` scope and belong to a GitHub account that has push access to the target repository (specified via the `user_id` and `repo_name` arguments).

You can set them in your shell like this:

```bash
export OPENAI_API_KEY="your_openai_api_key"
export GITHUB_TOKEN="your_github_personal_access_token"
```

## How to Run

The application is run from the command line, specifying the repository, issue number, and other options.

### Command Structure
```bash
python -m src.octoagent.main <repo_name> <issue_number> [--user_id <user_id>] [--target_file <path>] [--max_review_cycles <int>] [--model <model_name>] [--no_token_usage] [--log_level <LEVEL>]
```

### Arguments
* `repo_name`: The name of the repository.
* `issue_number`: The number of the issue you want to solve.
* `--user_id` (optional): The GitHub username or organization that owns the repository. The provided `GITHUB_TOKEN` must have permissions for this user/organization's repository. **Defaults to `bgreenwell`**.
* `--target_file`, `-f` (optional): The full path to the file that should be modified. If provided, this will skip the agent-based file identification step.
* `--max_review_cycles` (optional): The maximum number of review cycles for code proposals. **Defaults to 3**.
* `--model` (optional): The OpenAI model to use for the agents (e.g., "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"). **Defaults to "gpt-4o"**.
* `--no_token_usage` (optional): If present, hides the summary of token usage. **Token usage is shown by default.**
* `--log_level` (optional): Set the logging level. Options: DEBUG, INFO, WARNING, ERROR, CRITICAL. **Defaults to WARNING.**

### Examples

1.  **Run in autonomous mode:**
    This command attempts to solve issue #12 in the `bgreenwell/statlingua` repository, letting the agent identify the correct file to fix.
    ```bash
    python -m src.octoragent.main statlingua 12
    ```

2.  **Run on another user's repository:**
    To target a repository under a different user or organization, use the `--user_id` flag. Note that the `GITHUB_TOKEN` you have set must have access permissions for this repository.
    ```bash
    python -m src.octoragent.main some-awesome-repo 42 --user_id another-developer
    ```
    
3.  **Run with a specific target file to override the agent:**
    If you already know which file needs to be fixed, you can specify it directly to skip the file identification step.
    ```bash
    python -m src.octoragent.main ramify 15 --target_file ".gitignore"
    ```

4.  **Run with a different number of review cycles:**
    You can control the code revision process by setting the maximum number of review cycles.
    ```bash
    python -m src.octoragent.main statlingua 12 --max_review_cycles 1
    ```

5.  **Run with a specific model:**
    ```bash
    python -m src.octoragent.main statlingua 12 --model gpt-3.5-turbo
    ```

6.  **Run without showing token usage:**
    ```bash
    python -m src.octoragent.main statlingua 12 --no_token_usage
    ```

7.  **Run with verbose debug logging:**
    ```bash
    python -m src.octoragent.main statlingua 12 --log_level DEBUG
    ```

## Writing Agent-Friendly Issues

While **OctoAgent** is designed to understand a variety of issue formats, providing a well-structured issue will significantly improve its accuracy and speed. A detailed and clear issue helps the agents identify the correct files and propose better solutions.

Here is a recommended template for bug reports:

````markdown
### Bug Report

**Description**
A clear and concise description of what the bug is. Why is it a bug and what is the expected outcome?

**To Reproduce**
Steps to reproduce the behavior:
1. Go to '...'
2. Use this input '....'
3. See error log: `...`

**Expected behavior**
A clear and concise description of what you expected to happen.

**Relevant Files (Optional but Recommended)**
If you have a hunch, list any files you suspect might be related to the issue. This is extremely helpful for the `FileIdentifierAgent`.
- `src/app/module.py`
- `src/utils/helpers.py`
````

For **feature requests**, please describe the problem you're trying to solve and your proposed solution in as much detail as possible.

## TODO

Current wishlist (in no particular order of priority):

- [x] Add NumPy style docstrings 
- [x] Introduce a "Planner Agent"
- [x] Add options to specify different provider and model 
- [ ] Add more agentic features (e.g., handoffs)
- [x] Improve logic to automatically determine target file
- [x] Add robust error handling and retries for API calls
- [ ] Create a more sophisticated review and revision loop
- [x] Implement multi-file context awareness
- [ ] Add agent to create a pull request automatically (maybe make this optional, like `--create_pr` flag)
- [ ] Configuration file for agent behavior
  * Instead of relying solely on command-line arguments, a configuration file (e.g., `.octoagent.yml`) could be added to the repository. This would allow users to define more complex behaviors, such as specifying different agent models (e.g., GPT-4 vs. GPT-3.5), setting different review standards, or providing persistent instructions for specific repositories.
- [x] Cost and token usage tracking
- [ ] Refine agent personas and specializations (e.g., R vs. Python expert)