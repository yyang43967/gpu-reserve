import os

# The settings class has no default signing key on purpose. Tests supply their own
# before any application module builds its settings.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-for-real-use-0123456789")
