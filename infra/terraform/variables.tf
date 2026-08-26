###############################################################################
# variables.tf — All configurable inputs for TenderAI AWS deployment
###############################################################################

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short name used as a prefix for all resources"
  type        = string
  default     = "tenderai"
}

variable "environment" {
  description = "Deployment environment tag (dev / staging / prod)"
  type        = string
  default     = "dev"
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for public subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "CIDR blocks for private subnets (one per AZ)"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}

# ---------------------------------------------------------------------------
# ECS / Container settings
# ---------------------------------------------------------------------------
variable "api_cpu" {
  description = "Fargate CPU units for the API task (1024 = 1 vCPU)"
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory (MiB) for the API task"
  type        = number
  default     = 1024
}

variable "worker_cpu" {
  description = "Fargate CPU units for the Worker task"
  type        = number
  default     = 1024
}

variable "worker_memory" {
  description = "Fargate memory (MiB) for the Worker task"
  type        = number
  default     = 2048
}

variable "api_desired_count" {
  description = "Desired number of API task replicas"
  type        = number
  default     = 1
}

variable "worker_desired_count" {
  description = "Desired number of Worker task replicas"
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# RDS (PostgreSQL + pgvector)
# ---------------------------------------------------------------------------
variable "db_instance_class" {
  description = "RDS instance type"
  type        = string
  default     = "db.t3.micro"
}

variable "db_name" {
  description = "PostgreSQL database name"
  type        = string
  default     = "tenderai"
}

variable "db_username" {
  description = "PostgreSQL master username"
  type        = string
  default     = "tenderai_admin"
  sensitive   = true
}

variable "db_password" {
  description = "PostgreSQL master password — OVERRIDE via TF_VAR_db_password or tfvars"
  type        = string
  sensitive   = true
}

# ---------------------------------------------------------------------------
# SQS
# ---------------------------------------------------------------------------
variable "sqs_message_retention_seconds" {
  description = "How long SQS retains unprocessed messages (default: 4 days)"
  type        = number
  default     = 345600
}

variable "sqs_visibility_timeout_seconds" {
  description = "Celery task visibility timeout — must be >= max task runtime"
  type        = number
  default     = 900
}

variable "sqs_max_receive_count" {
  description = "Number of times a message is delivered before moving to DLQ"
  type        = number
  default     = 4
}

# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------
variable "s3_force_destroy" {
  description = "Allow Terraform to destroy bucket even when it contains objects (dev only)"
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Gemini API
# ---------------------------------------------------------------------------
variable "gemini_api_key" {
  description = "Google Gemini API key — stored in AWS Secrets Manager, passed as env var"
  type        = string
  sensitive   = true
}
