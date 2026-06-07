"""
Google Workspace Integration – Calendar & Gmail
Uses browser_control (Playwright) to interact with Google Calendar and Gmail
through the user's logged-in Chrome profile.
"""
from __future__ import annotations

import webbrowser
import time
import subprocess
import os
from datetime import datetime, timedelta
from urllib.parse import quote


def google_workspace(*, parameters: dict, player=None, speak=None) -> str:
    action = parameters.get("action", "").lower().strip()

    if action == "open_calendar":
        return _open_calendar()
    elif action == "open_gmail":
        return _open_gmail()
    elif action == "add_event":
        return _add_event(parameters)
    elif action == "check_calendar":
        return _open_calendar_view(parameters)
    elif action == "compose_email":
        return _compose_email(parameters)
    elif action == "check_email":
        return _open_gmail()
    else:
        return f"Unknown google_workspace action: '{action}'. Available: open_calendar, open_gmail, add_event, check_calendar, compose_email, check_email"


def _get_chrome_command() -> list[str]:
    """Returns the Chrome launch command with Jasper profile."""
    local = os.environ.get("LOCALAPPDATA", "")
    chrome_path = os.path.join(local, "Google", "Chrome", "Application", "chrome.exe")
    if os.path.exists(chrome_path):
        return [chrome_path, "--profile-directory=Default"]
    # Fallback to default path
    return ["chrome", "--profile-directory=Default"]


def _launch_chrome_url(url: str) -> str:
    """Opens a URL in Chrome with the Jasper profile."""
    try:
        cmd = _get_chrome_command() + [url]
        subprocess.Popen(cmd, shell=False)
        return "opened"
    except Exception:
        # Fallback to webbrowser
        webbrowser.open(url)
        return "opened (fallback)"


def _open_calendar() -> str:
    result = _launch_chrome_url("https://calendar.google.com")
    return f"Google Calendar {result} in Chrome (Jasper profile)."


def _open_gmail() -> str:
    result = _launch_chrome_url("https://mail.google.com")
    return f"Gmail {result} in Chrome (Jasper profile)."


def _add_event(params: dict) -> str:
    """
    Creates a Google Calendar event using the quick-add URL.
    Params:
        title: str       – event title (required)
        date: str        – date string like "2026-06-10" or "tomorrow"
        time: str        – time string like "14:00" or "2pm"
        duration: int    – duration in minutes (default 60)
        description: str – event description
        location: str    – event location
    """
    title       = params.get("title", "New Event")
    date_str    = params.get("date", "")
    time_str    = params.get("time", "")
    duration    = int(params.get("duration", 60))
    description = params.get("description", "")
    location    = params.get("location", "")

    # Build Google Calendar create event URL
    # Format: https://calendar.google.com/calendar/render?action=TEMPLATE&...
    base_url = "https://calendar.google.com/calendar/render?action=TEMPLATE"

    # Title
    base_url += f"&text={quote(title)}"

    # Try to parse date/time into proper format
    start_dt = None
    if date_str:
        try:
            # Handle various date formats
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"]:
                try:
                    start_dt = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if start_dt is None:
                # Handle relative dates
                today = datetime.now()
                dl = date_str.lower()
                if "tomorrow" in dl:
                    start_dt = today + timedelta(days=1)
                elif "today" in dl:
                    start_dt = today
                elif "next week" in dl:
                    start_dt = today + timedelta(weeks=1)
                else:
                    start_dt = today  # default to today
        except Exception:
            start_dt = datetime.now()

    if start_dt is None:
        start_dt = datetime.now()

    # Apply time
    if time_str:
        try:
            # Parse time strings like "14:00", "2pm", "2:30 PM"
            for fmt in ["%H:%M", "%I:%M %p", "%I:%M%p", "%I%p", "%I %p"]:
                try:
                    t = datetime.strptime(time_str.strip().upper(), fmt)
                    start_dt = start_dt.replace(hour=t.hour, minute=t.minute, second=0)
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    end_dt = start_dt + timedelta(minutes=duration)

    # Format as Google Calendar date format: 20260610T140000
    date_fmt = "%Y%m%dT%H%M%S"
    base_url += f"&dates={start_dt.strftime(date_fmt)}/{end_dt.strftime(date_fmt)}"

    if description:
        base_url += f"&details={quote(description)}"
    if location:
        base_url += f"&location={quote(location)}"

    result = _launch_chrome_url(base_url)
    return (
        f"Google Calendar event creation page {result}. "
        f"Event: '{title}' on {start_dt.strftime('%B %d, %Y at %I:%M %p')} "
        f"({duration} minutes). Please confirm in the browser."
    )


def _open_calendar_view(params: dict) -> str:
    """Opens Google Calendar to a specific view/date."""
    date_str = params.get("date", "")
    view = params.get("view", "")  # day, week, month

    url = "https://calendar.google.com/calendar/r"

    if view:
        view_map = {"day": "day", "week": "week", "month": "month", "agenda": "agenda"}
        v = view_map.get(view.lower(), "week")
        url += f"/{v}"

    if date_str:
        try:
            for fmt in ["%Y-%m-%d", "%m/%d/%Y"]:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    url += f"/{dt.year}/{dt.month}/{dt.day}"
                    break
                except ValueError:
                    continue
        except Exception:
            pass

    result = _launch_chrome_url(url)
    return f"Google Calendar view {result} in Chrome (Jasper profile)."


def _compose_email(params: dict) -> str:
    """
    Opens Gmail compose window with pre-filled fields.
    Params:
        to: str      – recipient email
        subject: str – email subject
        body: str    – email body text
    """
    to      = params.get("to", "")
    subject = params.get("subject", "")
    body    = params.get("body", "")

    url = "https://mail.google.com/mail/?view=cm&fs=1"
    if to:
        url += f"&to={quote(to)}"
    if subject:
        url += f"&su={quote(subject)}"
    if body:
        url += f"&body={quote(body)}"

    result = _launch_chrome_url(url)
    fields = []
    if to:
        fields.append(f"To: {to}")
    if subject:
        fields.append(f"Subject: {subject}")
    field_str = ", ".join(fields) if fields else "blank compose"

    return f"Gmail compose window {result}. {field_str}."
