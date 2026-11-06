#!/bin/sh
# Starts a single-process SeaweedFS (master, volume, filer, S3) for local development.
#
# Writes the S3 identity file from the environment, then hands over to the image's
# entrypoint, which drops root. The only identity is the application's, limited to the
# uploads bucket. There is no admin identity and no anonymous identity, so every
# unsigned request is rejected. The server creates the bucket itself (-bucket) and
# answers CORS for the frontend origins (-s3.allowedOrigins), so no admin key is needed
# for setup.
set -eu

: "${S3_ACCESS_KEY_ID:?S3_ACCESS_KEY_ID is required}"
: "${S3_SECRET_ACCESS_KEY:?S3_SECRET_ACCESS_KEY is required}"
: "${S3_BUCKET_UPLOADS:?S3_BUCKET_UPLOADS is required}"
: "${S3_ALLOWED_ORIGINS:?S3_ALLOWED_ORIGINS is required}"

config=/etc/seaweedfs/s3.json
umask 077
cat > "$config" <<JSON
{
  "identities": [
    {
      "name": "app",
      "credentials": [
        {"accessKey": "${S3_ACCESS_KEY_ID}", "secretKey": "${S3_SECRET_ACCESS_KEY}"}
      ],
      "actions": [
        "Read:${S3_BUCKET_UPLOADS}",
        "Write:${S3_BUCKET_UPLOADS}",
        "List:${S3_BUCKET_UPLOADS}",
        "Tagging:${S3_BUCKET_UPLOADS}"
      ]
    }
  ]
}
JSON
chown seaweed:seaweed "$config"

exec /entrypoint.sh mini \
  -dir=/data \
  -bucket="${S3_BUCKET_UPLOADS}" \
  -s3.config="$config" \
  -s3.allowedOrigins="${S3_ALLOWED_ORIGINS}" \
  -s3.iam=false \
  -webdav=false \
  -admin.ui=false \
  -s3.port.iceberg=0 \
  -s3.port.lance=0
