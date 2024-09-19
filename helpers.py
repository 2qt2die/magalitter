import re
import typing as t
import httpx
from html.parser import HTMLParser
from html import unescape
import logging
from atproto import Client, models

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

def fetch_and_create_ogp_embed(url: str, bluesky_client: Client, fallback: str) -> t.Optional[models.AppBskyEmbedExternal.Main]:
    """Fetch OGP data and create the embed for Bluesky."""
    try:
        img_url, title, description = get_og_tags(url)
        title, description = (unescape(title), unescape(description)) if title and description else (None, None)

        thumb_blob = fetch_and_upload_image(url, img_url, fallback, bluesky_client)

        if title and description:
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


def fetch_and_upload_image(url: str, img_url: str, fallback: str, bluesky_client: Client) -> t.Optional[str]:
    """Fetch image from URL or fallback and upload it to Bluesky."""
    thumb_blob = None

    if img_url:
        try:
            response = httpx.get(img_url)
            response.raise_for_status()
            img_data = response.content

            if len(img_data) > 976 * 1024:
                logging.warning(f"Image size {len(img_data)} bytes exceeds the maximum allowed size. Using fallback image.")
                img_data = None
            else:
                thumb_blob = upload_image_to_bluesky(response.content, bluesky_client)
        except (httpx.HTTPStatusError, httpx.RequestError) as http_err:
            logging.warning(f"Error fetching image {img_url}: {http_err}")
        except ValueError as val_err:
            logging.error(f"Value error during image upload: {val_err}")
        except Exception as img_error:
            logging.error(f"Unexpected error processing image: {img_error}")

    if not thumb_blob:
        logging.info(f"Using fallback image for {url}.")
        thumb_blob = upload_image_to_bluesky(httpx.get(fallback).content, bluesky_client)

    return thumb_blob


def upload_image_to_bluesky(img_data: bytes, bluesky_client: Client) -> t.Optional[str]:
    """Upload image to Bluesky and return the blob."""
    upload_response = bluesky_client.upload_blob(img_data)
    thumb_blob = upload_response.blob
    if not thumb_blob:
        raise ValueError("Failed to upload image. No blob returned.")
    logging.info(f"Uploaded blob: {thumb_blob}")
    return thumb_blob

class HTMLCleaner(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fed = []

    def handle_data(self, data):
        """Collect the text data between tags."""
        self.fed.append(data)

    def handle_starttag(self, tag, attrs):
        """Handle specific tags like <br>."""
        if tag == 'br':
            self.fed.append(' ')

    def get_data(self):
        """Return the cleaned text."""
        return ''.join(self.fed)

    def clean_html(self, text: str) -> str:
        """Feed text into the parser and return cleaned data."""
        self.feed(text)
        cleaned_text = self.get_data()
        return unescape(cleaned_text)