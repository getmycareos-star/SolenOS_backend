import sys
sys.path.insert(0, '.')

from app.main import app
from fastapi.testclient import TestClient
from app.schemas.situation import SituationInput
from datetime import datetime, timezone

client = TestClient(app)
payload = SituationInput(
    caregiver_id="int-cg-1",
    person_id="int-person-1",
    raw_input="The person fell while getting out of bed.",
    timestamp=datetime.now(timezone.utc).isoformat(),
)
response = client.post("/api/situation", json=payload.model_dump(mode="json"))
print("Status:", response.status_code)
print("Response:", response.text)
if response.status_code != 200:
    print("Error details:")
    try:
        print(response.json())
    except:
        print("Could not parse JSON")
