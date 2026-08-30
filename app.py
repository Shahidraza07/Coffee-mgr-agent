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

# --- CSS: GitHub/Edit hide + Pulsing Status + Animated Coffee Cup in Sidebar ---
custom_styles = """
    <style>
    /* Toolbar se GitHub aur Edit icons ko hide karna */
    [data-testid="stToolbar"] a[href*="github.com"],
    [data-testid="stToolbar"] button[kind="header"] {
        display: none !important;
    }

    /* Sidebar Top Card Design */
    .sidebar-card {
        background: linear-gradient(135deg, #3E2723 0%, #4E342E 100%);
        padding: 22px;
        border-radius: 14px;
        color: #EFEBE9;
        box-shadow: 0 6px 20px rgba(0,0,0,0.3);
        border: 1px solid #6D4C41;
        margin-bottom: 20px;
    }

    .sidebar-title {
        font-size: 1.25rem;
        font-weight: bold;
        color: #FFF3E0;
        margin-bottom: 6px;
    }

    /* Pulsing Animation for Green Live Dot */
    @keyframes livePulse {
        0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(76, 175, 80, 0.9); }
        70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(76, 175, 80, 0); }
        100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(76, 175, 80, 0); }
    }

    .status-container {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 14px;
        background: rgba(0, 0, 0, 0.3);
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }

    .live-dot {
        width: 12px;
        height: 12px;
        background-color: #4CAF50;
        border-radius: 50%;
        display: inline-block;
        animation: livePulse 1.5s infinite ease-in-out;
    }
    
    .status-text {
        font-size: 0.95rem;
        font-weight: 500;
        color: #C8E6C9;
    }

    /* Animated Steaming Coffee Cup Card */
    .coffee-animation-card {
        background: linear-gradient(135deg, #4E342E 0%, #3E2723 100%);
        padding: 25px 15px;
        border-radius: 14px;
        text-align: center;
        margin-top: 15px;
        box-shadow: 0 6px 20px rgba(0,0,0,0.3);
        border: 1px solid #6D4C41;
    }

    .cup-body {
        width: 55px;
        height: 40px;
        background: #FFF3E0;
        border-radius: 0 0 28px 28px;
        position: relative;
        margin: 25px auto 10px auto;
        box-shadow: inset 0 -5px 0 #D7CCC8;
    }

    .cup-handle {
        position: absolute;
        right: -16px;
        top: 7px;
        width: 16px;
        height: 22px;
        border: 4px solid #FFF3E0;
        border-left: none;
        border-radius: 0 12px 12px 0;
    }

    .steam-container {
        position: relative;
        height: 35px;
        width: 55px;
        margin: 0 auto;
    }

    @keyframes riseSteam {
        0% { transform: translateY(0) scaleX(1); opacity: 0; }
        50% { opacity: 0.7; }
        100% { transform: translateY(-28px) scaleX(1.4); opacity: 0; }
    }

    .steam {
        position: absolute;
        background: rgba(255, 243, 224, 0.65);
        border-radius: 50%;
        animation: riseSteam 2s infinite ease-out;
    }

    .steam-1 { width: 5px; height: 16px; left: 16px; animation-delay: 0s; }
    .steam-2 { width: 7px; height: 20px; left: 24px; animation-delay: 0.6s; }
    .steam-3 { width: 5px; height: 15px; left: 32px; animation-delay: 1.2s; }

    .sidebar-caption {
        color: #FFECB3;
        font-size: 0.9rem;
        margin-top: 15px;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    </style>
"""
st.markdown(custom_styles, unsafe_allow_html=True)

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
# STREAMLIT UI SETUP (Animated Sidebar & Coffee Graphic)
# ==========================================

with st.sidebar:
    st.markdown("""
        <div class="sidebar-card">
            <div class="sidebar-title">☕ Coffee Shop Monitor</div>
            <div style="font-size: 0.85em; color: #D7CCC8; margin-bottom: 8px; line-height: 1.4;">
                Advanced Business Analytics & Inventory Control System
            </div>
            <div class="status-container">
                <span class="live-dot"></span>
                <span class="status-text">Live Agent Connected</span>
            </div>
        </div>

        <div class="coffee-animation-card">
            <div class="steam-container">
                <div class="steam steam-1"></div>
                <div class="steam steam-2"></div>
                <div class="steam steam-3"></div>
            </div>
            <div class="cup-body">
                <div class="cup-handle"></div>
            </div>
            <div class="sidebar-caption">⚡ Fresh Brew & Analytics</div>
        </div>
    """, unsafe_allow_html=True)

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
