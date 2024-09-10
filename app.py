import os
import logging
import re
import typing as t
import httpx
import html
import time
from json import JSONDecodeError
from re import sub
from dotenv import load_dotenv
from atproto import Client, models
from tweepy import OAuthHandler, API
from tweepy.errors import TweepyException
import warnings
from helpers import get_og_tags

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic.main")


logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

class MagalitterBot:
    def __init__(self):
        load_dotenv('.env')

        self.enable_twitter = os.getenv('ENABLE_TWITTER', 'false').lower() == 'true'
        self.enable_bluesky = os.getenv('ENABLE_BLUESKY', 'false').lower() == 'true'
        self.domain_name = os.getenv('DOMAIN_NAME')
        self.url = os.getenv('BOARD_URL').format(domain=self.domain_name)
        self.post_format = os.getenv('POST_FORMAT', "New post on /{board}/: {sub} {com}...")
        self.hashtag_name = os.getenv('HASHTAG_NAME')
        self.time_interval_hours = float(os.getenv('TIME_INTERVAL_HOURS', 3))
        self.time_interval_seconds = self.time_interval_hours * 3600
        self.tweeted_post_file = 'tweeted_posts.txt'

        self.twitter_api = self.init_twitter() if self.enable_twitter else None
        self.bluesky_client = self.init_bluesky() if self.enable_bluesky else None

    def init_twitter(self):
        """Initialize Twitter API using Tweepy."""
        try:
            auth = OAuthHandler(os.getenv('API_KEY'), os.getenv('API_SECRET_KEY'))
            auth.set_access_token(os.getenv('ACCESS_TOKEN'), os.getenv('ACCESS_TOKEN_SECRET'))
            api = API(auth)
            logging.info("Twitter API initialized successfully")
            return api
        except TweepyException as e:
            logging.error(f"Error initializing Twitter API: {e}")
            raise

    def init_bluesky(self):
        """Initialize Bluesky client using atproto."""
        try:
            client = Client()
            client.login(os.getenv('BLUESKY_HANDLE'), os.getenv('BLUESKY_PASSWORD'))
            logging.info("Bluesky client initialized successfully")
            return client
        except Exception as e:
            logging.error(f"Error initializing Bluesky client: {e}")
            raise

    def get_tweeted_post_ids(self) -> set:
        """Read already tweeted post IDs from file."""
        if os.path.exists(self.tweeted_post_file):
            with open(self.tweeted_post_file, 'r') as file:
                return set(file.read().splitlines())
        return set()

    def save_tweeted_post_id(self, post_id: int):
        """Save the post ID to avoid future duplication."""
        with open(self.tweeted_post_file, 'a') as file:
            file.write(f"{post_id}\n")

    def fetch_posts(self) -> t.List[dict]:
        """Fetch data from the URL and return posts."""
        try:
            response = httpx.get(self.url)
            response.raise_for_status()
            parsed_data = response.json()
            first_posts = [thread_group['posts'][0] for thread_group in parsed_data['threads']]
            logging.info("Fetched posts successfully")
            return first_posts
        except (httpx.HTTPStatusError, httpx.RequestError, JSONDecodeError) as e:
            logging.error(f"Error fetching posts: {e}")
            return []

    def strip_html(self, text: str) -> str:
        """Remove HTML tags using regex."""
        return sub(r'<[^>]*>', '', text)

    def format_message(self, post: dict) -> str:
        """Format the message using the template from the .env file."""
        board = post.get('board')
        sub = post.get('sub', '').strip()
        com = self.strip_html(post.get('com')).strip()[:150]  # Limit to 150 chars
        com = html.unescape(com)
        url = f"{self.domain_name}/{board}/res/{post.get('no')}"

        if sub:
            return self.post_format.format(board=board, sub=f"{sub} -", com=com, url=url)
        return self.post_format.replace("{sub}", "").format(board=board, com=com, url=url)

    def post_to_bluesky(self, message: str, url: t.Optional[str] = None):
        """Post the message to Bluesky, optionally with media or external resource."""
        if not self.enable_bluesky or not self.bluesky_client:
            logging.info("Bluesky posting is disabled.")
            return

        embed = None
        facets = []

        hashtag = f"#{self.hashtag_name}"
        message += f"{hashtag}"

        message_bytes = message.encode('utf-8')
        hashtag_bytes = hashtag.encode('utf-8')
        hashtag_start = message_bytes.find(hashtag_bytes)
        hashtag_end = hashtag_start + len(hashtag_bytes) 

        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(byteStart=hashtag_start, byteEnd=hashtag_end),
            features=[models.AppBskyRichtextFacet.Tag(tag=self.hashtag_name)]
        ))

        if url:
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
                        thumb_blob = self.bluesky_client.upload_blob(img_data).blob

                    embed = models.AppBskyEmbedExternal.Main(
                        external=models.AppBskyEmbedExternal.External(
                            title=title,
                            description=description,
                            uri=url,
                            thumb=thumb_blob
                        )
                    )
            except Exception as e:
                logging.error(f"Failed to fetch or embed OGP tags: {e}")

        try:
            self.bluesky_client.send_post(text=message, facets=facets, embed=embed)
            logging.info(f"Posted on Bluesky: {message}")
        except Exception as e:
            logging.error(f"Failed to post on Bluesky: {e}")

    def post_to_twitter(self, message: str):
        """Post the message to Twitter."""
        if not self.enable_twitter or not self.twitter_api:
            logging.info("Twitter posting is disabled.")
            return

        try:
            self.twitter_api.update_status(message)
            logging.info(f"Tweeted: {message}")
        except TweepyException as e:
            logging.error(f"Failed to tweet: {e}")


    def run(self):
        """Main bot logic: fetch posts, tweet and post to Bluesky."""
        tweeted_post_ids = self.get_tweeted_post_ids()
        first_posts = self.fetch_posts()

        current_time = time.time()

        for post in first_posts:
            post_id = post.get('no')
            post_time = post.get('time')

            if current_time - post_time < self.time_interval_seconds:
                logging.info(f"Thread #{post_id} is less than {self.time_interval_hours} hours old. Skipping.")
                continue

            if post.get('sticky') == 1 or post.get('locked') == 1:
                logging.info(f"Skipping thread #{post_id}. Sticky: {post.get('sticky')}, Locked: {post.get('locked')}.")
                continue

            if str(post_id) in tweeted_post_ids:
                logging.info(f"Thread #{post_id} already tweeted. Skipping.")
                continue

            message = self.format_message(post)
            url = f"{self.domain_name}/{post.get('board')}/res/{post_id}"

            self.post_to_twitter(message)
            self.post_to_bluesky(message, url=url)
            self.save_tweeted_post_id(post_id)

if __name__ == "__main__":
    bot = MagalitterBot()
    bot.run()
