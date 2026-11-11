# Outgoing mail from a subdomain, so the apex stays free for other uses. DNS is on
# Cloudflare: the DKIM records are outputs to add there by hand.

resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.email_domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

# While the account is in the SES sandbox, mail only reaches verified addresses.
# Each address here gets a verification email from AWS when it is created.
resource "aws_sesv2_email_identity" "recipient" {
  for_each       = toset(var.ses_sandbox_recipients)
  email_identity = each.value
}
