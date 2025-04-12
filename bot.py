import os
import json
import asyncio
import logging
import nextcord
from nextcord.ext import tasks, commands
from sitemap_checker import check_sitemaps, SitemapCheckResult

# Configure logging
logger = logging.getLogger(__name__)

# Initialize bot
intents = nextcord.Intents.default()
intents.message_content = True  # Enable message content intent
bot = commands.Bot(command_prefix='!', intents=intents)

# Global variables
CHECK_INTERVAL = 3600  # 1 hour in seconds
MAX_URLS_TO_DISPLAY = 5
SITEMAP_CONFIG_FILE = 'sitemap_config.json'

# Default sitemap URLs
DEFAULT_SITEMAP_URLS = [
    "https://example.com/sitemap.xml",
    "https://blog.example.com/sitemap.xml",
]

def get_sitemap_urls():
    """Get the list of sitemap URLs from the configuration file"""
    try:
        if os.path.exists(SITEMAP_CONFIG_FILE):
            with open(SITEMAP_CONFIG_FILE, 'r') as f:
                config = json.load(f)
                return config.get('sitemap_urls', DEFAULT_SITEMAP_URLS)
        else:
            # Create the file with default values if it doesn't exist
            update_sitemap_urls(DEFAULT_SITEMAP_URLS)
            return DEFAULT_SITEMAP_URLS
    except Exception as e:
        logger.error(f"Error loading sitemap URLs: {str(e)}")
        return DEFAULT_SITEMAP_URLS

def update_sitemap_urls(urls):
    """Update the list of sitemap URLs in the configuration file"""
    try:
        config = {'sitemap_urls': urls}
        with open(SITEMAP_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        logger.info(f"Updated sitemap URLs: {urls}")
        return True
    except Exception as e:
        logger.error(f"Error updating sitemap URLs: {str(e)}")
        return False

@bot.event
async def on_ready():
    """Called when the bot is ready and connected to Discord"""
    logger.info(f'Bot connected as {bot.user.name} (ID: {bot.user.id})')
    
    # Create an empty known_urls.json file if it doesn't exist
    if not os.path.exists('known_urls.json'):
        with open('known_urls.json', 'w') as f:
            json.dump({}, f)
    
    # Start the sitemap check loop
    check_sitemaps_task.start()

@tasks.loop(seconds=CHECK_INTERVAL)
async def check_sitemaps_task():
    """Task that runs periodically to check sitemaps for new URLs"""
    logger.info("Starting scheduled sitemap check")
    
    try:
        # Read the notification channel ID from environment variable
        channel_id = int(os.environ.get('NOTIFICATION_CHANNEL_ID', 0))
        if not channel_id:
            logger.error("No notification channel ID provided")
            return
        
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.error(f"Could not find channel with ID {channel_id}")
            return
        
        # Get the latest sitemap URLs from configuration
        sitemap_urls = get_sitemap_urls()
        logger.info(f"Checking {len(sitemap_urls)} sitemaps: {sitemap_urls}")
        
        if not sitemap_urls:
            logger.warning("No sitemap URLs configured")
            return
            
        # Check all configured sitemaps
        results = check_sitemaps(sitemap_urls)
        
        # Send notifications for sitemaps with new URLs
        for result in results:
            if result.new_urls:
                await send_notification(channel, result)
                
        logger.info("Completed scheduled sitemap check")
                
    except Exception as e:
        logger.error(f"Error during sitemap check: {str(e)}", exc_info=True)

@check_sitemaps_task.before_loop
async def before_check_sitemaps():
    """Wait for the bot to be ready before starting the task loop"""
    await bot.wait_until_ready()

    async def send_notification(channel, result: SitemapCheckResult):
        """Send a notification to the specified channel about new URLs"""
        site_name = result.sitemap_url # Use sitemap URL as identifier for filename
        # Sanitize site_name to create a valid filename
        safe_site_name = "".join(c for c in site_name if c.isalnum() or c in ('-', '_')).rstrip()
        if not safe_site_name: # Handle empty sanitized name
             safe_site_name = "unknown_site"

        new_urls = result.new_urls
        num_new_urls = len(new_urls)

        logger.info(f"Preparing notification for {num_new_urls} new URLs from {site_name}")

        if num_new_urls == 0:
            logger.warning(f"send_notification called with 0 new URLs for {site_name}")
            return # Nothing to send

        # --- Option 1: Send an embed if few URLs ---
        if num_new_urls <= MAX_URLS_TO_DISPLAY:
            displayed_urls = new_urls # Show all if <= MAX_URLS_TO_DISPLAY

            # Create embed for notification
            embed = nextcord.Embed(
                title=f"🔎 New URLs Detected",
                description=f"Found {num_new_urls} new URL(s) in sitemap: {site_name}",
                color=0x5865F2
            )

            # Add URLs to the embed
            url_list_text = ""
            for url in displayed_urls:
                line_to_add = f"• <{url}>\n" # Use angle brackets
                if len(url_list_text) + len(line_to_add) > 1000: # Check length limit
                    break 
                url_list_text += line_to_add

            if url_list_text:
                embed.add_field(name=f"New URLs ({num_new_urls} total):", value=url_list_text, inline=False)
            else:
                 embed.add_field(name=f"New URLs ({num_new_urls} total):", value="*(Unable to display URLs due to length)*", inline=False)

            try:
                await channel.send(embed=embed)
                logger.info(f"Sent embed notification for {num_new_urls} new URLs from {site_name}")
            except Exception as e:
                logger.error(f"Error sending embed notification for {site_name}: {e}", exc_info=True)

        # --- Option 2: Send a file if many URLs ---
        else:
            filename = f"new_urls_{safe_site_name[:50]}.txt" # Limit filename length
            try:
                # Create and write all URLs to the file
                with open(filename, 'w', encoding='utf-8') as f:
                    for url in new_urls:
                        f.write(url + '\n')

                # Prepare message and file attachment
                message_text = f"🔎 Found {num_new_urls} new URLs for {site_name}. Full list attached."
                discord_file = nextcord.File(filename)

                # Send message with file
                await channel.send(content=message_text, file=discord_file)
                logger.info(f"Sent file notification for {num_new_urls} new URLs from {site_name}")

            except Exception as e:
                logger.error(f"Error creating or sending file notification for {site_name}: {e}", exc_info=True)
                # Fallback: Try sending a simple text message without the list
                try:
                    await channel.send(f"🔎 Found {num_new_urls} new URLs for {site_name}, but failed to attach file.")
                except Exception as fallback_e:
                     logger.error(f"Error sending fallback text notification for {site_name}: {fallback_e}", exc_info=True)

            finally:
                # Clean up the temporary file if it exists
                if os.path.exists(filename):
                    try:
                        os.remove(filename)
                        logger.info(f"Deleted temporary file: {filename}")
                    except Exception as e:
                        logger.error(f"Error deleting temporary file {filename}: {e}")

def run_bot():
    """Run the Discord bot using the token from environment variables"""
    token = os.environ.get('DISCORD_TOKEN')
    print("Checking token...")
    if not token:
        logger.error("No Discord token found in environment variables")
        return
    
    try:
        bot.run(token)
    except Exception as e:
        logger.error(f"Error running bot: {str(e)}", exc_info=True)

if __name__ == "__main__":
    run_bot()
