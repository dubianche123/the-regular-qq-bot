"""The maintained starter choices for the official OpenAI service."""
from urllib.parse import urlsplit

BASE_URL = "https://api.openai.com/v1"
CHAT_MODELS = ("gpt-6-luna", "gpt-6.1-terra")


def official_base(value):
    """Apply these choices only to this API, never an arbitrary compatible URL."""
    try:
        url = urlsplit(str(value))
        return (url.scheme == "https" and url.hostname == "api.openai.com"
                and url.port in (None, 443) and url.path.rstrip("/") == "/v1"
                and not any((url.username, url.password, url.query, url.fragment)))
    except ValueError:
        return False
