# OctoAgent 🐙🕵️

[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-brightgreen.svg?style=flat-square)](https://github.com/bgreenwell/octoagent/issues)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square)](http://makeapullrequest.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

My personal playground for exploring agentic AI concepts by attempting to tackle GitHub issues. This project uses a team of AI agents, powered by the OpenAI Python SDK, to triage, propose, review, and commit solutions for GitHub issues.

**Disclaimer:** This is strictly for learning and experimentation, not for serious bug squashing... yet!

## Directory Structure

The project is organized into a Python package within the `src` directory for better modularity and maintainability.

```
.
├── LICENSE
├── requirements.txt
├── .gitignore
├── src/
│   └── octoagent/
│       ├── __init__.py         # Makes 'octoagent' a Python package
│       ├── agents.py           # All agent class definitions
│       ├── github_client.py    # Handles all GitHub API interactions
│       ├── tools.py            # Agent tools and utility functions
│       └── main.py             # Main execution flow and CLI arguments
└── README.md
```

* `agents.py`: Defines the different AI agents, such as `IssueTriagerAgent`, `CodeProposerAgent`, `CodeReviewerAgent`, and `CodeCommitterAgent`.
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
python -m src.octoagent.main <repo_name> <issue_number> [--user_id <user_id>] [--target_file <path>]
```

### Arguments
* `repo_name`: The name of the repository.
* `issue_number`: The number of the issue you want to solve.
* `--user_id` (optional): The GitHub username or organization that owns the repository. The provided `GITHUB_TOKEN` must have permissions for this user/organization's repository. **Defaults to `bgreenwell`**.
* `--target_file`, `-f` (optional): The full path to the file that should be modified. If provided, this will skip the agent-based file identification step.

### Examples

1.  **Run in autonomous mode:**
    This command attempts to solve issue #12 in the `bgreenwell/statlingua` repository, letting the agent identify the correct file to fix.
    ```bash
    python -m src.octoagent.main statlingua 12
    ```

2.  **Run on another user's repository:**
To target a repository under a different user or organization, use the `--user_id` flag. Note that the `GITHUB_TOKEN` you have set must have access permissions for this repository.
    ```bash
    python -m src.octoagent.main some-awesome-repo 42 --user_id another-developer
    ```
    
3.  **Run with a specific target file to override the agent:**
    If you already know which file needs to be fixed, you can specify it directly to skip the file identification step.
    ```bash
    python -m src.octoagent.main ramify 15 --target_file ".gitignore"
    ```

## TODO

- [x] Add NumPy style docstrings 
- [ ] Add options to specify different provider and model 
- [ ] Add more agentic features (e.g., handoffs)
- [x] Improve logic to automatically determine target file
- [ ] Add robust error handling and retries for API calls
- [ ] Create a more sophisticated review and revision loop
