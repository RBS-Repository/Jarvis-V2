import asyncio
import re
import threading
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np
import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    add_conversation_entry, get_recent_context,
)

# --- OPENWAKEWORD IMPORT ---
try:
    # Set cache directory before importing to avoid OneDrive permission issues
    import os
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    cache_dir = os.path.join(desktop, ".openwakeword_cache")
    os.makedirs(cache_dir, exist_ok=True)
    os.environ["XDG_CACHE_HOME"] = cache_dir
    
    from openwakeword.model import Model as OWWModel
    _OWW_OK = True
except ImportError:
    _OWW_OK = False

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.sticky_note       import write_sticky_note
from actions.google_workspace  import google_workspace
from actions.calendar          import calendar_action


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 23200  # Slightly lower than 24k to deepen the voice and slow down pacing
CHUNK_SIZE          = 1280

def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information. Can save results directly to a Word document with save_as_docx parameter.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"},
                "save_as_docx": {"type": "BOOLEAN", "description": "Set to true to save search results directly to a Word document with proper formatting"},
                "filename": {"type": "STRING", "description": "Filename for the Word document (used with save_as_docx, defaults to search_results.docx)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "set_standby",
        "description": "Sets JARVIS to standby mode (sleep) or wakes him up.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "enabled": {
                    "type": "BOOLEAN",
                    "description": "True to go to sleep, False to wake up."
                }
            },
            "required": ["enabled"]
        }
    },
    {
        "name": "save_memory",
        "description": "Stores important information about the user or preferences into long-term memory.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {"type": "STRING", "description": "Category: identity, preferences, relationships, etc."},
                "key":      {"type": "STRING", "description": "Short unique identifier for the info"},
                "value":    {"type": "STRING", "description": "The information content"}
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "write_sticky_note",
        "description": "Writes a list, task, or note to Windows Sticky Notes.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": {"type": "STRING", "description": "The exact text or list to write. Format it with line breaks if it's a list."}
            },
            "required": ["text"]
        }
    },
    {
        "name": "send_message",
        "description": "Sends a text message via WhatsApp, Telegram, or other messaging platform.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube. Use for: playing videos, summarizing a video's content, "
            "getting video info, or showing trending videos."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls any web browser. Use for: opening websites, searching the web, "
            "clicking elements, filling forms, scrolling, screenshots, navigation, any web-based task. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | open_first | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage. Can create Excel (.xlsx) and Word (.docx) files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_excel | create_word | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write/create_word"},
                "data":        {"type": "STRING", "description": "Data for create_excel (CSV format or simple text)"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_jarvis",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Jarvis. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "Can also create Word documents from text content with create_docx parameter. "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
            "create_docx": {"type": "BOOLEAN", "description": "Set to true to create a Word document from text content"},
            "content": {"type": "STRING", "description": "Text content to create Word document from (used with create_docx)"},
            "filename": {"type": "STRING", "description": "Filename for the Word document to create (used with create_docx)"},
            "output_dir": {"type": "STRING", "description": "Output directory for the Word document (optional, defaults to Jarvis directory)"},
        },
        "required": []
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "google_workspace",
        "description": "Interacts with Google Calendar and Gmail. Can open calendar/gmail, add calendar events, check calendar schedule, and compose emails.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open_calendar | open_gmail | add_event | check_calendar | compose_email | check_email"},
                "title":       {"type": "STRING", "description": "Title for calendar event"},
                "date":        {"type": "STRING", "description": "Date for event/calendar view (e.g. '2026-06-10', 'tomorrow')"},
                "time":        {"type": "STRING", "description": "Time for event (e.g. '14:00', '2pm')"},
                "duration":    {"type": "INTEGER", "description": "Duration in minutes (default 60)"},
                "description": {"type": "STRING", "description": "Event description"},
                "location":    {"type": "STRING", "description": "Event location"},
                "view":        {"type": "STRING", "description": "day | week | month for check_calendar"},
                "to":          {"type": "STRING", "description": "Recipient email address for compose"},
                "subject":     {"type": "STRING", "description": "Email subject"},
                "body":        {"type": "STRING", "description": "Email body content"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "calendar",
        "description": "Manages Google Calendar events: list, create, delete, and update events.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "list | create | delete | update"},
                "summary": {"type": "STRING", "description": "Event title (for create/update)"},
                "description": {"type": "STRING", "description": "Event description (for create/update)"},
                "location": {"type": "STRING", "description": "Event location (for create/update)"},
                "start_time": {"type": "STRING", "description": "Start time in ISO format (for create/update), e.g., '2024-01-01T10:00:00'"},
                "end_time": {"type": "STRING", "description": "End time in ISO format (for create/update), e.g., '2024-01-01T11:00:00'"},
                "event_id": {"type": "STRING", "description": "Event ID (for delete/update)"},
                "max_results": {"type": "INTEGER", "description": "Maximum number of events to return (for list, default: 10)"}
            },
            "required": ["action"]
        }
    },
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self.standby        = False
        self._loop          = None
        self._is_speaking   = False
        self._interrupted   = False
        self._speaking_lock = threading.Lock()
        self._standby_enter_time = 0 # To prevent immediate re-wake
        self._init_oww() # Init wake word engine
        self.ui.on_text_command = self._on_text_command
        self._turn_done_event: asyncio.Event | None = None

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif self.standby:
            self.ui.set_state("STANDBY")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns=[types.Content(parts=[types.Part(text=text)])],
                turn_complete=True
            ),
            self._loop
        )

    def _init_oww(self):
        if _OWW_OK:
            try:
                print("[JARVIS] 🧠 Initializing Wake Word Engine...")
                self._oww_model = OWWModel(wakeword_models=["jarvis"], inference_framework="onnx")
                print("[JARVIS] ✅ Wake Word Engine Ready (Jarvis)")
            except Exception as e:
                print(f"[JARVIS] ⚠️ Wake Word Initialization failed: {e}")
                print("[JARVIS] ℹ️  Continuing without wake word detection. You can still use the UI.")
                self._oww_model = None
        else:
            print("[JARVIS] ℹ️  OpenWakeWord not installed. Wake word detection disabled.")
            self._oww_model = None

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()
        context_str = get_recent_context(limit=5)

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if context_str:
            parts.append(context_str)
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        # Truncate long parameter values for cleaner logging
        truncated_args = {}
        for key, value in args.items():
            if isinstance(value, str) and len(value) > 100:
                truncated_args[key] = value[:97] + "..."
            else:
                truncated_args[key] = value
        print(f"[JARVIS] 🔧 {name}  {truncated_args}")
        self.ui.set_state("THINKING")
        if not self.ui.muted and not getattr(self, "standby", False):
            self.ui.play_sound("working.mp3")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown sequence initiated.")
                def _shutdown():
                    import time, os
                    # 7 seconds allows the long sign-off string to finish speaking
                    time.sleep(7)
                    print("[JARVIS] 🔴 Systems offline. Shutdown complete.")
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()
                result = "Shutdown sequence initiated. Please finish your final sign-off, JARVIS."

            elif name == "write_sticky_note":
                text = args.get("text", "")
                result = write_sticky_note(text)

            elif name == "google_workspace":
                r = await loop.run_in_executor(None, lambda: google_workspace(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "spotify_control":
                r = await loop.run_in_executor(None, lambda: spotify_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "calendar":
                r = await loop.run_in_executor(None, lambda: calendar_action(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or "Done."

            elif name == "set_standby":
                enabled = args.get("enabled", False)
                self.standby = enabled
                if enabled:
                    self._standby_enter_time = time.time()
                    if self._oww_model:
                        try: self._oww_model.reset()
                        except: pass
                    
                    def _play_standby():
                        import time
                        time.sleep(6.5) # Wait for speech to finish
                        self.ui.play_sound("standby.mp3")
                    threading.Thread(target=_play_standby, daemon=True).start()
                    
                    self.ui.set_state("STANDBY")
                    self.ui.write_log("SYS: Standby mode activated.")
                    result = "Standby mode activated. I am now silent until you say 'Wake up, Jarvis'."
                else:
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: Systems active.")
                    result = "I am awake and at your service, sir."

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            new_state = "STANDBY" if self.standby else "LISTENING"
            self.ui.set_state(new_state)

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if self.ui.muted:
                return

            # Simple Volume Calculation
            volume = np.linalg.norm(indata) / np.sqrt(len(indata))
            
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking

            # Standard Listening Logic:
            # If JARVIS is talking, we usually don't want to send mic data 
            # unless we want barge-in. Since we now use a manual button,
            # we'll only listen when he's silent or if a button is pushed.
            # But the server is good at filtering echo, so we can just send.
            
            # Calculate audio level for visualization
            audio_level = np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / 32768.0
            loop.call_soon_threadsafe(self.ui._win.hud.update_audio_level, min(1.0, audio_level * 5.0), True)
            
            # Apply noise reduction
            try:
                import noisereduce as nr
                # Convert to float for noise reduction
                audio_float = indata.astype(np.float32)
                # Simple noise gate - reduce low-level noise
                noise_gate_threshold = 0.01
                if audio_level < noise_gate_threshold:
                    audio_float = audio_float * 0.1  # Attenuate low-level noise
                indata = audio_float.astype(np.int16)
            except ImportError:
                pass  # noisereduce not installed, skip noise reduction
            except Exception as e:
                if self._tick % 100 == 0:
                    print(f"[JARVIS] ⚠️ Noise reduction error: {e}")
            
            if not jarvis_speaking:
                if self.standby and self._oww_model:
                    # LOCAL WAKE WORD DETECTION
                    if time.time() - self._standby_enter_time < 5.0:
                        return # Cooling down to prevent immediate re-wake

                    try:
                        raw_data = indata.flatten()
                        predictions = self._oww_model.predict(raw_data)
                        
                        # Log scores periodically or if any score > 0.1 to avoid spam
                        for m_name, score in predictions.items():
                            if score > 0.12:
                                print(f"[OWW] Detect: {m_name} | Score: {score:.3f}")
                            
                            # WAKE LOGIC - Extreme sensitivity for "Jarvis"
                            if score > 0.15:
                                print(f"[JARVIS] 🔔 Wake word triggered by '{m_name}' (Score: {score:.2f})")
                                self.standby = False
                                loop.call_soon_threadsafe(self.ui.set_state, "LISTENING")
                                loop.call_soon_threadsafe(self.ui.play_sound, "startup.mp3")
                                loop.call_soon_threadsafe(self.ui.write_log, "SYS: Wake word detected.")
                                # SYNCHRONIZE WITH SERVER
                                self.speak("Wake up, Jarvis.")
                                break
                    except Exception as e:
                        if self._tick % 100 == 0:
                            print(f"[JARVIS] ⚠️ OWW Predict error: {e}")
                elif not self.standby:
                    # NORMAL STREAMING TO SERVER
                    data = indata.tobytes()
                    loop.call_soon_threadsafe(
                        self.out_queue.put_nowait,
                        {"data": data, "mime_type": "audio/pcm"}
                    )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        self._interrupted = False # Reset on new chunk
                        if self._turn_done_event and self._turn_done_event.is_set():
                            self._turn_done_event.clear()
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []
                            
                            # Track conversation for context-aware responses
                            if full_in and full_out:
                                try:
                                    add_conversation_entry(full_in, full_out)
                                except Exception as e:
                                    print(f"[JARVIS] ⚠️ Conversation tracking error: {e}")

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            
                            # IMMEDIATE WAKE-UP GUARD:
                            # If we see a wake-up call, drop standby immediately 
                            # so the upcoming audio doesn't get discarded.
                            if fc.name == "set_standby":
                                try:
                                    # Peek at args to see if it's wake up
                                    if not fc.args.get("enabled", True):
                                        self.standby = False
                                except: pass

                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            if "1006" not in str(e) and "1011" not in str(e):
                print(f"[JARVIS] ❌ Recv error: {e}")
            raise

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        import numpy as np
        
        # Ring modulation parameters 
        mod_freq = 40.0  
        phase = 0.0      
        
        # Echo / Reverb parameters
        echo_delay_ms = 80.0 # 70 ms creates a nice spacious "helmet" or room echo
        delay_samples = int(RECEIVE_SAMPLE_RATE * (echo_delay_ms / 1000.0))
        echo_buffer = np.zeros(0, dtype=np.float32)

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.1
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                
                self.set_speaking(True)

                # Check if we've been interrupted since the last chunk
                if self._interrupted:
                    self.set_speaking(False)
                    continue

                # --- APPLY ROBOTIC & ECHO EFFECT ---
                audio_data = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                chunk_len = len(audio_data)
                
                # 1. Ring Modulator (Robotic flutter)
                t = (np.arange(chunk_len) + phase) / RECEIVE_SAMPLE_RATE
                sine_wave = np.sin(2 * np.pi * mod_freq * t)
                phase += chunk_len

                # Slightly reduce flutter (75/25) so the echo doesn't get too messy
                ring_mod = audio_data * sine_wave
                blended = (audio_data * 0.75) + (ring_mod * 0.25)
                
                # 2. Echo (Helmet / Room Reverb)
                echo_buffer = np.concatenate((echo_buffer, blended))
                echo_signal = np.zeros(chunk_len, dtype=np.float32)
                
                # If we have enough history, grab the delayed chunk
                if len(echo_buffer) >= delay_samples + chunk_len:
                    echo_signal = echo_buffer[-(delay_samples + chunk_len):-delay_samples]
                elif len(echo_buffer) > delay_samples:
                    available = len(echo_buffer) - delay_samples
                    echo_signal[-available:] = echo_buffer[-len(echo_buffer):-delay_samples]

                # Cap buffer length to save memory (max 1 second of audio)
                if len(echo_buffer) > RECEIVE_SAMPLE_RATE:
                    echo_buffer = echo_buffer[-RECEIVE_SAMPLE_RATE:]

                # Mix current audio with the echo (echo volume = 35%)
                final_audio = blended + (echo_signal * 0.35)
                
                # Convert back to audio bytes
                chunk = np.clip(final_audio, -32768, 32767).astype(np.int16).tobytes()
                # ---------------------------------
                
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    def _handle_interrupt(self):
        """Clears playback queue to stop JARVIS from speaking immediately."""
        self._interrupted = True
        if hasattr(self, 'audio_in_queue'):
            while not self.audio_in_queue.empty():
                try: self.audio_in_queue.get_nowait()
                except: break
        print("[JARVIS] 🛑 Manual interruption handled")

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        while True:
            try:
                print("[JARVIS] 🔌 Connecting...")
                self.ui.set_state("THINKING")
                config = self._build_config()

                async with (
                    client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue()
                    self._turn_done_event = asyncio.Event()

                    print("[JARVIS] ✅ Connected.")
                    self.ui.play_startup_sound()
                    await asyncio.sleep(2.0)

                    self.ui.set_state("LISTENING")

                    self.ui.write_log("SYS: JARVIS online.")

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

                    # Auto-trigger the first greeting
                    await self.session.send_client_content(
                        turns=[types.Content(parts=[types.Part(text="Hello. I'm online.")])],
                        turn_complete=True
                    )

            except BaseExceptionGroup as eg:
                # TaskGroup raises an ExceptionGroup. Check the full traceback if it's a routine connection drop
                full_trace = "".join(traceback.format_exception(type(eg), eg, eg.__traceback__))
                if "1006" in full_trace or "1011" in full_trace or "TimeoutError" in full_trace:
                    print("[JARVIS] ⚠️ Server connection reset. Restarting...")
                else:
                    print(f"[JARVIS] ⚠️ Unhandled connection error:")
                    traceback.print_exc()
            except Exception as e:
                print(f"[JARVIS] ⚠️ Exception: {e}")
                traceback.print_exc()
                
            self.set_speaking(False)
            if not self.standby:
                self.ui.set_state("THINKING")
            print("[JARVIS] 🔄 Reconnecting in 3s...")
            await asyncio.sleep(3)

def main():
    import os
    import sys
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    sys.argv.extend(["-platform", "windows:dpiawareness=0"])
    
    ui = JarvisUI("face.png")
    class UILogger:
        def __init__(self, ui, old_out):
            self.ui = ui
            self.old = old_out
            self.buf = ""
        def write(self, text):
            self.old.write(text)
            self.buf += text
            if '\n' in self.buf:
                lines = self.buf.split('\n')
                for line in lines[:-1]:
                    clean_line = line.strip()
                    if clean_line and "[UI] State Transition" not in clean_line and "Could not update timestamps" not in clean_line and "mp3float" not in clean_line:
                        try:
                            # Use loop call_soon_threadsafe if possible, else direct
                            self.ui.write_log(clean_line)
                        except: pass
                self.buf = lines[-1]
        def flush(self):
            self.old.flush()

    sys.stdout = UILogger(ui, sys.stdout)

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        ui.on_interrupt = jarvis._handle_interrupt
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()