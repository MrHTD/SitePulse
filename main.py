from flask import Flask, render_template
from flask_socketio import SocketIO
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
import requests, urllib.parse
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key'

socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp', '.ico'}

def is_image_url(url):
    """Helper to check if a URL ends with a known image extension."""
    if not url:
        return False
    path = urlparse(url).path
    _, ext = os.path.splitext(path.lower())
    return ext in IMAGE_EXTENSIONS
    
def check_url(url):
    """Validates URL status codes masquerading as a real browser."""
    
    # 1. Clean the URL (remove trailing spaces)
    url = url.strip()
    
    # 2. FIX: Encode special characters but KEEP slashes intact (safe="/%")
    parsed = urllib.parse.urlparse(url)
    clean_path = urllib.parse.quote(parsed.path, safe="/%")
    url = parsed._replace(path=clean_path).geturl()

    # 3. Enhanced headers specifically for strict sites like Facebook/LinkedIn
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    try:
        # Pinging strict social media sites with HEAD usually triggers a block.
        # But we will try it, and fallback to GET if they complain.
        response = requests.head(url, headers=headers, timeout=8, allow_redirects=True)
        
        if response.status_code in [405, 403, 400, 401]:
            response = requests.get(url, headers=headers, timeout=8, allow_redirects=True, stream=True)
            
        return response.status_code
    except requests.exceptions.RequestException:
        return "FAILED"

def scroll_page(driver):
    """Gradually scrolls to the bottom of the page to trigger lazy loading."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        socketio.sleep(1.5)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def background_crawler(start_url):
    """Crawls all internal pages of a site with no page limits."""
    if not start_url.startswith("http://") and not start_url.startswith("https://"):
        start_url = "https://" + start_url
        print(f"[SYSTEM] Fixed URL format to: {start_url}")

    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        chrome_options.page_load_strategy = 'none'
        
        print(f"[SYSTEM] Booting up Headless Chrome (Unlimited Scan Mode)...")
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(30)

        visited_pages = set()
        to_visit_pages = [start_url]
        tested_assets = set()
        base_domain = urlparse(start_url).netloc

        # Initialize thread pool executor to rapidly validate links concurrently
        with ThreadPoolExecutor(max_workers=15) as executor:

            while to_visit_pages:
                page_url = to_visit_pages.pop(0)

                if page_url in visited_pages:
                    continue
                
                visited_pages.add(page_url)

                try:
                    print(f"\n[SCANNING PAGE] {page_url}")
                    driver.get(page_url)
                    
                    # Signal frontend that a new page loop has successfully started
                    socketio.emit("update", {"type": "PAGE", "url": page_url, "status": "200 OK"})
                    socketio.sleep(3.0)
                    
                    scroll_page(driver)

                    # --- 1. HARVEST & PROCESS SUB-PAGES & LINKS ---
                    links = driver.find_elements(By.TAG_NAME, "a")
                    link_futures = []

                    for link in links:
                        try:
                            href = link.get_attribute("href")
                            if not href:
                                continue
                            
                            full_url = urljoin(page_url, href).split('#')[0]
                            if not full_url.startswith("http") or "javascript" in full_url:
                                continue

                            asset_type = "IMAGE (via Link)" if is_image_url(full_url) else "LINK"

                            if full_url not in tested_assets:
                                tested_assets.add(full_url)
                                # Offload the request validation to the background thread pool
                                future = executor.submit(check_url, full_url)
                                link_futures.append((future, asset_type, full_url))

                            if urlparse(full_url).netloc == base_domain and asset_type == "LINK":
                                if full_url not in visited_pages and full_url not in to_visit_pages:
                                    to_visit_pages.append(full_url)
                        except Exception:
                            continue

                    # Emit the responses for the links as they complete processing
                    for future, asset_type, full_url in link_futures:
                        try:
                            status = future.result()
                            socketio.emit("update", {"type": asset_type, "url": full_url, "status": status})
                            socketio.sleep(0.001)
                        except Exception:
                            continue

                    # --- 2. HARVEST & PROCESS STANDARD IMAGES ---
                    images = driver.find_elements(By.TAG_NAME, "img")
                    image_futures = []

                    for img in images:
                        try:
                            src = img.get_attribute("src")
                            if src and src not in tested_assets:
                                tested_assets.add(src)
                                # Offload the image asset verification to the background pool
                                future = executor.submit(check_url, src)
                                image_futures.append((future, src))
                        except Exception:
                            continue

                    # Emit the responses for images as they complete processing
                    for future, src in image_futures:
                        try:
                            status = future.result()
                            socketio.emit("update", {"type": "IMAGE", "url": src, "status": status})
                            socketio.sleep(0.001)
                        except Exception:
                            continue

                except Exception as inner_e:
                    print(f"[WARNING] Skipping page {page_url}: {inner_e}")
                    socketio.emit("update", {"type": "ERROR", "url": page_url, "status": "Page Error"})

        print("\n[SYSTEM] Scan Complete. Closing browser.")
        
    except Exception as e:
        print(f"\n[CRITICAL ERROR] {e}")
        socketio.emit("update", {
            "type": "CRITICAL ERROR",
            "url": "Selenium/Chrome configuration failed.",
            "status": "FAIL"
        })
    finally:
        if driver:
            driver.quit()
        socketio.emit("done")

@app.route("/")
def home():
    return render_template("index.html")

@socketio.on("start_scan")
def handle_scan(data):
    target_url = data.get("url")
    print(f"[SYSTEM] Received request to scan: {target_url}")
    socketio.start_background_task(background_crawler, target_url)

if __name__ == "__main__":
    socketio.run(app, debug=True)