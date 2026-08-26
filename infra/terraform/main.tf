###############################################################################
# main.tf — TenderAI AWS Infrastructure
#
# Resources: S3, SQS (+DLQ), RDS (PostgreSQL/pgvector), ECR, ECS Fargate,
#            ALB, Lambda trigger, Secrets Manager, CloudWatch Log Groups
###############################################################################

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }

  # Uncomment and configure to use S3 remote state (recommended for teams)
  # backend "s3" {
  #   bucket         = "your-tf-state-bucket"
  #   key            = "tenderai/dev/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-lock"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.common_tags
  }
}

locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }

  # Shared name prefix
  prefix = "${var.project_name}-${var.environment}"
}

# ===========================================================================
# SECRETS MANAGER — store sensitive config outside container images
# ===========================================================================

resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${local.prefix}/db-password"
  description             = "TenderAI RDS master password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = var.db_password
}

resource "aws_secretsmanager_secret" "gemini_api_key" {
  name                    = "${local.prefix}/gemini-api-key"
  description             = "Google Gemini API key for TenderAI LLM agents"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "gemini_api_key" {
  secret_id     = aws_secretsmanager_secret.gemini_api_key.id
  secret_string = var.gemini_api_key
}

# ===========================================================================
# S3 — Tender document storage
# ===========================================================================

resource "aws_s3_bucket" "tender_documents" {
  bucket        = "${local.prefix}-tender-documents"
  force_destroy = var.s3_force_destroy
}

resource "aws_s3_bucket_versioning" "tender_documents" {
  bucket = aws_s3_bucket.tender_documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tender_documents" {
  bucket = aws_s3_bucket.tender_documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tender_documents" {
  bucket                  = aws_s3_bucket.tender_documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 → Lambda notification (fires when a PDF lands in uploads/ prefix)
resource "aws_s3_bucket_notification" "upload_trigger" {
  bucket = aws_s3_bucket.tender_documents.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.s3_upload_trigger.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
    filter_suffix       = ".pdf"
  }

  depends_on = [aws_lambda_permission.s3_invoke]
}

# ===========================================================================
# SQS — Job queue + Dead-Letter Queue
# ===========================================================================

# Dead-Letter Queue — receives messages that failed after max_receive_count
resource "aws_sqs_queue" "tender_jobs_dlq" {
  name                      = "${local.prefix}-tender-jobs-dlq"
  message_retention_seconds = 1209600  # 14 days — max allowed, for investigation
  
  # Encryption at rest
  sqs_managed_sse_enabled = true
}

# Main queue
resource "aws_sqs_queue" "tender_jobs" {
  name                       = "${local.prefix}-tender-jobs"
  message_retention_seconds  = var.sqs_message_retention_seconds
  visibility_timeout_seconds = var.sqs_visibility_timeout_seconds

  # After sqs_max_receive_count failed deliveries, route to DLQ
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.tender_jobs_dlq.arn
    maxReceiveCount     = var.sqs_max_receive_count
  })

  sqs_managed_sse_enabled = true
}

# Allow S3 to send a notification through (if using S3→SQS direct instead of Lambda)
resource "aws_sqs_queue_policy" "tender_jobs" {
  queue_url = aws_sqs_queue.tender_jobs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.tender_jobs.arn
      Condition = {
        ArnLike = { "aws:SourceArn" = aws_s3_bucket.tender_documents.arn }
      }
    }]
  })
}

# ===========================================================================
# RDS — PostgreSQL with pgvector support
# ===========================================================================

resource "aws_db_subnet_group" "main" {
  name       = "${local.prefix}-db-subnet-group"
  subnet_ids = aws_subnet.private[*].id

  tags = merge(local.common_tags, { Name = "${local.prefix}-db-subnet-group" })
}

resource "aws_db_instance" "postgres" {
  identifier = "${local.prefix}-postgres"

  # Engine — use PostgreSQL 15 which supports pgvector via extension
  engine               = "postgres"
  engine_version       = "15.4"
  instance_class       = var.db_instance_class
  allocated_storage    = 20
  storage_encrypted    = true
  storage_type         = "gp3"

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # pgvector must be enabled after creation:
  #   CREATE EXTENSION IF NOT EXISTS vector;
  # The FastAPI app does this automatically at startup.
  parameter_group_name = aws_db_parameter_group.postgres15.name

  backup_retention_period = 7
  deletion_protection     = false   # Set true in prod
  skip_final_snapshot     = true    # Set false in prod

  tags = merge(local.common_tags, { Name = "${local.prefix}-postgres" })
}

resource "aws_db_parameter_group" "postgres15" {
  name   = "${local.prefix}-postgres15"
  family = "postgres15"

  # Required for pgvector — ensure shared_preload_libraries includes vector
  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
}

