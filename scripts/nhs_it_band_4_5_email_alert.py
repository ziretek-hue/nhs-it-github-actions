#!/usr/bin/env python3
"""Search NHS Jobs for Band 4/5 IT sponsorship roles and email an alert."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path


BASE = "https://www.jobs.nhs.uk"
TODAY = dt.date.today()

KEYWORDS = [
    "information technology",
    "IT support",
    "digital",
    "informatics",
    "service desk",
    "application support",
    "systems",
    "clinical systems",
    "cyber",
    "data",
    "infrastructure",
]

BANDS = {
    "Band 4": "BAND_4",
    "Band 5": "BAND_5",
}

IT_TITLE_PATTERN = (
    r"\bIT\b|I\.T\.|ICT|IM&T|information|technology|digital|informatics|"
    r"service desk|help ?desk|desktop|technical support|support analyst|"
    r"systems?|applications?|clinical systems?|EPR|EPMA|data|database|"
    r"business intelligence|\bBI\b|cyber|network (engineer|analyst|administrator|"
    r"manager|support|technician)|infrastructure|telecom|"
    r"developer|software|integration"
)

POSITIVE_PATTERNS = [
    r"applications from job seekers who require current skilled worker sponsorship.*?welcome",
    r"applications from individuals who require a skilled worker sponsorship.*?welcome",
    r"this role is eligible for visa sponsorship",
    r"role is eligible for visa sponsorship",
    r"eligible for visa sponsorship under",
    r"health and care worker visa",
    r"certificate of sponsorship",
]

NEGATIVE_PATTERNS = [
    r"not eligible for (?:skilled worker )?visa sponsorship",
    r"unable to (?:offer|provide|consider).*?sponsorship",
    r"does not meet .*?visa sponsorship",
    r"do not meet .*?sponsorship",
    r"must already have the right to work",
    r"applicants must already have the right to work",
    r"will not be able to sponsor",
    r"not able to sponsor",
    r"unfortunately ineligible to apply",
    r"requires? current skilled worker sponsorship.*?ineligible",
    r"requiring current skilled worker sponsorship.*?ineligible",
]


@dataclass
class Candidate:
    band: str
    keyword: str
    title: str
    employer: str
    location: str
    salary: str
    date_posted: str
    closing_date: str
    url: str


@dataclass
class Job:
    band: str
    title: str
    employer: str
    location: str
    salary: str
    date_posted: str
    closing_date: str
    url: str
    evidence: str


def build_search_url(keyword: str, pay_band: str, page_no: int = 1) -> str:
    query = {
        "keyword": keyword,
        "sort": "publicationDateDesc",
        "skipPhraseSuggester": "true",
        "payBand": pay_band,
    }
    if page_no > 1:
        query["page"] = str(page_no)
    return BASE + "/candidate/search/results?" + urllib.parse.urlencode(query)


def fetch(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 GitHub Actions NHS jobs alert",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def clean(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(value.strip(), "%d %B %Y").date()
    except ValueError:
        return None


def page_text(page: str) -> str:
    return clean(re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page))


def parse_title(page: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.S)
    return clean(match.group(1)) if match else ""


def extract_search_results(page: str, band: str, keyword: str) -> list[Candidate]:
    results = []
    blocks = re.findall(
        r'<li class="nhsuk-list-panel search-result.*?</li>\s*</ul>|'
        r'<li class="nhsuk-list-panel search-result.*?</li>',
        page,
        flags=re.S,
    )

    if not blocks:
        blocks = re.findall(
            r'<li class="nhsuk-list-panel search-result.*?(?=<li class="nhsuk-list-panel search-result|</ul>)',
            page,
            flags=re.S,
        )

    for block in blocks:
        link_match = re.search(
            r'<a href="([^"]*?/candidate/jobadvert/[^"]+)"[^>]*data-test="search-result-job-title"[^>]*>(.*?)</a>',
            block,
            flags=re.S,
        )
        if not link_match:
            continue

        url = html.unescape(link_match.group(1))
        if url.startswith("/"):
            url = BASE + url
        url = url.split("?")[0]

        title = clean(link_match.group(2))
        employer_match = re.search(
            r'data-test="search-result-location".*?<h3[^>]*>(.*?)<div',
            block,
            flags=re.S,
        )
        location_match = re.search(r'<div class="location-font-size">\s*(.*?)\s*</div>', block, flags=re.S)
        salary_match = re.search(
            r'data-test="search-result-salary".*?<strong[^>]*>(.*?)</strong>',
            block,
            flags=re.S,
        )
        posted_match = re.search(
            r'data-test="search-result-publicationDate".*?<strong[^>]*>(.*?)</strong>',
            block,
            flags=re.S,
        )
        closing_match = re.search(
            r'data-test="search-result-closingDate".*?<strong[^>]*>(.*?)</strong>',
            block,
            flags=re.S,
        )

        results.append(
            Candidate(
                band=band,
                keyword=keyword,
                title=title,
                employer=clean(employer_match.group(1)) if employer_match else "",
                location=clean(location_match.group(1)) if location_match else "",
                salary=clean(salary_match.group(1)) if salary_match else "",
                date_posted=clean(posted_match.group(1)) if posted_match else "",
                closing_date=clean(closing_match.group(1)) if closing_match else "",
                url=url,
            )
        )
    return results


def find_evidence(text: str) -> str:
    for pattern in POSITIVE_PATTERNS:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return ""


def has_negative_sponsorship(text: str) -> bool:
    lower = text.lower()
    return any(re.search(pattern, lower, flags=re.S) for pattern in NEGATIVE_PATTERNS)


def gather_candidates(pages: int) -> tuple[list[Candidate], list[tuple[str, str, str]]]:
    candidates_by_url: dict[str, Candidate] = {}
    searches_checked = []

    for band_label, pay_band in BANDS.items():
        for keyword in KEYWORDS:
            for page_no in range(1, pages + 1):
                url = build_search_url(keyword, pay_band, page_no)
                searches_checked.append((band_label, keyword, url))
                try:
                    page = fetch(url)
                except Exception as exc:
                    print(f"Search failed for {band_label} / {keyword}: {exc}", file=sys.stderr)
                    continue

                for candidate in extract_search_results(page, band_label, keyword):
                    if not re.search(IT_TITLE_PATTERN, candidate.title, flags=re.I):
                        continue
                    if candidate.url not in candidates_by_url:
                        candidates_by_url[candidate.url] = candidate
                time.sleep(0.15)

    return list(candidates_by_url.values()), searches_checked


def find_jobs(pages: int, max_details: int) -> tuple[list[Job], list[tuple[str, str, str]]]:
    candidates, searches_checked = gather_candidates(pages)
    jobs = []

    for candidate in candidates[:max_details]:
        closing = parse_date(candidate.closing_date)
        if closing and closing < TODAY:
            continue

        try:
            detail = fetch(candidate.url)
        except Exception as exc:
            print(f"Detail fetch failed for {candidate.url}: {exc}", file=sys.stderr)
            continue

        text = page_text(detail)
        if "this job is now closed" in text.lower():
            continue
        if has_negative_sponsorship(text):
            continue

        evidence = find_evidence(text)
        if not evidence:
            continue

        title = parse_title(detail) or candidate.title
        if not re.search(IT_TITLE_PATTERN, title, flags=re.I):
            continue

        jobs.append(
            Job(
                band=candidate.band,
                title=title,
                employer=candidate.employer,
                location=candidate.location,
                salary=candidate.salary,
                date_posted=candidate.date_posted,
                closing_date=candidate.closing_date,
                url=candidate.url,
                evidence=evidence,
            )
        )
        time.sleep(0.2)

    return jobs, searches_checked


def escape(value: str) -> str:
    return html.escape(value or "", quote=True)


def render_text(jobs: list[Job], searches_checked: list[tuple[str, str, str]]) -> str:
    grouped: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        grouped[job.band].append(job)

    lines = [
        f"NHS IT Band 4/5 visa sponsorship alert - {TODAY.strftime('%d %B %Y')}",
        "",
        f"Found {len(jobs)} clear open match{'es' if len(jobs) != 1 else ''}.",
        "",
    ]

    if not jobs:
        lines.extend(
            [
                "No clear open NHS IT Band 4 or Band 5 listings with positive visa/Skilled Worker sponsorship wording were found today.",
                "",
                "Listings that explicitly said sponsorship is unavailable, not eligible, or require existing right to work were excluded.",
                "",
                "Searches checked:",
            ]
        )
        lines.extend(f"- {band}: {keyword} - {url}" for band, keyword, url in searches_checked)
        return "\n".join(lines).rstrip() + "\n"

    for band_label in BANDS:
        lines.extend([band_label, "=" * len(band_label), ""])
        band_jobs = grouped.get(band_label, [])
        if not band_jobs:
            lines.extend(["No clear matches found.", ""])
            continue

        for index, job in enumerate(band_jobs, 1):
            lines.extend(
                [
                    f"{index}. {job.title}",
                    f"Employer: {job.employer}",
                    f"Location: {job.location}",
                    f"Salary/Band: {job.salary}",
                    f"Date posted: {job.date_posted}",
                    f"Closing date: {job.closing_date}",
                    f"Sponsorship evidence: {job.evidence}",
                    f"Apply: {job.url}",
                    "",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def render_html(jobs: list[Job], searches_checked: list[tuple[str, str, str]]) -> str:
    grouped: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        grouped[job.band].append(job)

    sections = []
    for band_label in BANDS:
        cards = []
        band_jobs = grouped.get(band_label, [])
        if not band_jobs:
            cards.append('<p class="empty">No clear matches found.</p>')

        for index, job in enumerate(band_jobs, 1):
            cards.append(
                f"""
                <article class="job-card">
                  <div class="job-card-top">
                    <span class="job-number">{escape(band_label)} match {index}</span>
                    <span class="deadline">{escape(job.closing_date or "Closing date listed")}</span>
                  </div>
                  <h3>{escape(job.title)}</h3>
                  <p class="employer">{escape(job.employer)}</p>
                  <div class="meta-grid">
                    <div><span>Location</span><strong>{escape(job.location)}</strong></div>
                    <div><span>Salary/Band</span><strong>{escape(job.salary)}</strong></div>
                    <div><span>Posted</span><strong>{escape(job.date_posted)}</strong></div>
                    <div><span>Closing</span><strong>{escape(job.closing_date)}</strong></div>
                  </div>
                  <div class="sponsorship">
                    <span>Sponsorship evidence</span>
                    <p>{escape(job.evidence)}</p>
                  </div>
                  <a class="apply-button" href="{escape(job.url)}">View and apply</a>
                </article>
                """
            )

        sections.append(
            f"""
            <section class="band-section">
              <h2>{escape(band_label)}</h2>
              {''.join(cards)}
            </section>
            """
        )

    if not jobs:
        search_items = "".join(
            f'<li><a href="{escape(url)}">{escape(band)}: {escape(keyword)}</a></li>'
            for band, keyword, url in searches_checked
        )
        sections = [
            f"""
            <section class="empty-state">
              <h2>No clear open matches today</h2>
              <p>No NHS IT Band 4 or Band 5 listings with positive visa or Skilled Worker sponsorship wording were found.</p>
              <p>Listings that said sponsorship is unavailable, not eligible, or require existing right to work were excluded.</p>
              <ul>{search_items}</ul>
            </section>
            """
        ]

    date_label = TODAY.strftime("%d %B %Y")
    count_label = f"{len(jobs)} open match{'es' if len(jobs) != 1 else ''}"
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>NHS IT Band 4/5 visa sponsorship jobs</title>
    <style>
      body {{ margin: 0; padding: 0; background: #eef3f8; color: #17202a; font-family: Arial, Helvetica, sans-serif; }}
      a {{ color: #005eb8; }}
      .page {{ width: 100%; background: #eef3f8; padding: 24px 0; }}
      .container {{ max-width: 760px; margin: 0 auto; background: #ffffff; border: 1px solid #d8e1ea; }}
      .hero {{ background: #005eb8; color: #ffffff; padding: 28px 28px 24px; }}
      .eyebrow {{ margin: 0 0 8px; color: #d6ecff; font-size: 13px; font-weight: 700; text-transform: uppercase; }}
      h1 {{ margin: 0; color: #ffffff; font-size: 28px; line-height: 1.18; }}
      .hero-pill {{ display: inline-block; margin: 18px 8px 0 0; padding: 8px 11px; border: 1px solid rgba(255,255,255,0.45); background: rgba(255,255,255,0.13); color: #ffffff; font-size: 14px; font-weight: 700; }}
      .content {{ padding: 24px; }}
      .band-section {{ margin: 0 0 24px; }}
      .band-section h2 {{ margin: 0 0 14px; color: #17202a; font-size: 24px; }}
      .job-card {{ margin: 0 0 18px; padding: 22px; border: 1px solid #ccd8e3; background: #ffffff; }}
      .job-card-top {{ display: table; width: 100%; margin-bottom: 12px; }}
      .job-number {{ display: inline-block; padding: 5px 9px; background: #e8f1fb; color: #004b93; font-size: 13px; font-weight: 700; }}
      .deadline {{ float: right; display: inline-block; padding: 5px 9px; background: #fff4d6; color: #5d4300; font-size: 13px; font-weight: 700; }}
      h3 {{ margin: 0 0 6px; color: #17202a; font-size: 22px; line-height: 1.25; }}
      .employer {{ margin: 0 0 18px; color: #4d5c68; font-size: 15px; font-weight: 700; }}
      .meta-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 0 0 16px; }}
      .meta-grid div {{ padding: 12px; background: #f6f9fc; border: 1px solid #e1e8ef; }}
      .meta-grid span {{ display: block; margin-bottom: 5px; color: #5d6b78; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
      .meta-grid strong {{ display: block; color: #17202a; font-size: 14px; line-height: 1.35; }}
      .sponsorship {{ margin: 0 0 18px; padding: 14px; border-left: 4px solid #007f61; background: #eef8f4; }}
      .sponsorship span {{ display: block; margin-bottom: 6px; color: #006747; font-size: 12px; font-weight: 700; text-transform: uppercase; }}
      .sponsorship p {{ margin: 0; color: #1d352d; font-size: 14px; line-height: 1.45; }}
      .apply-button {{ display: inline-block; padding: 12px 16px; background: #007f61; color: #ffffff !important; font-size: 15px; font-weight: 700; text-decoration: none; }}
      .empty-state {{ padding: 24px; border: 1px solid #ccd8e3; background: #f8fbfd; }}
      .empty, .empty-state p {{ color: #344552; font-size: 15px; line-height: 1.45; }}
      .footer {{ padding: 18px 24px 24px; color: #5d6b78; font-size: 12px; line-height: 1.45; }}
      @media screen and (max-width: 620px) {{
        .page {{ padding: 0; }}
        .container {{ width: 100%; border-left: 0; border-right: 0; }}
        .hero {{ padding: 24px 18px 20px; }}
        h1 {{ font-size: 24px; }}
        .content {{ padding: 16px; }}
        .job-card {{ padding: 18px; }}
        .deadline {{ float: none; margin-top: 8px; }}
        .meta-grid {{ display: block; }}
        .meta-grid div {{ margin-bottom: 8px; }}
      }}
    </style>
  </head>
  <body>
    <div class="page">
      <main class="container">
        <header class="hero">
          <p class="eyebrow">NHS Jobs alert</p>
          <h1>NHS IT Band 4/5 visa sponsorship jobs</h1>
          <span class="hero-pill">{escape(date_label)}</span>
          <span class="hero-pill">{escape(count_label)}</span>
          <span class="hero-pill">Skilled Worker wording checked</span>
        </header>
        <section class="content">
          {''.join(sections)}
        </section>
        <footer class="footer">
          Searches cover IT, digital, informatics, service desk, applications, systems, cyber, data, and infrastructure keywords on NHS Jobs.
        </footer>
      </main>
    </div>
  </body>
</html>
"""


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value or ""


