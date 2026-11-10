import os

# The settings class has no default signing key on purpose. Tests supply their own
# before any application module builds its settings.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-real-use-0123456789")

# A developer's backend/.env may hold real keys. Environment variables take precedence
# over the env file, and blank values mean "unset", so no test can reach a paid API or
# send traces, whatever is on disk.
for _name in (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "VLLM_BASE_URL",
    "VLLM_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
):
    os.environ[_name] = ""

# Without explicit keys, boto3's credential chain asks the EC2 metadata service. Tests
# never run on an instance, and the network block would turn that lookup into an error.
os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
