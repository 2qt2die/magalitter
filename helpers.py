import re
import typing as t
import httpx
import logging

# Regular expressions for parsing Open Graph tags
_META_PATTERN = re.compile(r'<meta property="og:[^>]*>')
_CONTENT_PATTERN = re.compile(r'<meta[^>]+content="([^"]+)"')

# Helper functions for parsing Open Graph tags
def _find_tag(og_tags: t.List[str], search_tag: str) -> t.Optional[str]:
    for tag in og_tags:
        if search_tag in tag:
            return tag
    return None

def _get_tag_content(tag: str) -> t.Optional[str]:
    match = _CONTENT_PATTERN.match(tag)
    return match.group(1) if match else None

def _get_og_tag_value(og_tags: t.List[str], tag_name: str) -> t.Optional[str]:
    tag = _find_tag(og_tags, tag_name)
    return _get_tag_content(tag) if tag else None

def get_og_tags(url: str) -> t.Tuple[t.Optional[str], t.Optional[str], t.Optional[str]]:
    """Fetch Open Graph (OG) tags from a given URL using httpx."""
    try:
        response = httpx.get(url)
        response.raise_for_status()
        og_tags = _META_PATTERN.findall(response.text)
        return (
            _get_og_tag_value(og_tags, 'og:image'),
            _get_og_tag_value(og_tags, 'og:title'),
            _get_og_tag_value(og_tags, 'og:description'),
        )
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logging.error(f"Error fetching Open Graph tags from {url}: {e}")
        return None, None, None