def build_message(to_email: str, subject: str, body: str, html_body: str) -> EmailMessage:
    sender = env("EMAIL_FROM") or env("SMTP_USERNAME", required=True)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_email
    message["Subject"] = subject

    reply_to = env("EMAIL_REPLY_TO")
    if reply_to:
        message["Reply-To"] = reply_to

    message.set_content(body)
    message.add_alternative(html_body, subtype="html")
    return message


def send_message(message: EmailMessage) -> None:
    host = env("SMTP_HOST", required=True)
    port = int(env("SMTP_PORT", "587"))
    username = env("SMTP_USERNAME")
    password = env("SMTP_PASSWORD")
    use_ssl = env("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
    use_starttls = env("SMTP_USE_STARTTLS", "true").lower() in {"1", "true", "yes"}

    context = ssl.create_default_context()
    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=context) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        if use_starttls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=int(os.environ.get("NHS_SEARCH_PAGES", "1")))
    parser.add_argument("--details", type=int, default=int(os.environ.get("NHS_DETAIL_LIMIT", "40")))
    parser.add_argument("--to", default=os.environ.get("EMAIL_TO", "kennethoseinimako@gmail.com"))
    parser.add_argument("--subject", default="NHS IT Band 4/5 visa sponsorship jobs")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    jobs, searches_checked = find_jobs(args.pages, args.details)
    text_body = render_text(jobs, searches_checked)
    html_body = render_html(jobs, searches_checked)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "nhs_it_band_4_5_notification.txt").write_text(text_body, encoding="utf-8")
    (output_dir / "nhs_it_band_4_5_notification.html").write_text(html_body, encoding="utf-8")

    message = build_message(args.to, args.subject, text_body, html_body)
    if args.dry_run:
        print(message)
        return

    send_message(message)
    print(f"Sent email to {message['To']} with {len(jobs)} match(es).")


if __name__ == "__main__":
    main()
