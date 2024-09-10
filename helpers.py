import re
import typing as t
import httpx
import html
import logging
from atproto import models

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

def create_hashtag_facet(message: str, hashtag_name: str) -> list:
    """Create the hashtag facet for the message."""
    hashtag = f"#{hashtag_name}"
    message += f"{hashtag}"

    message_bytes = message.encode('utf-8')
    hashtag_bytes = hashtag.encode('utf-8')
    hashtag_start = message_bytes.find(hashtag_bytes)
    hashtag_end = hashtag_start + len(hashtag_bytes)

    return [
        models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(byteStart=hashtag_start, byteEnd=hashtag_end),
            features=[models.AppBskyRichtextFacet.Tag(tag=hashtag_name)]
        )
    ]

def fetch_and_create_ogp_embed(url: str, bluesky_client: models.Client) -> t.Optional[models.AppBskyEmbedExternal.Main]:
    """Fetch OGP data and create the embed for Bluesky."""
    try:
        img_url, title, description = get_og_tags(url)

        if title:
            title = html.unescape(title)
        if description:
            description = html.unescape(description)

        if title and description:
            thumb_blob = None
            if img_url:
                img_data = httpx.get(img_url).content
                thumb_blob = bluesky_client.upload_blob(img_data).blob

            return models.AppBskyEmbedExternal.Main(
                external=models.AppBskyEmbedExternal.External(
                    title=title,
                    description=description,
                    uri=url,
                    thumb=thumb_blob
                )
            )
    except Exception as e:
        logging.error(f"Failed to fetch or embed OGP tags: {e}")
        return None