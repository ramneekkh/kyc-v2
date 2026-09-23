from google.cloud import storage
import logging
import os

logger = logging.getLogger(__name__)

class GCSClient:
    def __init__(self, bucket_name="kycbucket1"):
        self.bucket_name = bucket_name
        self.client = None
        self.bucket = None
        
    def connect(self):
        if self.client:
            return
            
        try:
            self.client = storage.Client()
            self.bucket = self.client.bucket(self.bucket_name)
            logger.info(f"Connected to GCS bucket: {self.bucket_name}")
        except Exception as e:
            logger.error(f"Failed to connect to GCS: {e}")
            self.client = None

    def upload_string(self, content, destination_blob_name, content_type="text/plain"):
        """Uploads a string to the bucket."""
        self.connect()
        if not self.bucket:
            logger.error("GCS bucket not connected. Skipping upload.")
            return None

        try:
            blob = self.bucket.blob(destination_blob_name)
            blob.upload_from_string(content, content_type=content_type)
            logger.info(f"Uploaded {destination_blob_name} to {self.bucket_name}")
            return f"gs://{self.bucket_name}/{destination_blob_name}"
        except Exception as e:
            logger.error(f"Failed to upload to GCS: {e}")
            return None

    def download_as_string(self, source_blob_name):
        """Downloads a blob from the bucket as a string."""
        self.connect()
        if not self.bucket:
            logger.error("GCS bucket not connected. Skipping download.")
            return None

        try:
            blob = self.bucket.blob(source_blob_name)
            if not blob.exists():
                logger.warning(f"Blob {source_blob_name} does not exist.")
                return None
                
            return blob.download_as_text()
        except Exception as e:
            logger.error(f"Failed to download from GCS: {e}")
            return None

# Global instance
gcs_client = GCSClient()
