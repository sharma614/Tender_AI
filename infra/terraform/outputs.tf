###############################################################################
# outputs.tf — Values you'll need after terraform apply
###############################################################################

output "api_endpoint" {
  description = "Public URL of the TenderAI FastAPI (via ALB)"
  value       = "http://${aws_lb.api.dns_name}"
}

output "s3_bucket_name" {
  description = "S3 bucket name for tender document uploads"
  value       = aws_s3_bucket.tender_documents.bucket
}

output "sqs_queue_url" {
  description = "SQS queue URL for job dispatch (used by API and Celery worker)"
  value       = aws_sqs_queue.tender_jobs.url
}

output "sqs_dlq_url" {
  description = "SQS Dead-Letter Queue URL for failed jobs"
  value       = aws_sqs_queue.tender_jobs_dlq.url
}

output "rds_endpoint" {
  description = "PostgreSQL RDS endpoint (private — only accessible from within VPC)"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "ecr_api_repository_url" {
  description = "ECR repository URL for the FastAPI image — push with: docker push <url>:latest"
  value       = aws_ecr_repository.api.repository_url
}

output "ecr_worker_repository_url" {
  description = "ECR repository URL for the Celery Worker image"
  value       = aws_ecr_repository.worker.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "db_secret_arn" {
  description = "ARN of the Secrets Manager secret storing the DB password"
  value       = aws_secretsmanager_secret.db_password.arn
  sensitive   = true
}

output "gemini_secret_arn" {
  description = "ARN of the Secrets Manager secret storing the Gemini API key"
  value       = aws_secretsmanager_secret.gemini_api_key.arn
  sensitive   = true
}

output "lambda_function_name" {
  description = "Lambda function that fires on S3 PDF uploads"
  value       = aws_lambda_function.s3_upload_trigger.function_name
}

output "deploy_commands" {
  description = "Quick-reference commands to push images and force service redeployment"
  value = <<-EOT
    # 1. Authenticate Docker with ECR
    aws ecr get-login-password --region ${var.aws_region} | \
      docker login --username AWS --password-stdin ${aws_ecr_repository.api.repository_url}

    # 2. Build + push API image
    docker build -f Dockerfile.api -t ${aws_ecr_repository.api.repository_url}:latest .
    docker push ${aws_ecr_repository.api.repository_url}:latest

    # 3. Build + push Worker image
    docker build -f Dockerfile.worker -t ${aws_ecr_repository.worker.repository_url}:latest .
    docker push ${aws_ecr_repository.worker.repository_url}:latest

    # 4. Force ECS service redeployment
    aws ecs update-service --cluster ${aws_ecs_cluster.main.name} \
      --service ${aws_ecs_service.api.name} --force-new-deployment
    aws ecs update-service --cluster ${aws_ecs_cluster.main.name} \
      --service ${aws_ecs_service.worker.name} --force-new-deployment
  EOT
}
