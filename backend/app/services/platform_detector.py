
from urllib.parse import urlsplit


# Explicitly supported hostnames.
# Add new hosts only after reviewing their safety.
PLATFORM_HOSTS = {
    "tiktok.com": "TikTok",
    "www.tiktok.com": "TikTok",
    "m.tiktok.com": "TikTok",
    "vm.tiktok.com": "TikTok",
    "vt.tiktok.com": "TikTok",

    "instagram.com": "Instagram",
    "www.instagram.com": "Instagram",

    "facebook.com": "Facebook",
    "www.facebook.com": "Facebook",
    "m.facebook.com": "Facebook",
    "web.facebook.com": "Facebook",
    "fb.watch": "Facebook",
}

MAX_URL_LENGTH = 2048


def detect_platform(url: str) -> dict:
    """
    Validate a media URL and identify its supported platform.

    This function identifies URLs only.
    It does not guarantee that the media is safe or downloadable.
    """

    if not isinstance(url, str):
        return {
            "valid": False,
            "platform": None,
            "message": "URL must be a string.",
        }

    if not url:
        return {
            "valid": False,
            "platform": None,
            "message": "Please enter a URL.",
        }

    if len(url) > MAX_URL_LENGTH:
        return {
            "valid": False,
            "platform": None,
            "message": "URL is too long.",
        }

    # Reject whitespace, ASCII control characters and backslashes
    # before urlsplit(), which may normalize some of these characters.
    if any(
        ord(char) <= 32 or ord(char) == 127
        for char in url
    ) or "\\" in url:
        return {
            "valid": False,
            "platform": None,
            "message": "URL contains invalid characters.",
        }

    try:
        parsed = urlsplit(url)

        if parsed.scheme.lower() != "https":
            return {
                "valid": False,
                "platform": None,
                "message": "Only HTTPS URLs are supported.",
            }

        if not parsed.hostname:
            return {
                "valid": False,
                "platform": None,
                "message": "Invalid URL format.",
            }

        if parsed.username is not None or parsed.password is not None:
            return {
                "valid": False,
                "platform": None,
                "message": "URLs containing credentials are not allowed.",
            }

        if parsed.port not in (None, 443):
            return {
                "valid": False,
                "platform": None,
                "message": "Custom URL ports are not supported.",
            }

        hostname = parsed.hostname.lower().rstrip(".")

        platform = PLATFORM_HOSTS.get(hostname)

        if platform is None:
            return {
                "valid": False,
                "platform": None,
                "message": (
                    "Unsupported platform. Use TikTok, Facebook, or Instagram."
                ),
            }

        if not parsed.path or parsed.path == "/":
            return {
                "valid": False,
                "platform": platform,
                "message": "The URL does not appear to contain a media link.",
            }

        return {
            "valid": True,
            "platform": platform,
            "message": f"{platform} URL recognized successfully.",
        }

    except ValueError:
        return {
            "valid": False,
            "platform": None,
            "message": "Invalid URL format.",
        }
