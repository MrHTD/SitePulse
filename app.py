from flask import Flask, render_template
from flask_socketio import SocketIO
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import requests

# Initialize Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key'

# CRITICAL: Use standard threading (safest for Selenium)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

def check_url(url):
    try:
        r = requests.get(url, timeout=5)
        return r.status_code
    except Exception:
        return "FAILED"

def background_crawler(start_url, max_pages):
    """Runs in the background and emits live updates to the frontend."""
    
    # Auto-fix missing http://
    if not start_url.startswith("http://") and not start_url.startswith("https://"):
        start_url = "https://" + start_url
        print(f"[SYSTEM] Fixed URL format to: {start_url}")

    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        
        # 1. NEW: Tell Chrome not to wait for background scripts
        chrome_options.page_load_strategy = 'eager' 
        
        print(f"[SYSTEM] Booting up Headless Chrome (Limit: {'Unlimited' if max_pages == 0 else max_pages} pages)...")
        driver = webdriver.Chrome(options=chrome_options)
        
        # Keep the timeout as a backup safety net
        driver.set_page_load_timeout(15)
        
        print(f"[SYSTEM] Booting up Headless Chrome (Limit: {'Unlimited' if max_pages == 0 else max_pages} pages)...")
        driver = webdriver.Chrome(options=chrome_options)
        
        # PREVENTS ENDLESS FREEZING ON MODERN APPS (React/Vercel)
        driver.set_page_load_timeout(15)

        visited = set()
        to_visit = [start_url]

        while to_visit:
            if max_pages > 0 and len(visited) >= max_pages:
                print("\n[SYSTEM] Reached the maximum page limit.")
                break

            page_url = to_visit.pop(0)

            if page_url in visited:
                continue

            try:
                print(f"\n[SCANNING] {page_url}")
                driver.get(page_url)
                
                socketio.sleep(0.5) 

                soup = BeautifulSoup(driver.page_source, "html.parser")

                # # 1. Emit Main Page Status
                # status = check_url(page_url)
                # print(f" -> Status: {status}")
                # socketio.emit("update", {"type": "PAGE", "url": page_url, "status": status})

                # 2. NEW: Check Hyperlinks (<a> tags)
                links = soup.find_all("a", href=True)
                for link in links:
                    href = link.get("href")
                    full_url = urljoin(page_url, href)
                    
                    # Only check actual web links (ignore mailto:, javascript:, etc.)
                    if full_url.startswith("http"):
                        # Emit the link status to the frontend
                        link_status = check_url(full_url)
                        socketio.emit("update", {"type": "LINK", "url": full_url, "status": link_status})
                        socketio.sleep(0.05)
                        
                        # If it's an internal link, add it to our list to crawl later
                        clean_url = full_url.split('#')[0]
                        if urlparse(clean_url).netloc == urlparse(start_url).netloc:
                            if clean_url not in visited and clean_url not in to_visit:
                                to_visit.append(clean_url)

                # 3. Emit Image Statuses
                images = soup.find_all("img")
                for img in images:
                    src = img.get("src")
                    if src:
                        img_url = urljoin(page_url, src)
                        img_status = check_url(img_url)
                        socketio.emit("update", {"type": "IMAGE", "url": img_url, "status": img_status})
                        socketio.sleep(0.05) 


            except Exception as inner_e:
                print(f"[WARNING] Skipping {page_url}: {inner_e}")
                socketio.emit("update", {"type": "ERROR", "url": page_url, "status": f"Page Error"})

            visited.add(page_url)

        print("\n[SYSTEM] Scan Complete. Closing browser.")
        driver.quit()
        socketio.emit("done")

    except Exception as e:
        print(f"\n[CRITICAL ERROR] {e}") 
        socketio.emit("update", {
            "type": "CRITICAL ERROR", 
            "url": "Selenium/Chrome failed to start. Check your terminal.", 
            "status": "FAIL"
        })
        socketio.emit("done")

@app.route("/")
def home():
    return render_template("index.html")

@socketio.on("start_scan")
def handle_scan(data):
    target_url = data.get("url")
    max_pages = int(data.get("max_pages", 0)) 
    print(f"[SYSTEM] Received request to scan: {target_url}")
    socketio.start_background_task(background_crawler, target_url, max_pages)

if __name__ == "__main__":
    socketio.run(app, debug=True)