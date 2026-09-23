from google.cloud import storage
import logging
import os

logger = logging.getLogger(__name__)

class GCSClient:
    def __init__(self, bucket_name=None):
        self.bucket_name = bucket_name or os.getenv("GCS_BUCKET_NAME", "kycbucket1")
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

    def download_from_uri(self, gcs_uri):
        """Downloads by full gs://bucket/path URI.

        Findings persist the URI, not the bare blob name, and the bucket in that
        URI is not necessarily the one this client is configured for (e.g. after
        a bucket rename). Reading the bucket out of the URI keeps archived
        evidence retrievable, which matters for audit reconstruction.
        """
        if not gcs_uri or not isinstance(gcs_uri, str):
            return None
        if not gcs_uri.startswith("gs://"):
            # Tolerate a bare blob name for callers that stored one.
            return self.download_as_string(gcs_uri)

        without_scheme = gcs_uri[len("gs://"):]
        bucket_name, _, blob_name = without_scheme.partition("/")
        if not bucket_name or not blob_name:
            logger.warning(f"Malformed GCS URI: {gcs_uri}")
            return None

        self.connect()
        if not self.client:
            logger.error("GCS client not connected. Skipping download.")
            return None

        try:
            if bucket_name == self.bucket_name and self.bucket:
                bucket = self.bucket
            else:
                bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(blob_name)
            if not blob.exists():
                logger.warning(f"Blob {gcs_uri} does not exist.")
                return None
            return blob.download_as_text()
        except Exception as e:
            logger.error(f"Failed to download {gcs_uri} from GCS: {e}")
            return None

# Global instance
gcs_client = GCSClient()
