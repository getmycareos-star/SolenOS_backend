import sys
import traceback
sys.path.insert(0, '.')

try:
    print("Importing app.main...")
    from app.main import app
    print("Success!")
except Exception as e:
    traceback.print_exc()
