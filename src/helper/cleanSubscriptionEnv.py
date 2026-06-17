import os


def clean_subscription_env(oauth_token: str) -> dict[str, str]:
    """Build the env for a headless `claude -p`: force subscription auth.

    Strips `ANTHROPIC_API_KEY` so `claude` can't bill the API, and injects
    `CLAUDE_CODE_OAUTH_TOKEN` only when we actually have one — an empty value would
    shadow the ambient login instead of letting `claude` fall back to it.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    if oauth_token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = oauth_token
    return env
