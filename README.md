Of course. That's a critical clarification to make for anyone using the tool. Clear documentation prevents confusion and errors.

Here is the updated content for your `README.md`. I've revised the "Environment Variables" and "Arguments" sections to explain the relationship between the token and the user ID.

### Updated File: `README.md`
```markdown
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
First, clone the repository and install the necessary Python dependencies. (It is recommended to do this in a virtual environment).

```bash
git clone [https://github.com/bgreenwell/octoagent.git](https://github.com/bgreenwell/octoagent.git)
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

The application is run from the command line, specifying the repository, issue number, and the target file for the fix.

### Command Structure
```bash
python -m src.octoagent.main <repo_name> <issue_number> <target_file> [--user_id <github_user_or_org>]
```

### Arguments
* `repo_name`: The name of the repository.
* `issue_number`: The number of the issue you want to solve.
* `target_file`: The full path to the file within the repository that should be modified.
* `--user_id` (optional): The GitHub username or organization that owns the repository. The provided `GITHUB_TOKEN` must have permissions for this user/organization's repository. **Defaults to `bgreenwell`**.

### Examples

1.  **Run on an issue in the default `bgreenwell` account:**
    The following command attempts to solve issue #12 in the `bgreenwell/statlingua` repository by modifying the `R/explain.R` file.

    ```bash
    python -m src.octoagent.main statlingua 12 R/explain.R
    ```

2.  **Run on an issue in another user's repository:**
    To target a repository under a different user or organization, use the `--user_id` flag.

    ```bash
    python -m src.octoagent.main some-awesome-repo 42 src/app/main.py --user_id another-developer
    ```

## TODO

- [ ] Add more agentic features (e.g., handoffs)
- [ ] Improve logic to automatically determine target files
- [ ] Add robust error handling and retries for API calls
- [ ] Create a more sophisticated review and revision loop
```