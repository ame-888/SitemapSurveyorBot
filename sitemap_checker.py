import os
import requests
import json
import logging
from lxml import etree
from dataclasses import dataclass
from typing import List, Set, Dict

# Configure logging
logger = logging.getLogger(__name__)

# Constants
KNOWN_URLS_FILE = 'known_urls.json'
USER_AGENT = 'Mozilla/5.0 (compatible; SitemapMonitorBot/1.0)'
REQUEST_TIMEOUT = 30  # seconds

@dataclass
class SitemapCheckResult:
    """Class to store the result of a sitemap check"""
    sitemap_url: str
    total_urls: int
    new_urls: List[str]
    error: str = ""

def load_known_urls() -> Dict[str, List[str]]:
    """Load the known URLs from the JSON file"""
    try:
        if os.path.exists(KNOWN_URLS_FILE):
            with open(KNOWN_URLS_FILE, 'r') as f:
                try:
                    data = json.load(f)
                    return data
                except json.JSONDecodeError as json_err:
                    logger.error(f"Invalid JSON in {KNOWN_URLS_FILE}: {str(json_err)}")
                    # Create a backup of the corrupted file
                    backup_file = f"{KNOWN_URLS_FILE}.bak"
                    logger.info(f"Creating backup of corrupted file as {backup_file}")
                    try:
                        with open(backup_file, 'w') as backup:
                            with open(KNOWN_URLS_FILE, 'r') as original:
                                backup.write(original.read())
                    except Exception as backup_err:
                        logger.error(f"Failed to create backup: {str(backup_err)}")
                    
                    # Return empty dict to start fresh
                    return {}
        return {}
    except Exception as e:
        logger.error(f"Error loading known URLs: {str(e)}")
        return {}

def save_known_urls(known_urls: Dict[str, List[str]]):
    """Save the known URLs to the JSON file"""
    try:
        # Make sure we have a dictionary
        if not isinstance(known_urls, dict):
            logger.error(f"Expected a dictionary for known_urls, got {type(known_urls)}")
            known_urls = {}
        
        # First validate we can actually convert this to JSON
        try:
            json_data = json.dumps(known_urls, indent=2)
        except Exception as json_err:
            logger.error(f"Cannot serialize known_urls to JSON: {str(json_err)}")
            return
        
        # Create a temporary file first to ensure atomic write
        temp_file = f"{KNOWN_URLS_FILE}.tmp"
        with open(temp_file, 'w') as f:
            f.write(json_data)
        
        # Then rename it to the actual file
        import os
        os.replace(temp_file, KNOWN_URLS_FILE)
        
    except Exception as e:
        logger.error(f"Error saving known URLs: {str(e)}")

