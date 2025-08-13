import os.path
import base64
from email.mime.text import MIMEText
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# The scope for sending emails. If you change this, delete token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/calendar"]

def get_gmail_service():
    """
    Authenticates with the Gmail API and returns a service object.
    This function handles the OAuth 2.0 flow, including storing and
    refreshing credentials.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens.
    # It's created automatically when the authorization flow completes for the first time.
    if os.path.exists("data/token.json"):
        creds = Credentials.from_authorized_user_file("data/token.json", SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # This will trigger the one-time browser-based authentication flow.
            flow = InstalledAppFlow.from_client_secrets_file("data/credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open("data/token.json", "w") as token:
            token.write(creds.to_json())
            
    return build("gmail", "v1", credentials=creds)

def create_message(sender, to, subject, message_text):
    """
    Creates a MIME message object for an email.
    """
    message = MIMEText(message_text, "html")
    message["to"] = to
    message["from"] = sender
    message["subject"] = subject
    # The Gmail API requires the message to be base64url encoded.
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return {"raw": raw_message}

def send_message(service, user_id, message):
    """
    Sends an email message using the Gmail API.
    
    Args:
        service: Authorized Gmail API service instance.
        user_id: User's email address. The special value "me" can be used to indicate the authenticated user.
        message: Message to be sent.
        
    Returns:
        Sent message metadata or None if an error occurred.
    """
    try:
        sent_message = service.users().messages().send(userId=user_id, body=message).execute()
        print(f'Message Id: {sent_message["id"]}')
        return sent_message
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None

def send_gmail(sender_email, recipient_email, subject, body):
    """
    Authenticates, creates, and sends an email using the Gmail API.
    Args:
        sender_email (str): The sender's email address ("me" for authenticated user).
        recipient_email (str): The recipient's email address.
        subject (str): Subject of the email.
        body (str): Body text of the email.
    Returns:
        dict: Sent message metadata or None if an error occurred.
    """
    service = get_gmail_service()
    email_message = create_message(sender_email, recipient_email, subject, body)
    print("Sending the email...")
    result = send_message(service, sender_email, email_message)
    if result:
        print("Email sent successfully!")
    return result

def get_calendar_service():
    """Authenticates with the Google Calendar API and returns a service object."""
    creds = None
    # The file token.json stores the user's access and refresh tokens.
    if os.path.exists("data/token.json"):
        creds = Credentials.from_authorized_user_file("data/token.json", SCOPES)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Before refreshing, ensure the scopes match. If not, re-authenticate.
            if set(creds.scopes) != set(SCOPES):
                 flow = InstalledAppFlow.from_client_secrets_file("data/credentials.json", SCOPES)
                 creds = flow.run_local_server(port=0)
            else:
                 creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("data/credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Save the credentials for the next run
        with open("data/token.json", "w") as token:
            token.write(creds.to_json())
            
    return build("calendar", "v3", credentials=creds)

def schedule_calendar_event(
    summary,
    location,
    description,
    start_time_str,
    end_time_str,
    attendees_emails,
    timezone='Asia/Kolkata',
    reminders=None,
    conference_request_id='sample-request-123'
):
    """
    Schedules a Google Calendar event with Google Meet link.

    Args:
        summary (str): Event summary/title.
        location (str): Event location.
        description (str): Event description.
        start_time_str (str): Event start time in RFC3339 format (e.g., '2025-08-14T10:00:00').
        end_time_str (str): Event end time in RFC3339 format (e.g., '2025-08-14T11:00:00').
        attendees_emails (list): List of attendee email addresses.
        timezone (str): Timezone for the event.
        reminders (list): List of reminder dicts (method and minutes).
        conference_request_id (str): Unique ID for conference request.

    Returns:
        dict: Event details if created successfully, None otherwise.
    """
    service = get_calendar_service()

    event_details = {
        'summary': summary,
        'location': location,
        'description': description,
        'start': {
            'dateTime': start_time_str,
            'timeZone': timezone,
        },
        'end': {
            'dateTime': end_time_str,
            'timeZone': timezone,
        },
        'attendees': [{'email': email} for email in attendees_emails],
        'reminders': {
            'useDefault': False,
            'overrides': reminders if reminders else [
                {'method': 'email', 'minutes': 24 * 60},
                {'method': 'popup', 'minutes': 30},
            ],
        },
        'conferenceData': {
            'createRequest': {
                'requestId': conference_request_id,
                'conferenceSolutionKey': {
                    'type': 'hangoutsMeet'
                }
            }
        }
    }

    print(f"Scheduling event: '{summary}' for {start_time_str}")
    try:
        event = service.events().insert(
            calendarId='primary',
            body=event_details,
            conferenceDataVersion=1,
            sendNotifications=True
        ).execute()
        print("Event created successfully!")
        print(f"Event ID: {event.get('id')}")
        print(f"Google Meet Link: {event.get('hangoutLink')}")
        return event
    except HttpError as error:
        print(f"An error occurred: {error}")
        return None



