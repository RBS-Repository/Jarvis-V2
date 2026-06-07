# calendar.py
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    _GOOGLE_OK = True
except ImportError:
    _GOOGLE_OK = False

SCOPES = ['https://www.googleapis.com/auth/calendar']


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _get_credentials():
    """Get or create Google Calendar credentials."""
    base_dir = _get_base_dir()
    token_path = base_dir / "config" / "calendar_token.json"
    credentials_path = base_dir / "config" / "calendar_credentials.json"
    
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                return None, "Google Calendar credentials file not found. Please create calendar_credentials.json in config folder."
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                return None, f"Failed to authenticate: {e}"
        
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    
    return creds, None


def calendar_action(parameters: dict = None, response=None, player=None, session_memory=None) -> str:
    """Handle Google Calendar actions."""
    if not _GOOGLE_OK:
        return "google-api-python-client is not installed. Run: pip install google-api-python-client"
    
    params = parameters or {}
    action = params.get("action", "list")
    
    creds, error = _get_credentials()
    if error:
        return error
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        if action == "list":
            return _list_events(service, params)
        elif action == "create":
            return _create_event(service, params)
        elif action == "delete":
            return _delete_event(service, params)
        elif action == "update":
            return _update_event(service, params)
        else:
            return f"Unknown action: {action}. Available: list, create, delete, update"
    
    except Exception as e:
        return f"Calendar error: {e}"


def _list_events(service, params):
    """List calendar events."""
    try:
        now = datetime.utcnow().isoformat() + 'Z'
        time_min = params.get("time_min", now)
        max_results = params.get("max_results", 10)
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return "No upcoming events found."
        
        response = f"Found {len(events)} upcoming events:\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            summary = event.get('summary', 'No title')
            response += f"- {summary} at {start}\n"
        
        return response
    
    except Exception as e:
        return f"Failed to list events: {e}"


def _create_event(service, params):
    """Create a new calendar event."""
    try:
        summary = params.get("summary", "New Event")
        description = params.get("description", "")
        location = params.get("location", "")
        
        # Parse time
        start_time = params.get("start_time")
        end_time = params.get("end_time")
        
        if not start_time:
            return "start_time is required for creating events"
        
        # Try to parse time string
        try:
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        except:
            return f"Invalid start_time format: {start_time}. Use ISO format like '2024-01-01T10:00:00'"
        
        if end_time:
            try:
                end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            except:
                return f"Invalid end_time format: {end_time}. Use ISO format like '2024-01-01T11:00:00'"
        else:
            # Default to 1 hour duration
            end_dt = start_dt + timedelta(hours=1)
        
        event = {
            'summary': summary,
            'description': description,
            'location': location,
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'UTC',
            },
        }
        
        event = service.events().insert(calendarId='primary', body=event).execute()
        return f"Created event: {summary} at {start_time}"
    
    except Exception as e:
        return f"Failed to create event: {e}"


def _delete_event(service, params):
    """Delete a calendar event."""
    try:
        event_id = params.get("event_id")
        if not event_id:
            return "event_id is required for deleting events"
        
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return f"Deleted event with ID: {event_id}"
    
    except Exception as e:
        return f"Failed to delete event: {e}"


def _update_event(service, params):
    """Update a calendar event."""
    try:
        event_id = params.get("event_id")
        if not event_id:
            return "event_id is required for updating events"
        
        event = service.events().get(calendarId='primary', eventId=event_id).execute()
        
        if 'summary' in params:
            event['summary'] = params['summary']
        if 'description' in params:
            event['description'] = params['description']
        if 'location' in params:
            event['location'] = params['location']
        if 'start_time' in params:
            start_dt = datetime.fromisoformat(params['start_time'].replace('Z', '+00:00'))
            event['start'] = {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'UTC',
            }
        if 'end_time' in params:
            end_dt = datetime.fromisoformat(params['end_time'].replace('Z', '+00:00'))
            event['end'] = {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'UTC',
            }
        
        updated_event = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=event
        ).execute()
        
        return f"Updated event: {updated_event.get('summary', 'Event')}"
    
    except Exception as e:
        return f"Failed to update event: {e}"
