import sys
import threading
import time
import requests
import json
from app.main import app
import uvicorn

def run_app():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

# Start the app in a thread
thread = threading.Thread(target=run_app)
thread.daemon = True
thread.start()

# Wait for the app to start
time.sleep(2)

# Make the request
url = "http://127.0.0.1:8000/api/situation"
payload = {
    "caregiver_id": "int-cg-1",
    "person_id": "int-person-1",
    "raw_input": "The person fell while getting out of bed.",
    "timestamp": datetime.now(timezone.utc).isoformat()
}
try:
    response = requests.post(url, json=payload, timeout=5)
    print("Status:", response.status_code)
    print("Response:", response.text)
except Exception as e:
    print("Error:", e)

# Note: we cannot easily stop the uvicorn server running in a thread.
# We'll just let it run and hope it doesn't cause issues.
