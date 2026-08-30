import os
import asyncio
import subprocess
from pathlib import Path
from typing import List
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from google.adk.agents import LlmAgent as Agent
from google.adk.tools import FunctionTool
from google.adk.apps import App
from google.adk import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

SANDBOX_CLI = '/usr/local/gcp/bin/sandbox'
IS_LOCAL_MODE = not Path(SANDBOX_CLI).exists()

active_connections: list[WebSocket] = []

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

def run_sandbox_process(args: list[str]):
    cmd = args[2:] if IS_LOCAL_MODE and args[:2] == ['do', '--'] else ([SANDBOX_CLI] + args if not IS_LOCAL_MODE else args)
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
        # Check if tab exists
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in spreadsheet.get('sheets', []):
            if sheet.get('properties', {}).get('title') == tab_name:
                return f"Sheet tab '{tab_name}' already exists."
        # Create tab
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
        '   - Receive the current graduation schedule directly from the manager\'s prompt (the manager will paste it and indicate it is the same schedule sequence as last year).\n'
        '   - Write a python3 script via the sandbox tool to:\n'
        '     a. Correlate the 2025 product spikes (Cold Brew, Alt Milk, Extra Espresso) with the specific ceremonies ending at those times.\n'
        '     b. Map those beverage profiles to the pasted schedule (which is the same sequence) to predict exactly when and where the 2026 spikes will occur.\n'
        '     c. Identify expected wait-time bottlenecks in 2026 based on the 2025 wait times for those same profiles.\n'
        '2. Bottleneck Diagnostics (Playbook):\n'
        '   - If a predicted high-volume slot in 2026 is expected to have Wait_Time_Minutes > 10:\n'
        '     - If Cashiers_Working < 2: Recommend scheduling another cashier.\n'
        '     - If Cashiers_Working == 2 and complex items (Cold Brew, Extra Espresso, Alt Milk) spike: Deduce that the bottleneck is barista output, not cashiers. Recommend adding a "Support Barista" role to handle fulfillment.\n'
        '3. Human-in-the-Loop Policy:\n'
        '   - Present your detailed data discoveries, wait-time bottlenecks, and actionable recommendations (stocking and staffing changes) to the manager.\n'
        '   - Highlight only two or three findings for specific ceremonies.\n'
        '   - Frame your recommendations as a clean list of suggested tasks for the manager\'s TODO list.\n'
        '   - Explicitly ask: "Would you like me to add these tasks to your \'TODO-2026\' TODO list?"\n'
        '   - Do NOT modify any spreadsheet data until explicit approval is given.\n'
        '4. Post-Approval Policy:\n'
        f'   - Upon receiving explicit user approval, first verify if the "TODO-2026" sheet tab exists in spreadsheet "{SPREADSHEET_ID}".\n'
        f'   - If the "TODO-2026" sheet tab does not exist, use the tool create_spreadsheet_tab to create it in spreadsheet "{SPREADSHEET_ID}".\n'
        f'   - Once the tab exists, use update_spreadsheet_values to append the approved adjustments as tasks to the "TODO-2026" sheet tab.\n'
        '   - Write the rows under the headers: Task (the actionable job, e.g., "Schedule a Support Barista role for Saturday morning"), Category ("Staffing" or "Inventory"), Ceremony, and Date_Added (today\'s date).\n'
        '   - Always confirm to the user exactly what tasks you have written to their "TODO-2026" TODO list.'
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

app = FastAPI(title="Secure ADK Sandbox Assistant")

# ==========================================
# ENDPOINTS & WEBSOCKET ROUTING
# ==========================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    await websocket.send_text("🔌 System: Connected. Agent is ready...")

    try:
        while True:
            owner_reply = await websocket.receive_text()
            print(f"Owner replied via WS: {owner_reply}")
            await websocket.send_text("_Agent is running tools and thinking..._")
            
            new_message = types.Content(parts=[types.Part(text=owner_reply)])
            events = await asyncio.to_thread(
                runner.run,
                user_id="local_user",
                session_id="local_session",
                new_message=new_message
            )
            
            final_response = "".join(
                part.text
                for event in events
                if event.content and event.content.parts
                for part in event.content.parts
                if part.text
            ) or "Agent completed execution updates without text output."
            
            await websocket.send_text(final_response.strip())
            
    except WebSocketDisconnect:
        active_connections.remove(websocket)

class UserPrompt(BaseModel):
    prompt: str

