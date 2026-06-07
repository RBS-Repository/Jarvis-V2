import time
import subprocess
import pyautogui

def write_sticky_note(text: str) -> str:
    """Opens Windows Sticky Notes, creates a new note, and types the given text."""
    try:
        # Launch Sticky Notes UWP App
        subprocess.Popen('start shell:Appsfolder\\Microsoft.MicrosoftStickyNotes_8wekyb3d8bbwe!App', shell=True)
        time.sleep(2)  # Wait for it to open
        
        # Press Ctrl+N to create a new note
        pyautogui.hotkey('ctrl', 'n')
        time.sleep(1)  # Wait for the new note window
        
        # Type the text
        pyautogui.write(text, interval=0.01)
        
        return "Successfully wrote to a new Sticky Note."
    except Exception as e:
        return f"Failed to write to Sticky Note: {e}"
