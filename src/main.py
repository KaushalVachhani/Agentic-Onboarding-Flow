# Standard library imports
import os
import sys
import sqlite3
import datetime as dt
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from pprint import pprint
from typing import Any, Dict, List, Optional, TypedDict

# Third-party library imports
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import StateGraph, END
import streamlit as st

# Local application imports
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from config.config import asana_api_client
from src.apis.asana_apis import create_onboarding_tasks
from src.apis.gmail_apis import send_gmail, schedule_calendar_event


# ------------------------------------------------------------
# DB setup utilities
# ------------------------------------------------------------
DB_PATH = "data/employees.db"

@contextmanager
def db_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()

def bootstrap_dummy_db():
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("DROP TABLE IF EXISTS employees")
        cur.execute("""
        CREATE TABLE employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL,
            department TEXT NOT NULL,
            date_joined TEXT NOT NULL,         -- ISO date string YYYY-MM-DD
            location TEXT NOT NULL,
            level TEXT NOT NULL,               -- "junior" or "senior"
            manager_email TEXT
        )
        """)
        # seed a few rows if table is empty
        count = cur.execute("SELECT COUNT(1) FROM employees").fetchone()[0]
        if count == 0:
            today = dt.date.today()
            last_week = today - dt.timedelta(days=7)
            rows = [
                # New joiners
                ("Kaushal Vachhani", "kaushal.vachhani@example.com", "Data Engineer", "Data Platform", today.isoformat(), "Bengaluru", "junior", "lead.de@example.com"),
                ("Asha Patel", "asha.patel@example.com", "Data Engineer", "Data Platform", (today - dt.timedelta(days=100)).isoformat(), "Bengaluru", "junior", "lead.de@example.com"),
                # Senior mentors
                ("Neeraj Singh", "neeraj.singh@example.com", "Data Engineer", "Data Platform", (today - dt.timedelta(days=400)).isoformat(), "Bengaluru", "senior", "director.de@example.com"),
                ("Sneha Rao", "sneha.rao@example.com", "Data Engineer", "Data Platform", (today - dt.timedelta(days=900)).isoformat(), "Pune", "senior", "director.de@example.com"),
                # Other roles
                ("Karan Shah", "karan.shah@example.com", "Backend Engineer", "App Eng", last_week.isoformat(), "Bengaluru", "junior", "lead.be@example.com"),
            ]
            cur.executemany("""
                INSERT INTO employees (name, email, role, department, date_joined, location, level, manager_email)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            con.commit()

# ------------------------------------------------------------
# Query helpers
# ------------------------------------------------------------
def find_new_joiners_data_engineers(joined_since_days: int = 14) -> List[Dict[str, Any]]:
    cutoff = dt.date.today() - dt.timedelta(days=joined_since_days)
    with db_conn() as con:
        rows = con.execute("""
            SELECT * FROM employees
             WHERE role = 'Data Engineer'
               AND date(date_joined) >= date(?)
               AND level = 'junior'
        """, (cutoff.isoformat(),)).fetchall()
        return [dict(r) for r in rows]

def find_senior_mentor(preferred_location: str) -> Optional[Dict[str, Any]]:
    with db_conn() as con:
        row = con.execute("""
            SELECT * FROM employees
             WHERE role = 'Data Engineer'
               AND level = 'senior'
               AND location = ?
             ORDER BY date(date_joined) ASC
             LIMIT 1
        """, (preferred_location,)).fetchone()
        if row:
            return dict(row)
        # fallback to any senior if none in location
        row2 = con.execute("""
            SELECT * FROM employees
             WHERE role = 'Data Engineer'
               AND level = 'senior'
             ORDER BY date(date_joined) ASC
             LIMIT 1
        """).fetchone()
        return dict(row2) if row2 else None

# ------------------------------------------------------------
# LangChain LLM setup for email generation
# ------------------------------------------------------------
def get_llm():
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.4,
        convert_system_message_to_human=True
    )

EMAIL_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are HR Ops helping write friendly welcome emails."),
    ("human", """
        Write a warm, welcoming **HTML email** for a new {role} joining the {team} team at VachhaniAI Labs.

        Requirements:
        - Output **only raw HTML**, no triple backticks, no extra markdown, no "html" tag outside the HTML structure.
        - Use inline CSS for styling so it looks elegant across all email clients.
        - Incorporate subtle brand colors:
        - Primary accent: #FF3621
        - Secondary: #1B3139
        - Background: #F9F7F4
        - Use clean typography (e.g., sans-serif fonts) with proper spacing.
        - Include:
        - A friendly greeting with {name}
        - A short paragraph welcoming them to the team
        - A section about what their first week will look like
        - A note about onboarding tasks in Asana which they receive soon via email
        - Contact info for their manager: {manager_email}
        - Sign-off from HR
        - Make sure the HTML looks balanced, mobile-friendly, and visually appealing.
        - Avoid overly fancy graphics, keep it modern and readable.

        Personalization fields:
        - Name: {name}
        - Role: {role}
        - Team: {team}
        - Start Date: {start_date}
        - Manager Email: {manager_email}
        - Location: {location}

        Return **only the HTML body**, no code fences, no extra commentary.
""")
])

def generate_welcome_email_content(employee: Dict[str, Any]) -> str:
    llm = get_llm()
    chain = EMAIL_PROMPT | llm | StrOutputParser()
    body = chain.invoke({
        "name": employee["name"],
        "role": employee["role"],
        "team": employee["department"],
        "start_date": employee["date_joined"],
        "manager_email": employee.get("manager_email") or "hr@company.com",
        "location": employee["location"]
    })
    return body.strip()

# ------------------------------------------------------------
# Shared graph state per employee
# ------------------------------------------------------------
class EmpState(TypedDict, total=False):
    employee: Dict[str, Any]
    email_body: str
    email_result: Dict[str, Any]
    asana_task: Dict[str, Any]
    mentor: Dict[str, Any]
    calendar_event: Dict[str, Any]
    logs: List[str]

def append_log(state: EmpState, msg: str) -> EmpState:
    logs = state.get("logs", [])
    logs.append(f"{dt.datetime.now().isoformat(timespec='seconds')} | {msg}")
    state["logs"] = logs
    return state

# ------------------------------------------------------------
# Graph nodes
# ------------------------------------------------------------
def node_generate_email(state: EmpState) -> EmpState:
    emp = state["employee"]
    body = generate_welcome_email_content(emp)
    state["email_body"] = body
    return append_log(state, f"Generated email for {emp['email']}")

def node_send_email(state: EmpState) -> EmpState:
    emp = state["employee"]
    subject = f"Welcome to the team, {emp['name']}!"
    res = send_gmail(
        sender_email="me",
        recipient_email=emp["email"],
        subject=subject,
        body=state["email_body"]
    )
    state["email_result"] = res
    return append_log(state, f"Sent welcome email to {emp['email']}")

def node_asana_task(state: EmpState) -> EmpState:
    emp = state["employee"]
    workspace_gid = os.environ.get("ASANA_WORKSPACE_GID", "workspace_dummy")
    project_gid = os.environ.get("ASANA_PROJECT_GID", "project_dummy")
    task_name = f"Onboarding for {emp['name']} - Data Engineer"
    task = create_onboarding_tasks(
        api_client=asana_api_client,
        workspace_gid=workspace_gid,
        project_gid=project_gid,
        new_member_email=emp["email"],
        task_name=task_name
    )
    state["asana_task"] = task
    return append_log(state, f"Created Asana task for {emp['email']}")

def node_find_mentor(state: EmpState) -> EmpState:
    emp = state["employee"]
    mentor = find_senior_mentor(preferred_location=emp["location"])
    if not mentor:
        raise RuntimeError(f"No senior mentor available for {emp['name']}")
    state["mentor"] = mentor
    return append_log(state, f"Selected mentor {mentor['name']} for {emp['email']}")

def node_schedule_intro_call(state: EmpState) -> EmpState:
    emp = state["employee"]
    mentor = state["mentor"]
    # Next week, same weekday at 10:00 to 11:00 IST
    today = dt.date.today()
    next_week = today + dt.timedelta(days=7)
    start_dt = dt.datetime.combine(next_week, dt.time(10, 0))
    end_dt = dt.datetime.combine(next_week, dt.time(11, 0))

    summary = f"Intro chat: {emp['name']} x {mentor['name']} (Data Platform)"
    location = "Google Meet"
    description = (
        f"Welcome {emp['name']}.\n"
        f"Mentor: {mentor['name']} ({mentor['email']}).\n"
        f"Agenda: Meet the team, tooling overview, first week goals.\n"
        f"Manager: {emp.get('manager_email') or 'N/A'}.\n"
    )

    event = schedule_calendar_event(
        summary=summary,
        location=location,
        description=description,
        start_time_str=start_dt.isoformat(),
        end_time_str=end_dt.isoformat(),
        attendees_emails=[emp["email"], mentor["email"]],
        timezone="Asia/Kolkata",
        reminders=[{"method": "popup", "minutes": 30}],
        conference_request_id=f"mentor-{emp['id']}-{next_week.isoformat()}"
    )
    state["calendar_event"] = event
    return append_log(state, f"Scheduled mentor call for {emp['email']}")

# ------------------------------------------------------------
# Build per-employee subgraph
# ------------------------------------------------------------
def build_employee_graph():
    g = StateGraph(EmpState)

    g.add_node("generate_email", RunnableLambda(node_generate_email))
    g.add_node("send_email", RunnableLambda(node_send_email))
    g.add_node("asana_task", RunnableLambda(node_asana_task))
    g.add_node("find_mentor", RunnableLambda(node_find_mentor))
    g.add_node("schedule_intro_call", RunnableLambda(node_schedule_intro_call))

    g.set_entry_point("generate_email")
    g.add_edge("generate_email", "send_email")
    g.add_edge("send_email", "asana_task")
    g.add_edge("asana_task", "find_mentor")
    g.add_edge("find_mentor", "schedule_intro_call")
    g.add_edge("schedule_intro_call", END)

    return g.compile()

# ------------------------------------------------------------
# Orchestration: load new joiners and run the subgraph for each
# ------------------------------------------------------------
@dataclass
class RunSummary:
    processed: int
    successes: int
    failures: List[str]

def run_onboarding_for_new_joiners(st,joined_since_days: int = 14) -> Dict[str, Any]:
    new_joiners = find_new_joiners_data_engineers(joined_since_days=joined_since_days)
    if not new_joiners:
        return {"processed": 0, "successes": 0, "failures": [], "message": "No new Data Engineers found"}

    st.write(f"Found {len(new_joiners)} new Data Engineers who joined in the last {joined_since_days} days.")
    st.write("Details of new joiners:")
    for emp in new_joiners:
        st.write(f"- {emp['name']} - {emp['email']}")
    st.write("They will be onboarded with the following steps:")
    st.markdown("""
    - Generate welcome email  
    - Send welcome email  
    - Create Asana task  
    - Find mentor  
    - Schedule intro call with mentor
    """)
    st.write("Starting onboarding workflow...")
    compiled = build_employee_graph()
    successes, failures = 0, []

    for emp in new_joiners:
        state: EmpState = {"employee": emp, "logs": []}
        try:
            _ = compiled.invoke(state)
            successes += 1
        except Exception as e:
            failures.append(f"{emp['email']}: {e}")

    return {
        "processed": len(new_joiners),
        "successes": successes,
        "failures": failures
    } 

# # ------------------------
# # Chat Interface
# # ------------------------

STOP_COMMANDS = {"stop", "exit", "quit", "silence"}

CHAT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You are an HR Tech assistant. Keep responses friendly and chill."),
    ("human", "{input}")
])

# ------------------------
# Functions
# ------------------------
def chat_mode(user_text: str) -> str:
    if user_text.strip().lower() in STOP_COMMANDS:
        return "Okay, stopping the onboarding flow. Bye!"
    llm = get_llm()
    chain = CHAT_PROMPT | llm | StrOutputParser()
    return chain.invoke({"input": user_text})

def onboarding_mode():
    st.write("Starting onboarding workflow...")
    st.write("Fetching new joiners...")
    summary = run_onboarding_for_new_joiners(st, joined_since_days=14)
    
    st.write("✅ Workflow completed.")

# ------------------------
# Streamlit App
# ------------------------
def main():
    st.set_page_config(
        page_title="Onboardia",
        page_icon="🤖",
        layout="centered",
        initial_sidebar_state="expanded"
    )

    # Custom elegant theme
    st.markdown("""
        <style>
            /* Background and text colors */
            body {
                background-color: #0A1F33; /* Deep navy */
                color: #F5F2E7; /* Soft ivory */
                font-family: 'Segoe UI', sans-serif;
            }
            h1 {
                color: #D4AF37 !important; /* Royal gold */
                font-size: 2.5rem;
                text-align: center;
                font-weight: bold;
            }
            h4, h2, h3 {
                color: #F5F2E7 !important;
            }
            .stSelectbox label {
                font-size: 1.2rem;
                color: #D4AF37 !important;
                font-weight: 600;
            }
            .stButton button {
                background-color: #D4AF37;
                color: #0A1F33;
                border-radius: 8px;
                font-weight: bold;
                border: none;
                padding: 0.6rem 1.2rem;
                font-size: 1.1rem;
            }
            .stButton button:hover {
                background-color: #B89227;
                color: white;
            }
            .stMarkdown p {
                font-size: 1.1rem;
            }
        </style>
    """, unsafe_allow_html=True)

    st.title("🤖 Onboardia - HR AI Assistant")
    st.markdown("Welcome to **Onboardia** – your premium AI-powered HR assistant for onboarding new Data Engineers at **VachhaniAI Labs**.")

    # Bootstrap dummy DB once
    if "db_bootstrapped" not in st.session_state:
        bootstrap_dummy_db()
        st.session_state.db_bootstrapped = True

    # Mode selection
    st.markdown("#### What would you like to do?")
    mode = st.selectbox(
        "Select Mode",
        ["Run Onboarding Workflow", "Chat with Onboardia"]
    )

    if mode == "Chat with Onboardia":
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []

        # Show existing chat history
        for sender, msg in st.session_state.chat_history:
            if sender == "You":
                st.chat_message("user").write(msg)
            else:
                st.chat_message("assistant").write(msg)

        # Chat input at the bottom
        if prompt := st.chat_input("Type your message..."):
            st.session_state.chat_history.append(("You", prompt))
            st.chat_message("user").write(prompt)

            with st.spinner("Thinking..."):
                response = chat_mode(prompt)

            st.session_state.chat_history.append(("Onboardia", response))
            st.chat_message("assistant").write(response)

    else:
        if st.button("Run Workflow"):
            onboarding_mode()

if __name__ == "__main__":
    main()
