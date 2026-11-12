# One repository for the backend image; the API and the worker run the same image
# with different commands.
resource "aws_ecr_repository" "api" {
  name = "${var.project}/api"
  # Images are tagged with the git commit; a tag never moves, so a deploy or a
  # rollback by tag always gets the image that was tested.
  image_tag_mutability = "IMMUTABLE"
  # Lets `terraform destroy` remove the repository with its images.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last ${var.ecr_images_kept} images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = var.ecr_images_kept
      }
      action = { type = "expire" }
    }]
  })
}
