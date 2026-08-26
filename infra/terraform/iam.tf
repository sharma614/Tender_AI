###############################################################################
# iam.tf — Scoped IAM roles for every TenderAI service
###############################################################################

# ---------------------------------------------------------------------------
# ECS Task Execution Role (used by ECS/Fargate to pull images & fetch secrets)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "ecs_execution_role" {
  name = "${var.project_name}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

# Allow ECS agent to pull ECR images and write CloudWatch logs
resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow ECS execution role to read our specific Secrets Manager secrets
resource "aws_iam_policy" "ecs_secrets_read" {
  name        = "${var.project_name}-ecs-secrets-read"
  description = "Allow ECS tasks to read TenderAI secrets from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret",
      ]
      Resource = [
        aws_secretsmanager_secret.db_password.arn,
        aws_secretsmanager_secret.gemini_api_key.arn,
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_secrets_attach" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = aws_iam_policy.ecs_secrets_read.arn
}

# ---------------------------------------------------------------------------
# ECS API Task Role (runtime permissions for the FastAPI container)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "api_task_role" {
  name = "${var.project_name}-api-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_policy" "api_task_policy" {
  name        = "${var.project_name}-api-task-policy"
  description = "Permissions for the TenderAI FastAPI container"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # S3: upload + download tender PDFs
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:HeadObject",
        ]
        Resource = "${aws_s3_bucket.tender_documents.arn}/*"
      },
      # SQS: send jobs to the main queue
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.tender_jobs.arn
      },
      # CloudWatch Logs
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "api_task_policy_attach" {
  role       = aws_iam_role.api_task_role.name
  policy_arn = aws_iam_policy.api_task_policy.arn
}

# ---------------------------------------------------------------------------
# ECS Worker Task Role (runtime permissions for the Celery Worker container)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "worker_task_role" {
  name = "${var.project_name}-worker-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_policy" "worker_task_policy" {
  name        = "${var.project_name}-worker-task-policy"
  description = "Permissions for the TenderAI Celery Worker container"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # SQS: poll jobs, delete messages, send to DLQ
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:SendMessage",    # needed to push to DLQ manually if required
        ]
        Resource = [
          aws_sqs_queue.tender_jobs.arn,
          aws_sqs_queue.tender_jobs_dlq.arn,
        ]
      },
      # S3: download tender PDFs for reprocessing
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:HeadObject",
        ]
        Resource = "${aws_s3_bucket.tender_documents.arn}/*"
      },
      # CloudWatch Logs
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "worker_task_policy_attach" {
  role       = aws_iam_role.worker_task_role.name
  policy_arn = aws_iam_policy.worker_task_policy.arn
}

# ---------------------------------------------------------------------------
# Lambda Execution Role (S3 upload trigger → SQS publisher)
# ---------------------------------------------------------------------------
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_policy" "lambda_policy" {
  name        = "${var.project_name}-lambda-policy"
  description = "Permissions for the TenderAI S3-trigger Lambda"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Basic Lambda execution (CloudWatch Logs)
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      # Read the uploaded object metadata from S3
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:HeadObject"]
        Resource = "${aws_s3_bucket.tender_documents.arn}/*"
      },
      # Call the FastAPI /upload endpoint via the internal ALB
      # (Lambda in same VPC uses internal DNS — no outbound internet needed)
      # SQS publish: Lambda can also publish directly if needed
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueUrl",
        ]
        Resource = aws_sqs_queue.tender_jobs.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy_attach" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.lambda_policy.arn
}

# Allow S3 to invoke the Lambda
resource "aws_lambda_permission" "s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.s3_upload_trigger.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.tender_documents.arn
}