def fetch_sitemap(url: str, from_robots_redirect: bool = False) -> str:
    """Fetch a sitemap from the given URL
    
    Args:
        url: The URL to fetch
        from_robots_redirect: Set to True if this call resulted from a robots.txt lookup
                             to prevent infinite recursion
    """
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/xml, text/xml, */*',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    
    # Special handling for known domains
    from urllib.parse import urlparse
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.lower()
    
    # Initialize special_case to False as the default
    special_case = False
    
    # Site-specific fixes
    if domain == 'github.com' and not 'sitemap' in url.lower():
        # GitHub doesn't have traditional sitemaps and blocks automated requests to /sitemap.xml
        logger.info(f"GitHub detected. GitHub restricts sitemap access, using direct page approach")
        
        # Don't try to append sitemap.xml - this is a special case
        special_case = True
        
        # Just use the home page directly
        url = "https://github.com/"
        logger.info(f"Using GitHub homepage directly: {url}")
    elif domain == 'twitter.com' or domain == 'x.com':
        logger.warning(f"Twitter/X doesn't provide public sitemaps")
        raise requests.RequestException("This site doesn't provide public sitemaps")
    elif domain == 'google.com' or domain == 'www.google.com':
        # Google has multiple sitemaps for different services
        logger.info(f"Google detected, using Google Search sitemap")
        url = "https://www.google.com/sitemap.xml"
    
    # Check if URL is missing the sitemap path and try to auto-fix common patterns 
    original_url = url
    if not url.endswith('.xml') and 'sitemap' not in url.lower() and 'robots.txt' not in url.lower() and not special_case:
        # If URL ends with /, append sitemap.xml
        if url.endswith('/'):
            url = f"{url}sitemap.xml"
        else:
            url = f"{url}/sitemap.xml"
        logger.info(f"URL doesn't appear to be a sitemap URL, trying {url} instead of {original_url}")
    
    try:
        logger.info(f"Sending request to {url}")
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        
        # Check content type
        content_type = response.headers.get('Content-Type', '')
        if not ('xml' in content_type.lower() or 'text/plain' in content_type.lower()):
            logger.warning(f"Response content type '{content_type}' may not be a proper sitemap format")
        
        response.raise_for_status()  # Raise exception for 4XX/5XX responses
        
        # Handle robots.txt special case
        content = response.text
        if 'robots.txt' in url.lower():
            import re
            logger.info("Parsing robots.txt to find sitemap references")
            sitemap_urls = re.findall(r'(?i)sitemap:\s*(https?://\S+)', content)
            if sitemap_urls:
                logger.info(f"Found {len(sitemap_urls)} sitemap(s) in robots.txt")
                sitemap_url = sitemap_urls[0]
                logger.info(f"Using first sitemap from robots.txt: {sitemap_url}")
                # Prevent infinite recursion by checking if we're already looking at a robots.txt
                if from_robots_redirect:
                    logger.warning("Already redirected from robots.txt, not following another redirect to avoid recursion")
                    # Just extract urls from this content
                    return content
                else:
                    # It's safe to follow this redirect but set maximum recursion to 1 level
                    logger.info("Following sitemap from robots.txt (one level only)")
                    try:
                        new_content = fetch_sitemap(sitemap_url, from_robots_redirect=True)
                        return new_content
                    except Exception as e:
                        logger.error(f"Error following robots.txt redirect: {str(e)}")
                        # Just return the original content if there's an error
                        return content
            else:
                logger.warning("No sitemaps found in robots.txt")
                raise requests.RequestException("No sitemaps found in robots.txt")
        
        # Quick check if content looks like XML
        if '<?xml' not in content and '<urlset' not in content and '<sitemapindex' not in content:
            logger.warning("Response doesn't appear to contain valid sitemap XML")
            
            # Try to find a link to sitemap if this is an HTML page
            if '<html' in content.lower():
                import re
                logger.info("Received HTML instead of XML, looking for sitemap link in HTML...")
                
                # Look for sitemap link in HTML
                sitemap_links = re.findall(r'href=[\'"]([^\'"]*sitemap[^\'"]*\.xml)[\'"]', content)
                if sitemap_links:
                    sitemap_link = sitemap_links[0]
                    logger.info(f"Found sitemap link in HTML: {sitemap_link}")
                    
                    # Make sure the link is absolute
                    if sitemap_link.startswith('/'):
                        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                        sitemap_link = f"{base_url}{sitemap_link}"
                    
                    # Try to fetch the actual sitemap
                    logger.info(f"Attempting to fetch sitemap from link: {sitemap_link}")
                    return fetch_sitemap(sitemap_link)
                
                # If no sitemap links found in HTML, try to fetch robots.txt
                # But only if we haven't already tried robots.txt to avoid infinite recursion
                if not url.endswith('robots.txt') and not from_robots_redirect:
                    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
                    logger.info(f"No sitemap links found in HTML, checking robots.txt at {robots_url}")
                    try:
                        # Try to fetch robots.txt directly without recursion
                        robots_response = requests.get(robots_url, headers=headers, timeout=REQUEST_TIMEOUT)
                        robots_content = robots_response.text
                        
                        # Parse robots.txt for sitemap references
                        import re
                        sitemap_refs = re.findall(r'(?i)sitemap:\s*(https?://\S+)', robots_content)
                        if sitemap_refs:
                            sitemap_ref = sitemap_refs[0]
                            logger.info(f"Found sitemap in robots.txt: {sitemap_ref}")
                            # Fetch this sitemap directly
                            try:
                                direct_sitemap_response = requests.get(sitemap_ref, headers=headers, timeout=REQUEST_TIMEOUT)
                                return direct_sitemap_response.text
                            except Exception as direct_e:
                                logger.error(f"Error fetching sitemap from robots.txt: {str(direct_e)}")
                        else:
                            logger.info("No sitemap references found in robots.txt")
                    except requests.RequestException as e:
                        logger.error(f"Failed to fetch robots.txt: {str(e)}")
            
        return content
    except requests.RequestException as e:
        logger.error(f"Error fetching sitemap {url}: {str(e)}")
        raise

def parse_sitemap(sitemap_content: str) -> Set[str]:
    """Parse the sitemap content and extract URLs"""
    urls = set()
    
    # Special case handling for HTML pages that aren't XML sitemaps
    if '<html' in sitemap_content.lower() and ('<?xml' not in sitemap_content and '<urlset' not in sitemap_content):
        logger.info("Content appears to be HTML, extracting URLs from HTML")
        import re
        # Extract all URLs from HTML content that look like real pages (not assets, etc)
        html_urls = re.findall(r'href=[\'"]([^\'"]*?(?:\/[^\'"]*?)+?(?:\.html?|\/|\.php))[\'"]', sitemap_content)
        if html_urls:
            logger.info(f"Extracted {len(html_urls)} URLs from HTML content")
            
            # Process URLs to make them absolute if needed
            from urllib.parse import urlparse, urljoin
            parsed_content_url = urlparse(sitemap_content[:1000])  # Use first 1000 chars to try to find base URL
            if parsed_content_url.netloc:
                base_url = f"{parsed_content_url.scheme}://{parsed_content_url.netloc}"
            else:
                # Fallback - this is a guess but we need a base URL
                base_url = "https://example.com"
                
            # Make URLs absolute and add them
            for url in html_urls:
                if url.startswith('/'):
                    absolute_url = urljoin(base_url, url)
                    urls.add(absolute_url)
                elif url.startswith(('http://', 'https://')):
                    urls.add(url)
            
            return urls
    
    try:
        # Parse XML
        root = etree.fromstring(sitemap_content.encode('utf-8'))
        
        # Check if this is a sitemap index
        is_index = root.tag.endswith('sitemapindex')
        
        if is_index:
            # This is a sitemap index, so extract URLs of sub-sitemaps
            namespaces = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            for sitemap in root.xpath('//sm:sitemap/sm:loc/text()', namespaces=namespaces):
                try:
                    sub_sitemap_content = fetch_sitemap(sitemap)
                    sub_urls = parse_sitemap(sub_sitemap_content)
                    urls.update(sub_urls)
                except Exception as sub_e:
                    logger.error(f"Error processing sub-sitemap {sitemap}: {str(sub_e)}")
                    # Continue with other sub-sitemaps instead of failing completely
        else:
            # This is a standard sitemap, extract URLs
            namespaces = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            for url in root.xpath('//sm:url/sm:loc/text()', namespaces=namespaces):
                urls.add(url)
    
    except Exception as e:
        logger.error(f"Error parsing sitemap XML: {str(e)}")
        # Instead of failing completely, try to fall back to regex-based parsing
        import re
        logger.info("Attempting fallback to regex-based parsing")
        # Try to extract URLs with a simple regex
        all_urls = re.findall(r'<loc>(https?://[^<]+)</loc>', sitemap_content)
        if all_urls:
            logger.info(f"Regex fallback found {len(all_urls)} URLs")
            urls.update(all_urls)
        else:
            # If regex fallback also fails, then raise the original exception
            raise
    
    return urls

def check_sitemaps(sitemap_urls: List[str]) -> List[SitemapCheckResult]:
    """Check all sitemaps for new URLs and return results"""
    results = []
    
    # Start with a clean new state for the first run
    if not os.path.exists(KNOWN_URLS_FILE):
        known_urls = {}
    else:
        try:
            known_urls = load_known_urls()
        except Exception as e:
            logger.error(f"Error loading known URLs, starting fresh: {str(e)}")
            known_urls = {}
    
    for sitemap_url in sitemap_urls:
        result = SitemapCheckResult(
            sitemap_url=sitemap_url,
            total_urls=0,
            new_urls=[]
        )
        
        try:
            # Fetch and parse sitemap
            logger.info(f"Fetching sitemap from {sitemap_url}")
            try:
                sitemap_content = fetch_sitemap(sitemap_url)
                current_urls = parse_sitemap(sitemap_content)
            except Exception as fetch_err:
                # Log and propagate the error
                logger.error(f"Error fetching/parsing sitemap {sitemap_url}: {str(fetch_err)}")
                result.error = str(fetch_err)
                results.append(result)
                continue
                
            result.total_urls = len(current_urls)
            
            # Check for new URLs
            if sitemap_url not in known_urls:
                logger.info(f"First time checking {sitemap_url}, storing all URLs as known")
                known_urls[sitemap_url] = []
            
            previous_urls = set(known_urls[sitemap_url])
            new_urls = current_urls - previous_urls
            
            # Also check for removed URLs
            removed_urls = previous_urls - current_urls
            if removed_urls:
                logger.info(f"Found {len(removed_urls)} URLs that were removed from {sitemap_url}")
            
            if new_urls:
                result.new_urls = list(new_urls)
                # Update known URLs with the current set
                known_urls[sitemap_url] = list(current_urls)
                logger.info(f"Found {len(new_urls)} new URLs in {sitemap_url}")
                logger.debug(f"New URLs: {new_urls}")
            else:
                logger.info(f"No new URLs found in {sitemap_url}")
            
            logger.info(f"Checked {sitemap_url}: found {len(current_urls)} URLs, {len(new_urls)} new, {len(removed_urls)} removed")
            
        except Exception as e:
            result.error = str(e)
            logger.error(f"Error checking sitemap {sitemap_url}: {str(e)}", exc_info=True)
        
        results.append(result)
    
    # Save updated known URLs only if we've processed all sitemaps without major errors
    try:
        if known_urls:
            save_known_urls(known_urls)
    except Exception as save_err:
        logger.error(f"Failed to save known URLs: {str(save_err)}")
    
    return results
