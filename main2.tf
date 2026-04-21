terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.40.0"
    }
    tls = {
      source  = "hashicorp/tls"
    }
    local = {
      source  = "hashicorp/local"
    }
  }
}


# ###########################
# VPC 网络
# ###########################
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "prod-vpc"
  }
}

resource "aws_subnet" "public_subnet" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidr
  availability_zone       = "ap-southeast-1a"
  map_public_ip_on_launch = true

  tags = {
    Name        = "public-subnet"
    NetworkType = "public"
  }
}

resource "aws_subnet" "private_subnet" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.private_subnet_cidr
  availability_zone       = "ap-southeast-1a"
  map_public_ip_on_launch = false

  tags = {
    Name        = "private-subnet"
    NetworkType = "private"
  }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "prod-igw"
  }
}

resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "public-route-table"
  }
}

resource "aws_route_table_association" "public_assoc" {
  subnet_id      = aws_subnet.public_subnet.id
  route_table_id = aws_route_table.public_rt.id
}

# ###########################
# 安全组
# ###########################
resource "aws_security_group" "openresty_sg" {
  name        = "openresty-sg"
  description = "Allow HTTP, HTTPS, SSH"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "openresty-sg"
  }
}

# ###########################
# SSH 密钥
# ###########################
resource "tls_private_key" "server_key" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "server_key_pair" {
  key_name   = "openresty-key"
  public_key = tls_private_key.server_key.public_key_openssh
}

resource "local_file" "private_key" {
  content         = tls_private_key.server_key.private_key_pem
  filename        = "${path.module}/openresty-key.pem"
  file_permission = "0400"
}

# ###########################
# EC2 OpenResty
# ###########################
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "openresty_server" {
  ami                    = data.aws_ami.amazon_linux_2.id
  instance_type          = "t2.micro"
  key_name               = aws_key_pair.server_key_pair.key_name
  vpc_security_group_ids = [aws_security_group.openresty_sg.id]
  subnet_id              = aws_subnet.public_subnet.id

  user_data = <<-EOF
#!/bin/bash
yum update -y
yum install -y yum-utils
yum-config-manager --add-repo https://openresty.org/package/amazon/openresty.repo
yum install -y openresty
systemctl start openresty
systemctl enable openresty
echo "<h1>Deployed via Terraform | CloudFront: redsheep.online</h1>" > /usr/local/openresty/nginx/html/index.html
EOF

  tags = {
    Name = "openresty-server"
  }
}

# ###########################
# Route53 自动获取托管区域
# ###########################
data "aws_route53_zone" "main" {
  name         = "redsheep.online."
  private_zone = false
}

# ###########################
# 源站域名（给 CloudFront 使用）
# ###########################
resource "aws_route53_record" "origin" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = "origin.${var.domain_name}"
  type    = "A"
  records = [aws_instance.openresty_server.public_ip]
  ttl     = 300
}

# ###########################
# CloudFront 证书（us-east-1）
# ###########################
resource "aws_acm_certificate" "cf_cert" {
  provider          = aws.us-east-1
  domain_name       = var.domain_name
  subject_alternative_names = [
    "*.${var.domain_name}"
  ]
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# 自动创建 DNS 验证记录（必须！）
resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.cf_cert.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      type   = dvo.resource_record_type
      value  = dvo.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.main.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.value]
  ttl             = 60
  allow_overwrite = true   # 关键修复：允许覆盖已有记录，避免冲突
}

# 等待证书自动验证
resource "aws_acm_certificate_validation" "cf_cert_validation" {
  provider                = aws.us-east-1
  certificate_arn         = aws_acm_certificate.cf_cert.arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation : record.fqdn]
}

# ###########################
# CloudFront 免费版
# ###########################
resource "aws_cloudfront_distribution" "cdn" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name = aws_route53_record.origin.name
    origin_id   = "OpenResty-EC2"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD", "OPTIONS"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "OpenResty-EC2"

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    viewer_protocol_policy = "redirect-to-https"
    min_ttl                = 0
    default_ttl            = 60
    max_ttl                = 300
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # 关键：等待证书验证完成
  depends_on = [aws_acm_certificate_validation.cf_cert_validation]

  viewer_certificate {
    acm_certificate_arn = aws_acm_certificate.cf_cert.arn
    ssl_support_method  = "sni-only"
  }
}

# ###########################
# 主域名指向 CloudFront
# ###########################
resource "aws_route53_record" "cf" {
  zone_id = data.aws_route53_zone.main.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.cdn.domain_name
    zone_id                = aws_cloudfront_distribution.cdn.hosted_zone_id
    evaluate_target_health = false
  }
}

# ###########################
# 输出结果（已修复）
# ###########################
output "ec2_public_ip" {
  value = aws_instance.openresty_server.public_ip
}

output "origin_domain" {
  value = aws_route53_record.origin.name
}

output "cloudfront_domain" {
  value = aws_cloudfront_distribution.cdn.domain_name
}

output "final_url" {
  value = "https://${var.domain_name}"
}
