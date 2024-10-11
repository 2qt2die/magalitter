import os
import logging
import typing as t
import httpx
import asyncio
import time
from logging.handlers import RotatingFileHandler
from atproto_client import exceptions as exceptions_at
from json import JSONDecodeError
from dotenv import load_dotenv
from atproto import Client, models
from tweepy import Client as x_Client
from tweepy.errors import TweepyException
import warnings
from helpers import HTMLCleaner, create_hashtag_facet, fetch_and_create_ogp_embed

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic.main")


logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        RotatingFileHandler("./log/magalitter_bot.log", maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
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
        self.fallback_image = os.getenv('FALLBACK_IMAGE').format(domain=self.domain_name)
        self.tweeted_post_file = 'tweeted_posts.txt'
        self.bluesky_post_file = 'bluesky_posts.txt'

        self.twitter_api = self.init_twitter() if self.enable_twitter else None
        self.bluesky_client = self.init_bluesky() if self.enable_bluesky else None

    def init_twitter(self):
        """Initialize Twitter API using Tweepy."""
        try:
            client = x_Client(
                bearer_token=os.getenv('BEARER_TOKEN'),
                consumer_key=os.getenv('API_KEY'),
                consumer_secret=os.getenv('API_SECRET_KEY'),
                access_token=os.getenv('ACCESS_TOKEN'),
                access_token_secret=os.getenv('ACCESS_TOKEN_SECRET'),
                wait_on_rate_limit=True
            )
            logging.info("Twitter API initialized successfully")
            return client
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
        except exceptions_at.UnauthorizedError as e:
            logging.error(f"Unauthorized error: Invalid identifier or password - {e}")
            self.enable_bluesky = False
        except Exception as e:
            logging.error(f"Error initializing Bluesky client: {e}")
            raise

    def get_posted_ids(self, plataform_file: str) -> set:
        """Read already tweeted post IDs from file."""
        if os.path.exists(plataform_file):
            with open(plataform_file, 'r') as file:
                return set(file.read().splitlines())
        return set()

    def save_posted_id(self, post_id: int, board_dir: str, plataform_file: str):
        """Save the post ID to avoid future duplication."""
        with open(plataform_file, 'a') as file:
            file.write(f"{board_dir}:{post_id}\n")

    async def fetch_posts(self) -> t.List[dict]:
        """Fetch data from the URL and return posts."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.url)
                response.raise_for_status()
                parsed_data = response.json()
                first_posts = [thread_group['posts'][0] for thread_group in parsed_data['threads']]
                logging.info("Fetched posts successfully")
                return first_posts
        except (httpx.HTTPStatusError, httpx.RequestError, JSONDecodeError) as e:
            logging.error(f"Error fetching posts: {e}")
            return []

    def strip_html(self, text: str) -> str:
        """Remove HTML tags and handle <br> by adding a space."""
        cleaner = HTMLCleaner()
        return cleaner.clean_html(text)

    def format_message(self, post: dict) -> str:
        """Format the message using the template from the .env file."""
        board = post.get('board')
        sub = post.get('sub', '').strip()
        com = self.strip_html(post.get('com')).strip()[:130]  # Limit to 130 chars

        if sub:
            return self.post_format.format(board=board, sub=f"{sub} -", com=com)
        return self.post_format.replace("{sub}", "").format(board=board, com=com)

    async def post_to_bluesky(self, message: str, url: t.Optional[str] = None):
        """Post the message to Bluesky, optionally with media or external resource."""
        if not self.enable_bluesky or not self.bluesky_client:
            logging.info("Bluesky posting is disabled.")
            return True

        embed = None
        hashtag = f"#{self.hashtag_name}"

        facets = create_hashtag_facet(message, self.hashtag_name)

        if url:
            embed = fetch_and_create_ogp_embed(url, self.bluesky_client, self.fallback_image)

        max_message_length = 300 - len(hashtag)
        if len(message) > max_message_length:
            message = message[:max_message_length - 3] + '...'

        bluesky_content = f"{message}\n\n{hashtag}"

        try:
            await asyncio.to_thread(self.bluesky_client.send_post, text=bluesky_content, facets=facets, embed=embed)
            logging.info(f"Posted on Bluesky: {bluesky_content}")
            return True
        except Exception as e:
            logging.error(f"Failed to post on Bluesky: {e}")
            return False 

    async def post_to_twitter(self, message: str, url: str):
        """Post the message to Twitter."""
        if not self.enable_twitter or not self.twitter_api:
            logging.info("Twitter posting is disabled.")
            return True

        suffix = os.getenv('TWITTER_SUFFIX', 'See more at: {url}\n\n').format(url=url)
        hashtag = f"#{self.hashtag_name}"

        max_message_length = 280 - len(suffix) - len(hashtag) 
        if len(message) > max_message_length:
            message = message[:max_message_length - 3] + '...'

        tweet_content = f"{message}{suffix}{hashtag}"

        try:
            await asyncio.to_thread(self.twitter_api.create_tweet, text=tweet_content)
            logging.info(f"Tweeted: {tweet_content}")
            return True
        except TweepyException as e:
            logging.error(f"Failed to tweet: {e}")
            return False

    def should_skip_post(self, post_flags: dict, current_time: float) -> bool:
        """Check whether the post should be skipped due to age, stickiness, or lock."""

        if current_time - post_flags['time'] < self.time_interval_seconds:
            logging.info(f"Skipping thread #{post_flags['no']} from /{post_flags['board']}/ - Too recent (posted {time_difference / 3600:.2f} hours ago).")
            return True

        if post_flags['sticky'] == 1 or post_flags['locked'] == 1:
            logging.info(f"Skipping thread #{post_flags['no']} from /{post_flags['board']}/. Sticky: {post_flags['sticky']}, Locked: {post_flags['locked']}.")
            return True

        return False

    async def post_to_platforms(self, post_id: int, board_dir: str, save_id: str, tweeted_post_ids: set, bluesky_post_ids: set, message: str, url: str):
        """Post to Twitter and Bluesky, and track the post IDs."""

        if save_id not in tweeted_post_ids:
            logging.info(f"Attempting to post to Twitter: Thread #{post_id} from board /{board_dir}/")
            if await self.post_to_twitter(message, url=url):
                self.save_posted_id(post_id, board_dir, self.tweeted_post_file)
            else:
                logging.info(f"Not saving Thread #{post_id} due to error on Twitter.")
        else:
            logging.info(f"Thread #{post_id} already tweeted. Skipping Twitter.")

        if save_id not in bluesky_post_ids:
            logging.info(f"Attempting to post to Bluesky: Thread #{post_id} from board /{board_dir}/")
            if await self.post_to_bluesky(message, url=url):
                self.save_posted_id(post_id, board_dir, self.bluesky_post_file)
            else:
                logging.info(f"Not saving Thread #{post_id} due to error on Bluesky.")
        else:
            logging.info(f"Thread #{post_id} already posted to Bluesky. Skipping Bluesky.")

    async def run(self):
        """Main bot logic: fetch posts, tweet and post to Bluesky."""
        tweeted_post_ids = self.get_posted_ids(self.tweeted_post_file)
        bluesky_post_ids = self.get_posted_ids(self.bluesky_post_file)
        first_posts = await self.fetch_posts()

        current_time = time.time()

        if not first_posts:
            logging.error("No posts fetched. Bye...")
            return

        logging.info(f"Posts fetched: {len(first_posts)} posts")

        tasks = []
        for post in first_posts:
            post_id = post.get('no')
            post_board = post.get('board')
            post_time = post.get('time')

            post_flags = {
                'time': post_time,
                'no': post_id,
                'board': post_board,
                'locked': post.get('locked', 0),
                'sticky': post.get('sticky', 0)
            }

            save_id = f"{post_board}:{post_id}"

            if not self.should_skip_post(post_flags, current_time):
                message = self.format_message(post)
                url = f"{self.domain_name}/{post_board}/res/{post_id}"

                tasks.append(self.post_to_platforms(post_id, post_board, save_id, tweeted_post_ids, bluesky_post_ids, message, url))

        await asyncio.gather(*tasks)

if __name__ == "__main__":
    bot = MagalitterBot()
    asyncio.run(bot.run())
