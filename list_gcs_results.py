
from google.cloud import storage
import os

def list_results():
    try:
        storage_client = storage.Client()
        bucket_name = "kycbucket1"
        bucket = storage_client.bucket(bucket_name)
        
        blobs = bucket.list_blobs(prefix="results/")
        
        print("Files in results/:")
        for blob in blobs:
            print(blob.name)
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    list_results()