@app.post("/chat")
def chat_with_agent(payload: UserPrompt):
    """Fallback HTTP POST endpoint if UI is not used."""
    try:
        events = runner.run(
            user_id="local_user",
            session_id="local_session",
            new_message=types.Content(parts=[types.Part(text=payload.prompt)])
        )
        final_response = "".join(
            part.text
            for event in events
            if event.content and event.content.parts
            for part in event.content.parts
            if part.text
        )
        return {"status": "success", "response": final_response.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent loop failed: {str(e)}")

@app.get("/", response_class=HTMLResponse)
async def get_chat_ui():
    """Serves the warm coffee-themed chat interface."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Coffee Shop Agent</title>
        <!-- Load marked.js for client-side markdown rendering -->
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <style>
            body { display: flex; height: 100vh; margin: 0; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }
            #sidebar { width: 250px; background: #3E2723; color: #EFEBE9; padding: 20px; }
            #sidebar h2 { font-size: 1.3em; margin-top: 10px; color: #FFF; border-bottom: 1px solid #5D4037; padding-bottom: 10px; }
            #main { flex-grow: 1; display: flex; flex-direction: column; background: #FAF8F6; }
            #chat-history { flex-grow: 1; padding: 20px; overflow-y: auto; background: #F5EFEB; }
            #input-area { padding: 20px; border-top: 1px solid #D7CCC8; background: #FAF8F6; display: flex;}
            input { 
                flex-grow: 1; 
                padding: 12px; 
                border-radius: 6px; 
                border: 1px solid #D7CCC8; 
                margin-right: 10px;
                font-size: 1em;
                background: #FFF;
                transition: border-color 0.2s, box-shadow 0.2s;
            }
            input:focus {
                outline: none;
                border-color: #8D6E63;
                box-shadow: 0 0 0 2px rgba(141, 110, 99, 0.25);
            }
            button { 
                padding: 10px 24px; 
                background: #6D4C41; 
                color: white; 
                border: none; 
                border-radius: 6px; 
                cursor: pointer; 
                font-weight: bold;
                font-size: 1em;
                transition: background-color 0.2s, transform 0.1s;
            }
            button:hover {
                background: #5D4037;
            }
            button:active {
                background: #4E342E;
                transform: scale(0.98);
            }
            .message { margin-bottom: 15px; padding: 12px 16px; border-radius: 8px; max-width: 85%; line-height: 1.5; }
            .user-msg { background: #EFEBE9; color: #3E2723; align-self: flex-end; margin-left: auto; border: 1px solid #D7CCC8;}
            .agent-msg { background: #fff; color: #3E2723; border: 1px solid #E0DCD8; box-shadow: 0 1px 3px rgba(62,39,35,0.06); }
            
            .msg-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
            .agent-name { font-weight: bold; color: #5D4037; margin: 0; font-size: 0.95em;}
            .user-name { font-weight: bold; color: #8D6E63; margin: 0; font-size: 0.95em;}
            .msg-timestamp { font-size: 0.8em; color: #8D6E63; font-weight: normal; }
            
            .day-divider {
                display: flex;
                align-items: center;
                text-align: center;
                color: #8D6E63;
                margin: 20px 0;
                font-size: 0.85em;
                font-weight: bold;
            }
            .day-divider::before, .day-divider::after {
                content: '';
                flex: 1;
                border-bottom: 1px solid #D7CCC8;
            }
            .day-divider:not(:empty)::before {
                margin-right: .5em;
            }
            .day-divider:not(:empty)::after {
                margin-left: .5em;
            }

            /* Markdown Styling inside Messages */
            .message p { margin: 4px 0 8px 0; }
            .message p:last-child { margin-bottom: 0; }
            .message ul, .message ol { margin: 4px 0 8px 0; padding-left: 20px; }
            .message li { margin-bottom: 3px; }
            .message h1, .message h2, .message h3, .message h4 { margin: 12px 0 6px 0; font-size: 1.15em; color: #3E2723; font-weight: bold; }
            .message h1:first-child, .message h2:first-child, .message h3:first-child { margin-top: 0; }
            
            /* Table Styling */
            .message table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 0.95em; }
            .message th, .message td { border: 1px solid #D7CCC8; padding: 8px 10px; text-align: left; }
            .message th { background-color: #F5EFEB; font-weight: bold; color: #3E2723; }
            .message tr:nth-child(even) { background-color: #FAF8F6; }
            
            /* Code / Blockquote styling */
            .message code { background: #EFEBE9; padding: 2px 4px; border-radius: 3px; font-family: monospace; font-size: 0.9em; color: #5D4037; }
            .message pre { background: #F5EFEB; padding: 10px; border-radius: 5px; overflow-x: auto; margin: 8px 0; border: 1px solid #D7CCC8; }
            .message pre code { background: none; padding: 0; }
            .message blockquote { margin: 8px 0; padding-left: 12px; border-left: 4px solid #6D4C41; color: #5D4037; }
        </style>
    </head>
    <body>
        <div id="sidebar">
            <h2>☕ Coffee Shop Monitor</h2>
            <p>Monitoring sheet...</p>
        </div>
        <div id="main">
            <div id="chat-history"></div>
            <div id="input-area">
                <input type="text" id="msg" placeholder="Message Coffee Shop Monitor..." onkeypress="if(event.key === 'Enter') sendMessage()">
                <button onclick="sendMessage()">Send</button>
            </div>
        </div>

        <script>
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
            const history = document.getElementById('chat-history');

            // Scroll to the bottom on load to show latest messages
            history.scrollTop = history.scrollHeight;

            ws.onmessage = function(event) {
                // Parse markdown content from the agent
                const parsedHtml = marked.parse(event.data);
                const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                history.innerHTML += `
                    <div class="message agent-msg">
                        <div class="msg-header">
                            <span class="agent-name">Inventory Agent APP</span>
                            <span class="msg-timestamp">${timeStr}</span>
                        </div>
                        <div>${parsedHtml}</div>
                    </div>`;
                history.scrollTop = history.scrollHeight;
            };

            function sendMessage() {
                const input = document.getElementById('msg');
                const text = input.value;
                if (!text) return;
                
                // Parse user markdown if any (optional, but keeps styling consistent)
                const parsedHtml = marked.parse(text);
                const timeStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                history.innerHTML += `
                    <div class="message user-msg">
                        <div class="msg-header">
                            <span class="user-name">You</span>
                            <span class="msg-timestamp">${timeStr}</span>
                        </div>
                        <div>${parsedHtml}</div>
                    </div>`;
                
                ws.send(text);
                input.value = '';
                history.scrollTop = history.scrollHeight;
            }
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    import uvicorn
    port_val = int(os.environ.get('PORT', 8080))
    uvicorn.run(app, host='0.0.0.0', port=port_val)
