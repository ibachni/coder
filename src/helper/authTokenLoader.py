import os

from dotenv import load_dotenv

load_dotenv()


def load_oauth_token() -> str:
    """Subscription OAuth token for headless `claude -p`, read from the environment.

    Returns "" when `CLAUDE_CODE_OAUTH_TOKEN` is unset; `clean_subscription_env` then
    leaves the variable unset so `claude` falls back to the ambient login.
    """
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
