import os
import asyncio
from pathlib import Path
from typing import List
import streamlit as st

from google.adk.agents import LlmAgent as Agent
from google.adk.tools import FunctionTool
from google.adk.apps import App
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# Page Config
st.set_page_config(page_title="Coffee Shop Monitor", page_icon="☕", layout="wide")

# --- CSS: Sirf GitHub aur Edit (pencil) icons ko hatane ke liye (Share button aur Menu safe rahenge) ---
hide_github_edit_style = """
    <style>
    header [data-testid="stToolbar"] a[href*="github.com"],
    header [data-testid="stToolbar"] button[aria-label="Edit app"] {
        display: none !important;
    }
    </style>
"""
st.markdown(hide_github_edit_style, unsafe_allow_html=True)

SANDBOX_CLI = '/usr/local/gcp/bin/sandbox'
IS_LOCAL_MODE = not Path(SANDBOX_CLI).exists()

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

def run_sandbox_process(args: list[str]):
    cmd = args[2:] if IS_LOCAL_MODE and args[:2] == ['do', '--'] else ([SANDBOX_CLI] + args if not IS_LOCAL_MODE else args)
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)

def execute_sandbox_command(command: str) -> str:
    """Executes arbitrary POSIX shell/bash commands inside sandbox."""
    mode = "LOCAL" if IS_LOCAL_MODE else "CLOUD RUN SANDBOX"
    print(f"[ADK Sandbox Tool] Starting {mode} shell run...")
    try:
        res = run_sandbox_process(['do', '--', '/bin/sh', '-c', command])
        if res.returncode != 0:
            return f"Execution Failed!\nExit Code: {res.returncode}\nStdout:\n{res.stdout}\nStderr:\n{res.stderr}"
        return res.stdout
    except Exception as err:
        return f"Internal Sandbox Tool Error: {str(err)}"

def get_sheets_service():
    """Initializes and returns the Google Sheets client service."""
    from google.auth import default
    from googleapiclient.discovery import build
    credentials, _ = default(scopes=[
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/cloud-platform'
    ])
    return build('sheets', 'v4', credentials=credentials)

def read_spreadsheet_values(spreadsheet_id: str, range_name: str) -> str:
    """Reads a range of cells from a Google Spreadsheet."""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
        rows = result.get('values', [])
        return str(rows) if rows else "No data found in the specified range."
    except Exception as e:
        return f"Read Error: {str(e)}"

def update_spreadsheet_values(spreadsheet_id: str, range_name: str, values: List[List[str]]) -> str:
    """Updates a range of cells in a Google Spreadsheet with the provided values."""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=range_name,
            valueInputOption="USER_ENTERED", body={'values': values}).execute()
        return f"Successfully updated {result.get('updatedCells')} cells in {range_name}."
    except Exception as e:
        return f"Write Error: {str(e)}"

def create_spreadsheet_tab(spreadsheet_id: str, tab_name: str) -> str:
    """Creates a new sheet tab in a Google Spreadsheet if it doesn't already exist."""
    try:
        service = get_sheets_service()
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in spreadsheet.get('sheets', []):
            if sheet.get('properties', {}).get('title') == tab_name:
                return f"Sheet tab '{tab_name}' already exists."
        body = {'requests': [{'addSheet': {'properties': {'title': tab_name}}}]}
        service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
        return f"Successfully created new sheet tab '{tab_name}'."
    except Exception as e:
        return f"Error creating sheet tab: {str(e)}"

# ==========================================
# ADK AGENT & RUNNER SETUP
# ==========================================

root_agent = Agent(
    name='secure_coding_assistant',
    description='ADK agent capable of executing shell commands and managing Google Spreadsheets.',
    model=os.environ.get('GEMINI_MODEL', 'gemini-3.1-flash-lite'),
    instruction=(
        f'You are an expert AI Business Analyst for a coffee shop during university graduation weekend.\n'
        f'The Google Spreadsheet ID you are managing is: "{SPREADSHEET_ID}". Use this ID for all sheet operations.\n'
        '1. Comparative Analysis Policy:\n'
        f'   - Ingest historical POS data from the "POS-2025" sheet tab using read_spreadsheet_values with spreadsheet_id="{SPREADSHEET_ID}".\n'
        '   - Receive the current graduation schedule directly from the manager\'s prompt.\n'
        '   - Write a python3 script via the sandbox tool to correlate and predict spikes.\n'
        '2. Bottleneck Diagnostics (Playbook):\n'
        '   - Analyze wait times and staffing needs.\n'
        '3. Human-in-the-Loop Policy:\n'
        '   - Present insights and ask: "Would you like me to add these tasks to your \'TODO-2026\' TODO list?"\n'
        '4. Post-Approval Policy:\n'
        '   - Verify/Create "TODO-2026" tab and append approved tasks upon confirmation.'
    ),
    tools=[
        FunctionTool(func=execute_sandbox_command),
        FunctionTool(func=read_spreadsheet_values),
        FunctionTool(func=update_spreadsheet_values),
        FunctionTool(func=create_spreadsheet_tab)
    ]
)

adk_app = App(name="secure_sandbox_app", root_agent=root_agent)
runner = Runner(app=adk_app, session_service=InMemorySessionService(), auto_create_session=True)

# ==========================================
# STREAMLIT UI SETUP
# ==========================================

st.sidebar.title("☕ Coffee Shop Monitor")
st.sidebar.write("Monitoring spreadsheet & agent status...")

st.title("Secure ADK Sandbox Assistant")

# Initialize chat history in session state
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "🔌 System: Connected. Agent is ready..."}
    ]

# Display prior messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Chat input box
if prompt := st.chat_input("Message Coffee Shop Monitor..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Agent is running tools and thinking..."):
            try:
                new_message = types.Content(parts=[types.Part(text=prompt)])
                
                # Synchronous event loop handler for runner
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                events = loop.run_until_complete(
                    asyncio.to_thread(
                        runner.run,
                        user_id="local_user",
                        session_id="local_session",
                        new_message=new_message
                    )
                )
                
                final_response = "".join(
                    part.text
                    for event in events
                    if event.content and event.content.parts
                    for part in event.content.parts
                    if part.text
                ) or "Agent completed execution updates without text output."
                
                st.markdown(final_response.strip())
                st.session_state.messages.append({"role": "assistant", "content": final_response.strip()})
            except Exception as e:
                err_msg = f"Agent loop failed: {str(e)}"
                st.error(err_msg)
                st.session_state.messages.append({"role": "assistant", "content": err_msg})
