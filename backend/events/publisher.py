import os
import json
import logging

try:
    from google.cloud import pubsub_v1
except ImportError:  # pragma: no cover - dependency is optional for local runs
    # Mirrors the Spanner client's behaviour: a missing optional dependency
    # disables one feature rather than preventing the whole app from importing.
    pubsub_v1 = None
    logging.warning("google-cloud-pubsub is not installed; job publishing is disabled.")

# Lazy Publisher Client (fork-safe with Gunicorn workers)
_publisher = None

def _get_publisher():
    global _publisher
    if pubsub_v1 is None:
        return None
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher

def publish_kyc_job(subject_data, mode="default"):
    """
    Publishes a KYC job to the Pub/Sub topic.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    topic_id = os.getenv("KYC_PUBSUB_TOPIC", "kyc-batch-jobs")

    if not project_id:
        logging.error("GOOGLE_CLOUD_PROJECT env var not set. Cannot publish to Pub/Sub.")
        return None

    publisher = _get_publisher()
    if publisher is None:
        logging.error("Pub/Sub client unavailable. Cannot publish KYC job.")
        return None

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
