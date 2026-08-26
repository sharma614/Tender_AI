"""
lambda/s3_trigger.py — S3 Upload Trigger Lambda

Triggered by: S3 PutObject events on the tender-documents bucket (uploads/*.pdf)

What it does:
  1. Parses the S3 event to extract bucket name and object key
  2. Builds a job payload with the S3 location
  3. Publishes the payload to the SQS tender-jobs queue
  4. The Celery worker picks up the SQS message and runs the full analysis pipeline

Environment variables (set by Terraform):
  SQS_QUEUE_URL  — URL of the main tender-jobs SQS queue
  AWS_REGION     — AWS region (auto-set by Lambda runtime, but explicit here)

Why Lambda instead of direct S3→SQS:
  - Lambda lets us validate the upload (file extension, size check, etc.)
    before creating a job, preventing poison-pill messages from clogging the queue.
  - Future: can add metadata extraction, virus scan trigger, or deduplication here.
"""
import json
import os
import urllib.parse
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client("sqs", region_name=os.environ.get("AWS_REGION", "us-east-1"))
SQS_QUEUE_URL = os.environ["SQS_QUEUE_URL"]


def handler(event, context):
    """
    AWS Lambda entry point.
    
    Parameters
    ----------
    event   : dict — AWS S3 event notification payload
    context : LambdaContext — runtime context (not used)
    
    Returns
    -------
    dict — {"statusCode": 200, "body": "..."} on success
    """
    logger.info("Received S3 event: %s", json.dumps(event))

    processed = []
    errors = []

    for record in event.get("Records", []):
        try:
            # Extract S3 metadata from the event record
            s3_info = record["s3"]
            bucket = s3_info["bucket"]["name"]
            key = urllib.parse.unquote_plus(s3_info["object"]["key"])
            size_bytes = s3_info["object"].get("size", 0)

            logger.info("Processing upload: s3://%s/%s (%d bytes)", bucket, key, size_bytes)

            # Basic validation — guard against empty files or non-PDFs slipping through
            if not key.endswith(".pdf"):
                logger.warning("Skipping non-PDF key: %s", key)
                continue

            if size_bytes == 0:
                logger.warning("Skipping zero-byte file: %s", key)
                continue

            # Build the SQS job payload
            # The Celery worker reads this to know which S3 object to process
            job_payload = {
                "task": "app.tasks.process_tender_job_from_s3",
                "id": f"s3-trigger-{context.aws_request_id}-{key.replace('/', '-')}",
                "args": [],
                "kwargs": {
                    "s3_bucket": bucket,
                    "s3_key": key,
                    "company_profile_dict": None,   # Worker will use the default AeroCorp profile
                },
            }

            # Publish to SQS
            response = sqs.send_message(
                QueueUrl=SQS_QUEUE_URL,
                MessageBody=json.dumps(job_payload),
                MessageAttributes={
                    "ContentType": {
                        "DataType": "String",
                        "StringValue": "application/json",
                    },
                    "SourceBucket": {
                        "DataType": "String",
                        "StringValue": bucket,
                    },
                },
            )

            message_id = response["MessageId"]
            logger.info(
                "Enqueued job for s3://%s/%s → SQS MessageId: %s",
                bucket, key, message_id
            )
            processed.append({"key": key, "message_id": message_id})

        except KeyError as e:
            logger.error("Missing expected field in S3 record: %s", e)
            errors.append({"error": str(e), "record": record})

        except Exception as e:
            logger.error("Unexpected error processing record: %s", e, exc_info=True)
            errors.append({"error": str(e), "record": record})

    result = {
        "processed": len(processed),
        "errors": len(errors),
        "jobs": processed,
    }

    if errors:
        logger.error("Some records failed: %s", json.dumps(errors))
        # Return 207 Multi-Status conceptually, but Lambda must return 200 to avoid
        # S3 retrying the entire batch. Individual errors are logged for alerting.

    logger.info("Lambda complete: %s", json.dumps(result))
    return {"statusCode": 200, "body": json.dumps(result)}
