import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

def setup():
    retry_strategy = Retry(
        total=5,  # Maximum number of retries
        status_forcelist=[429, 500, 502, 503, 504],  # Retry on these status codes
        backoff_factor=1  # Wait 1 sec before retrying, then increase by 1 sec each retry
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    http = requests.Session()
    http.mount("https://", adapter)

def download(url: str, downloaded_file: str):
    setup()
    response = http.get(url, stream=True)

    try:
        with open(downloaded_file, 'wb') as file:
            for chunk in response.iter_content(chunk_size=10 * 1024):
                file.write(chunk)
    except OSError as e:  # For file I/O errors (e.g., disk full, permission denied)
        pblog.error(f"File I/O error: {e}")
