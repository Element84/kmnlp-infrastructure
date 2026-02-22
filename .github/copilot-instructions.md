# AI Coding Agent Instructions for KMNLP Infrastructure

## Project Architecture

This is a **CDK for Terraform (CDKTF)** project that defines AWS infrastructure for the KM NLP project using Python. The architecture consists of 4 interdependent stacks deployed in sequence:

1. **`bootstrap`** - Creates IAM deploy role for CI/CD
2. **`eks_cluster`** - EKS cluster with Dask operator and Fargate profiles
3. **`dask_cluster`** - Kubernetes Dask cluster for distributed computing
4. **`kmnlp_infra`** - ECS Fargate service running Chainlit app + OpenSearch

### Key Integration Points

- **Dask connectivity**: `kmnlp_infra` retrieves the Dask scheduler service from Kubernetes via `DataKubernetesService` and connects over TCP port 8786
- **Configuration management**: All stacks now use a centralized YAML configuration file loaded via `Config.from_config_file()` with Pydantic validation
- **State management**: Each stack uses separate S3 backend state files but shares the same bucket and DynamoDB lock table
- **Bootstrap state sharing**: The `eks_cluster` stack reads the deploy role ARN from bootstrap stack's Terraform remote state

## Essential Development Patterns

### Environment Setup
- **Required**: Set `CONFIG_FILE` environment variable to point to your YAML configuration file (defaults to `configs/prod.yaml`)
- **Configuration file**: Use YAML files in `configs/` directory with Pydantic validation via `kmnlp_infra/config.py`
- **YAML features**: Supports `!env` tag for environment variable substitution and `!from` tag for loading external YAML files
- **Key config sections**: `terraform` (state bucket, deploy bucket, lock table), `aws_account` (account ID, region, VPC name, role ARNs), `chainlit` (ECS service config with image refs)
- **VPC assumption**: Config specifies VPC name; code queries existing VPC with public/private subnets across multiple AZs

### Stack Dependencies
- Bootstrap must be deployed first for CI roles (outputs deploy_role_arn)
- EKS cluster must exist before Dask cluster and reads deploy_role_arn from bootstrap state
- Dask scheduler service must be running before `kmnlp_infra` deployment
- Use `scripts/deploy.sh <stack_name>` for individual stacks (replaces old `cdktf_command.sh`)

### Common Infrastructure Patterns
- **Construct composition**: Each major component (EKS, Dask, ECS service) is a separate construct class that accepts a `Config` object
- **Network sharing**: `Network` construct from `kmnlp_infra/network.py` provides VPC/subnet lookups across stacks
- **IAM patterns**: Use `create_policy()` and `create_oidc_access_policy()` helpers from `kmnlp_infra/utils/policies.py` for consistent policy generation
- **Configuration injection**: All infrastructure constructs receive a `Config` instance to access centralized settings

## Critical Commands

### Development Workflow
```bash
# Setup (after copying .env.template to .env)
uv sync --all-extras
pre-commit install

# Validation
scripts/lint.sh        # Runs shellcheck, ruff, pyright

# Common deployment (assumes bootstrap/EKS already exist)
scripts/deploy.sh      # Deploys dask_cluster + kmnlp_infra only
```

### Stack Management
```bash
# Full deployment sequence
scripts/deploy.sh bootstrap
scripts/deploy.sh eks_cluster
scripts/deploy.sh dask_cluster
scripts/deploy.sh kmnlp_infra
```

## Project-Specific Conventions

### File Organization
- **`main.py`**: Defines all 4 TerraformStack classes and S3Backend configurations
- **`kmnlp_infra/config.py`**: Centralized YAML configuration with Pydantic models (replaces environment variables)
- **`kmnlp_infra/network.py`**: Network construct for VPC and subnet lookups
- **`kmnlp_infra/utils/common.py`**: Shared constants and utility functions
- **`kmnlp_infra/utils/policies.py`**: IAM policy creation helpers (moved from common.py)
- **`kmnlp_infra/utils/yaml_loader.py`**: Custom YAML loader with `!env` and `!from` tag support
- **`kmnlp_infra/*.py`**: Individual infrastructure components as construct classes
- **`kmnlp_infra/components/`**: Reusable sub-components
- **`configs/`**: YAML configuration files (e.g., `prod.yaml`, `image_versions/chainlit.yaml`)
- **`scripts/`**: All deployment and validation automation

### Code Patterns
- **Configuration access**: Use `Config.from_config_file()` to load config, pass `Config` objects to all constructs
- **Environment variables**: Use `!env` tag in YAML for dynamic values, or `get_env_var()` from `utils/common.py` for script-level vars
- **Resource naming**: Follow pattern `{project}-{environment}-{resource-type}` (e.g., `demo-kmnlp-chainlit-alb`)
- **IAM roles**: Use OIDC for Kubernetes service account access, not instance roles
- **Error handling**: Raise `BuildError` for configuration issues that should fail fast
- **Image references**: Use `ImageRef` Pydantic model for ECR image configuration

### AWS-Specific Assumptions
- **Certificate management**: Expects ACM certificate for `demo.kmnlp.element84.com` to exist
- **Route 53**: Manual DNS updates required for new load balancers (see README deployment notes)
- **Parameter Store**: Uses SSM parameters for dynamic configuration (e.g., allowed CIDRs)
- **ECR**: Task definitions pull from ECR repositories in same account

## Debugging Common Issues

- **Dask connection failures**: Verify EKS cluster exists and scheduler service is running with `kubectl get svc`
- **Configuration errors**: Check `CONFIG_FILE` environment variable points to valid YAML file with all required sections
- **Environment variable errors**: For YAML `!env` tags, ensure referenced environment variables are set
- **State lock conflicts**: DynamoDB table `LOCK_TABLE` might need manual cleanup
- **VPC not found**: Verify `VPC_NAME` in config YAML matches existing VPC
- **Image tag issues**: Check `configs/image_versions/*.yaml` files for correct ECR image tags
