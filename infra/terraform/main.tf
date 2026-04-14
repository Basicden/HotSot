# HotSot — Terraform AWS Infrastructure (ap-south-1 India Region)

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket = "hotsot-terraform-state"
    key    = "production/terraform.tfstate"
    region = "ap-south-1"
  }
}

provider "aws" {
  region = "ap-south-1"
  default_tags {
    tags = {
      Project   = "HotSot"
      ManagedBy = "terraform"
    }
  }
}

# ─── VPC ───
resource "aws_vpc" "hotsot" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = { Name = "hotsot-vpc" }
}

# Public Subnets (ALB, NAT)
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.hotsot.id
  cidr_block              = "10.0.${count.index + 1}.0/24"
  availability_zone       = element(["ap-south-1a", "ap-south-1b"], count.index)
  map_public_ip_on_launch = true
  tags = { Name = "hotsot-public-${count.index + 1}" }
}

# Private Subnets (EKS Nodes)
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.hotsot.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = element(["ap-south-1a", "ap-south-1b"], count.index)
  tags = { Name = "hotsot-private-${count.index + 1}", "kubernetes.io/role/internal-elb" = "1" }
}

# DB Subnets
resource "aws_subnet" "database" {
  count             = 2
  vpc_id            = aws_vpc.hotsot.id
  cidr_block        = "10.0.${count.index + 20}.0/24"
  availability_zone = element(["ap-south-1a", "ap-south-1b"], count.index)
  tags = { Name = "hotsot-db-${count.index + 1}" }
}

# Internet Gateway
resource "aws_internet_gateway" "hotsot" {
  vpc_id = aws_vpc.hotsot.id
  tags   = { Name = "hotsot-igw" }
}

# NAT Gateway
resource "aws_eip" "nat" {
  domain = "vpc"
}

resource "aws_nat_gateway" "hotsot" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "hotsot-nat" }
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.hotsot.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.hotsot.id
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.hotsot.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.hotsot.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ─── SECURITY GROUPS ───
resource "aws_security_group" "eks" {
  name        = "hotsot-eks-sg"
  description = "EKS cluster security group"
  vpc_id      = aws_vpc.hotsot.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 8000
    to_port     = 8007
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "hotsot-rds-sg"
  description = "RDS security group"
  vpc_id      = aws_vpc.hotsot.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.eks.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ─── RDS POSTGRESQL ───
resource "aws_db_subnet_group" "hotsot" {
  name       = "hotsot-db-subnet"
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_db_instance" "hotsot" {
  identifier             = "hotsot-production"
  engine                 = "postgres"
  engine_version         = "16.1"
  instance_class         = "db.r6g.large"
  allocated_storage      = 100
  storage_encrypted      = true
  db_name                = "hotsot"
  username               = "hotsot_admin"
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.hotsot.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  multi_az               = true
  backup_retention_period = 7
  skip_final_snapshot    = false
  final_snapshot_identifier = "hotsot-final-snapshot"
}

# ─── ELASTICACHE REDIS ───
resource "aws_elasticache_subnet_group" "hotsot" {
  name       = "hotsot-redis-subnet"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "hotsot" {
  replication_group_id          = "hotsot-redis"
  replication_group_description = "HotSot Redis cluster"
  engine                        = "redis"
  engine_version                = "7.1"
  node_type                     = "cache.r6g.large"
  number_cache_clusters         = 2
  subnet_group_name             = aws_elasticache_subnet_group.hotsot.name
  security_group_ids            = [aws_security_group.eks.id]
  automatic_failover_enabled    = true
  at_rest_encryption_enabled    = true
  transit_encryption_enabled    = true
}

# ─── MSK KAFKA ───
resource "aws_msk_cluster" "hotsot" {
  cluster_name           = "hotsot-kafka"
  kafka_version          = "3.5.1"
  number_of_broker_nodes = 3

  broker_node_group_info {
    instance_type   = "kafka.m5.large"
    client_subnets  = aws_subnet.private[*].id
    security_groups = [aws_security_group.eks.id]
    storage_info {
      ebs_storage {
        volume_size = 100
      }
    }
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = aws_kms_key.msk.arn
  }

  configuration_info {
    arn      = aws_msk_configuration.hotsot.arn
    revision = aws_msk_configuration.hotsot.latest_revision
  }
}

resource "aws_msk_configuration" "hotsot" {
  name           = "hotsot-kafka-config"
  kafka_versions = ["3.5.1"]
  server_properties = <<PROPERTIES
auto.create.topics.enable=true
default.replication.factor=3
min.insync.replicas=2
num.partitions=6
PROPERTIES
}

resource "aws_kms_key" "msk" {
  description = "MSK encryption key"
}

# ─── EKS CLUSTER ───
resource "aws_eks_cluster" "hotsot" {
  name     = "hotsot-production"
  role_arn = aws_iam_role.eks_cluster.arn
  vpc_config {
    subnet_ids = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.eks.id]
  }
}

resource "aws_eks_node_group" "hotsot" {
  cluster_name    = aws_eks_cluster.hotsot.name
  node_group_name = "hotsot-workers"
  node_role_arn   = aws_iam_role.eks_node.arn
  subnet_ids      = aws_subnet.private[*].id

  scaling_config {
    desired_size = 3
    max_size     = 10
    min_size     = 2
  }

  instance_types = ["m6i.large"]
}

# ─── IAM ROLES ───
resource "aws_iam_role" "eks_cluster" {
  name = "hotsot-eks-cluster-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role" "eks_node" {
  name = "hotsot-eks-node-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "eks_cluster" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_iam_role_policy_attachment" "eks_node" {
  for_each = toset([
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  policy_arn = each.value
  role       = aws_iam_role.eks_node.name
}

# ─── S3 BUCKET (ML Data + Logs) ───
resource "aws_s3_bucket" "hotsot_data" {
  bucket = "hotsot-ml-data-${var.environment}"
}

# ─── OUTPUTS ───
output "rds_endpoint" {
  value = aws_db_instance.hotsot.endpoint
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.hotsot.primary_endpoint_address
}

output "kafka_bootstrap" {
  value = aws_msk_cluster.hotsot.bootstrap_brokers
}

output "eks_cluster_name" {
  value = aws_eks_cluster.hotsot.name
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "environment" {
  type    = string
  default = "production"
}
