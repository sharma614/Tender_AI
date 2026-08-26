@echo off
REM ============================================================================
REM deploy.bat — One-command build + push + deploy for TenderAI on AWS
REM
REM Prerequisites:
REM   1. AWS CLI configured (aws configure) with sufficient IAM permissions
REM   2. Docker installed and running
REM   3. Terraform applied at least once (infra/terraform/)
REM   4. ECR repository URLs set (auto-read from terraform output)
REM
REM Usage: deploy.bat [--apply-infra]
REM   --apply-infra  : Also runs terraform apply before pushing images
REM ============================================================================

setlocal EnableDelayedExpansion

REM ---- Configuration (edit these to match your deployment) ----
set AWS_REGION=us-east-1
set TERRAFORM_DIR=infra\terraform

REM ---- Step 0: Optionally apply Terraform infrastructure ----
if "%1"=="--apply-infra" (
    echo [1/5] Applying Terraform infrastructure...
    cd %TERRAFORM_DIR%
    terraform init
    terraform apply -auto-approve
    cd ..\..
)

REM ---- Step 1: Read ECR URLs from Terraform output ----
echo [2/5] Reading ECR repository URLs from Terraform...
cd %TERRAFORM_DIR%

for /f "tokens=*" %%a in ('terraform output -raw ecr_api_repository_url 2^>nul') do set ECR_API_URL=%%a
for /f "tokens=*" %%a in ('terraform output -raw ecr_worker_repository_url 2^>nul') do set ECR_WORKER_URL=%%a
for /f "tokens=*" %%a in ('terraform output -raw ecs_cluster_name 2^>nul') do set ECS_CLUSTER=%%a

cd ..\..

if "!ECR_API_URL!"=="" (
    echo ERROR: Could not read ECR API URL from Terraform output.
    echo Make sure you have run: terraform apply
    exit /b 1
)

echo    API Image:    !ECR_API_URL!:latest
echo    Worker Image: !ECR_WORKER_URL!:latest
echo    ECS Cluster:  !ECS_CLUSTER!

REM ---- Step 2: Authenticate Docker with ECR ----
echo [3/5] Authenticating Docker with ECR...
aws ecr get-login-password --region %AWS_REGION% | docker login --username AWS --password-stdin !ECR_API_URL!
if errorlevel 1 ( echo ERROR: ECR login failed & exit /b 1 )

REM ---- Step 3: Build and push API image ----
echo [4/5] Building and pushing API image...
docker build -f Dockerfile.api -t !ECR_API_URL!:latest .
if errorlevel 1 ( echo ERROR: API Docker build failed & exit /b 1 )

docker push !ECR_API_URL!:latest
if errorlevel 1 ( echo ERROR: API Docker push failed & exit /b 1 )

REM ---- Step 4: Build and push Worker image ----
echo [4/5] Building and pushing Worker image...
docker build -f Dockerfile.worker -t !ECR_WORKER_URL!:latest .
if errorlevel 1 ( echo ERROR: Worker Docker build failed & exit /b 1 )

docker push !ECR_WORKER_URL!:latest
if errorlevel 1 ( echo ERROR: Worker Docker push failed & exit /b 1 )

REM ---- Step 5: Force ECS service redeployment ----
echo [5/5] Forcing ECS service redeployment...

aws ecs update-service ^
    --cluster !ECS_CLUSTER! ^
    --service tenderai-dev-api-service ^
    --force-new-deployment ^
    --region %AWS_REGION% ^
    --output text --query "service.serviceName"

aws ecs update-service ^
    --cluster !ECS_CLUSTER! ^
    --service tenderai-dev-worker-service ^
    --force-new-deployment ^
    --region %AWS_REGION% ^
    --output text --query "service.serviceName"

echo.
echo ============================================================
echo  Deploy complete!
echo  API endpoint: Run 'terraform output api_endpoint' to view
echo  Monitor:      aws ecs describe-services --cluster !ECS_CLUSTER! --services tenderai-dev-api-service
echo ============================================================
