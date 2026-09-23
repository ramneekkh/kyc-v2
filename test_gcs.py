from backend.storage.gcs_client import gcs_client

try:
    print("Testing GCS Upload...")
    uri = gcs_client.upload_string("This is a test content.", "test_verification.txt")
    if uri:
        print(f"SUCCESS: Uploaded to {uri}")
    else:
        print("FAILURE: Upload returned None")
except Exception as e:
    print(f"FAILURE: Exception: {e}")
