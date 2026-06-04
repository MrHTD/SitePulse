from flask import Flask, render_template, request
from flask_socketio import SocketIO
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import requests
import time

# Initialize Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_key'

# 1. REMOVE the eventlet async_mode. 
# 2. Tell it to explicitly use standard 'threading'
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="*")

MAX_PAGES = 5

def check_url(url):
    try:
        r = requests.get(url, timeout=5)
        return r.status_code
    except Exception as e:
        return "FAILED"

def background_crawler(start_url):
    """Runs in the background and emits live updates to the frontend."""
    
    # 1. WRAP IN A TRY/EXCEPT TO CATCH SILENT CRASHES
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox") # Crucial for preventing crashes
        chrome_options.add_argument("--disable-dev-shm-usage") # Crucial for memory issues
        
        # If this line fails (e.g., missing ChromeDriver), it will now trigger the exception below
        driver = webdriver.Chrome(options=chrome_options)

        visited = set()
        to_visit = [start_url]

        while to_visit and len(visited) < MAX_PAGES:
            page_url = to_visit.pop(0)

            if page_url in visited:
                continue

            try:
                driver.get(page_url)
                time.sleep(1)

                soup = BeautifulSoup(driver.page_source, "html.parser")

                # Main Page
                status = check_url(page_url)
                socketio.emit("update", {"type": "PAGE", "url": page_url, "status": status})

                # Images
                for img in soup.find_all("img"):
                    src = img.get("src")
                    if src:
                        img_url = urljoin(page_url, src)
                        img_status = check_url(img_url)
                        socketio.emit("update", {"type": "IMAGE", "url": img_url, "status": img_status})

                # Internal Links
                for link in soup.find_all("a", href=True):
                    full_url = urljoin(page_url, link["href"])
                    if urlparse(full_url).netloc == urlparse(start_url).netloc:
                        if full_url not in visited and full_url not in to_visit:
                            to_visit.append(full_url)

            except Exception as inner_e:
                socketio.emit("update", {"type": "ERROR", "url": page_url, "status": f"Page Error: {inner_e}"})

            visited.add(page_url)

        driver.quit()
        socketio.emit("done")

    # 2. IF THE WHOLE DRIVER CRASHES, SEND THE ERROR TO THE FRONTEND
    except Exception as e:
        print(f"CRITICAL SYSTEM ERROR: {e}") # Prints to your terminal
        socketio.emit("update", {
            "type": "CRITICAL ERROR", 
            "url": "Selenium/Chrome failed to start", 
            "status": str(e)
        })
        socketio.emit("done")


@app.route("/")
def home():
    return render_template("index.html")

# Switch from a standard HTTP POST to a WebSockets event
@socketio.on("start_scan")
def handle_scan(data):
    target_url = data.get("url")
    # Execute the crawler in a background thread to prevent blocking
    socketio.start_background_task(background_crawler, target_url)

if __name__ == "__main__":
    socketio.run(app, debug=True)