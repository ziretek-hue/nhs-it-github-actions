# NHS IT Band 4/5 visa sponsorship job alert

This project runs a scheduled GitHub Actions job that searches NHS Jobs for Band 4 and Band 5 IT-related roles with positive visa sponsorship wording, then emails the result to `kennethoseinimako@gmail.com`.

## What it checks.

- Band 4 and Band 5 NHS Jobs listings.
- IT, digital, informatics, service desk, application support, systems, clinical systems, cyber, data, and infrastructure keywords.
- Positive sponsorship wording such as Skilled Worker sponsorship, Health and Care Worker visa, visa sponsorship eligibility, or Certificate of Sponsorship.
- Excludes listings that say sponsorship is unavailable, not eligible, or require existing right to work.

## GitHub setup

1. Create a private GitHub repository.
2. Add these files to the repository.
3. In GitHub, open the repository settings.
4. Go to **Secrets and variables** -> **Actions** -> **New repository secret**.
5. Add the SMTP secrets below.
6. Open the **Actions** tab and enable workflows if GitHub asks.

## Required secrets

Use the same values from your existing local SMTP setup:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`

Recommended optional secrets:

- `EMAIL_FROM`
- `EMAIL_REPLY_TO`
- `SMTP_USE_SSL`
- `SMTP_USE_STARTTLS`

For the SMTP mode you used locally, `SMTP_USE_STARTTLS` is usually `true` and `SMTP_USE_SSL` is usually `false`, unless your provider uses SSL on port 465.

## Schedule

The workflow runs every day at 08:30 UTC, which is 09:30 in London during British Summer Time. You can also run it manually from GitHub:

**Actions** -> **NHS IT job alert** -> **Run workflow**

## Local test

To test without sending:

```bash
python scripts/nhs_it_band_4_5_email_alert.py --dry-run
```

To send locally, set the same SMTP environment variables and run:

```bash
python scripts/nhs_it_band_4_5_email_alert.py
```
