import os
import threading
import logging
import json
from flask import Flask, render_template, request, redirect, flash, url_for, jsonify
from bot import run_bot, get_sitemap_urls, update_sitemap_urls

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "default-secret-key")

@app.route('/')
def index():
    """Render the main status page"""
    sitemap_urls = get_sitemap_urls()
    return render_template('index.html', sitemap_urls=sitemap_urls)

@app.route('/update_sitemaps', methods=['POST'])
def update_sitemaps():
    """Update the list of sitemap URLs"""
    if request.method == 'POST':
        sitemap_urls = request.form.get('sitemap_urls', '')
        urls = [url.strip() for url in sitemap_urls.split('\n') if url.strip()]
        update_sitemap_urls(urls)
        flash('Sitemap URLs have been updated successfully', 'success')
    return redirect(url_for('index'))

@app.route('/health')
def health():
    """Health check endpoint for uptime monitoring"""
    return "OK", 200

@app.route('/api/check', methods=['GET'])
def api_check():
    """API endpoint to manually trigger a sitemap check"""
    from sitemap_checker import check_sitemaps
    sitemap_urls = get_sitemap_urls()
    
    if not sitemap_urls:
        return jsonify({
            'status': 'error',
            'message': 'No sitemap URLs configured'
        }), 400
        
    try:
        results = check_sitemaps(sitemap_urls)
        response = {
            'status': 'success',
            'checked': len(sitemap_urls),
            'results': []
        }
        
        for result in results:
            result_data = {
                'sitemap_url': result.sitemap_url,
                'total_urls': result.total_urls,
                'new_urls_count': len(result.new_urls),
                'new_urls': result.new_urls[:10],  # Limit to first 10
                'error': result.error
            }
            response['results'].append(result_data)
            
        return jsonify(response)
    except Exception as e:
        logger.error(f"Error during API check: {str(e)}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

def run_flask():
    """Run the Flask web server"""
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    # Start Flask web server in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Flask thread started")

    # Run the Bot in the main thread
    logger.info("Starting Discord bot")
    run_bot()
# NO EXTRA LINES AFTER THIS (except maybe blank lines)