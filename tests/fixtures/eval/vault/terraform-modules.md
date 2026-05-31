# Terraform Reusable Modules

A Terraform module is a reusable package of infrastructure configuration. Input
variables parameterize a module so the same code provisions different environments,
and outputs expose values for other modules to consume. Calling a module with a
source address pulls it from a registry or git repository. Modules keep large
configurations DRY and let teams share vetted building blocks. Running terraform
plan previews changes before terraform apply provisions real cloud resources.
