import os
import json
import logging
from google.cloud import pubsub_v1

# Global Publisher Client
publisher = pubsub_v1.PublisherClient()

def publish_kyc_job(subject_data, mode="default"):
    """
    Publishes a KYC job to the Pub/Sub topic.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    topic_id = "kyc-batch-jobs"
    
    if not project_id:
        logging.error("GOOGLE_CLOUD_PROJECT env var not set. Cannot publish to Pub/Sub.")
        return False

    topic_path = publisher.topic_path(project_id, topic_id)
    
    payload = {
        "subject": subject_data,
        "mode": mode
    }
    
    data_str = json.dumps(payload)
    data = data_str.encode("utf-8")
    
    try:
        future = publisher.publish(topic_path, data)
        message_id = future.result()
        logging.info(f"Published KYC job for {subject_data.get('name')} to {topic_path}. Msg ID: {message_id}")
        return message_id
    except Exception as e:
        logging.error(f"Failed to publish KYC job: {e}")
        return None