# ===========================================================================
# ECR — Container registries for API and Worker images
# ===========================================================================

resource "aws_ecr_repository" "api" {
  name                 = "${local.prefix}-api"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_repository" "worker" {
  name                 = "${local.prefix}-worker"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Lifecycle: keep only the last 10 images to control storage costs
resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 10 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name
  policy     = aws_ecr_lifecycle_policy.api.policy
}

# ===========================================================================
# CloudWatch Log Groups
# ===========================================================================

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.prefix}/api"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${local.prefix}/worker"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.prefix}-s3-trigger"
  retention_in_days = 14
}

# ===========================================================================
# ECS Cluster
# ===========================================================================

resource "aws_ecs_cluster" "main" {
  name = "${local.prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
  }
}

# ===========================================================================
# ECS Task Definitions
# ===========================================================================

# --- FastAPI API Task ---
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.api_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:latest"
      essential = true

      portMappings = [{
        containerPort = 8000
        protocol      = "tcp"
      }]

      environment = [
        { name = "AWS_REGION",           value = var.aws_region },
        { name = "S3_BUCKET_NAME",       value = aws_s3_bucket.tender_documents.bucket },
        { name = "SQS_QUEUE_URL",        value = aws_sqs_queue.tender_jobs.url },
        { name = "EMBEDDING_MODEL_NAME", value = "all-MiniLM-L6-v2" },
        # Database URL is assembled from secret + static parts
        { name = "DB_HOST",     value = aws_db_instance.postgres.address },
        { name = "DB_PORT",     value = "5432" },
        { name = "DB_NAME",     value = var.db_name },
        { name = "DB_USER",     value = var.db_username },
      ]

      secrets = [
        {
          name      = "DB_PASSWORD"
          valueFrom = aws_secretsmanager_secret.db_password.arn
        },
        {
          name      = "GEMINI_API_KEY"
          valueFrom = aws_secretsmanager_secret.gemini_api_key.arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }
    }
  ])
}

# --- Celery Worker Task ---
resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.prefix}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.worker_task_role.arn

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${aws_ecr_repository.worker.repository_url}:latest"
      essential = true

      environment = [
        { name = "AWS_REGION",           value = var.aws_region },
        { name = "S3_BUCKET_NAME",       value = aws_s3_bucket.tender_documents.bucket },
        { name = "SQS_QUEUE_URL",        value = aws_sqs_queue.tender_jobs.url },
        # SQS broker URL for Celery (kombu SQS transport)
        { name = "CELERY_BROKER_URL",    value = "sqs://" },
        { name = "CELERY_TASK_QUEUE",    value = aws_sqs_queue.tender_jobs.name },
        { name = "EMBEDDING_MODEL_NAME", value = "all-MiniLM-L6-v2" },
        { name = "DB_HOST",              value = aws_db_instance.postgres.address },
        { name = "DB_PORT",              value = "5432" },
        { name = "DB_NAME",              value = var.db_name },
        { name = "DB_USER",              value = var.db_username },
      ]

      secrets = [
        {
          name      = "DB_PASSWORD"
          valueFrom = aws_secretsmanager_secret.db_password.arn
        },
        {
          name      = "GEMINI_API_KEY"
          valueFrom = aws_secretsmanager_secret.gemini_api_key.arn
        },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
    }
  ])
}

# ===========================================================================
# Application Load Balancer — public facing, routes to API tasks
# ===========================================================================

resource "aws_lb" "api" {
  name               = "${local.prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_lb_target_group" "api" {
  name        = "${local.prefix}-api-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"  # required for Fargate awsvpc network mode

  health_check {
    path                = "/health"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = "200"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

# ===========================================================================
# ECS Services
# ===========================================================================

# API service — auto-registered with ALB
resource "aws_ecs_service" "api" {
  name            = "${local.prefix}-api-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

# Worker service — no load balancer, just long-running Celery process
resource "aws_ecs_service" "worker" {
  name            = "${local.prefix}-worker-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = false
  }
}

# ===========================================================================
# Lambda — S3 upload trigger
# ===========================================================================

# Package the Lambda source
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../../lambda/s3_trigger.py"
  output_path = "${path.module}/../../lambda/s3_trigger.zip"
}

resource "aws_lambda_function" "s3_upload_trigger" {
  function_name    = "${local.prefix}-s3-trigger"
  description      = "Triggered by S3 PDF uploads — enqueues a job on SQS for the Celery worker"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.11"
  handler          = "s3_trigger.handler"
  role             = aws_iam_role.lambda_role.arn
  timeout          = 30
  memory_size      = 128

  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.lambda.id]
  }

  environment {
    variables = {
      SQS_QUEUE_URL = aws_sqs_queue.tender_jobs.url
      AWS_REGION    = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}
