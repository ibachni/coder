import re
from helper.authTokenLoader import load_oauth_token

oauth_token = load_oauth_token()


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"
