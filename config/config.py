import os
from dotenv import load_dotenv
import asana

# Load .env file into environment
load_dotenv()

# Read required environment variables
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ASANA_WORKSPACE_GID = os.getenv("ASANA_WORKSPACE_GID")
ASANA_PROJECT_GID = os.getenv("ASANA_PROJECT_GID")
ASANA_PAT = os.getenv("ASANA_PAT")

# Validate environment variables
missing = []
if not GOOGLE_API_KEY:
    missing.append("GOOGLE_API_KEY")
if not ASANA_WORKSPACE_GID:
    missing.append("ASANA_WORKSPACE_GID")
if not ASANA_PROJECT_GID:
    missing.append("ASANA_PROJECT_GID")
if not ASANA_PAT:
    missing.append("ASANA_PAT")

if missing:
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

# Initialize Asana API client globally
configuration = asana.Configuration()
configuration.access_token = ASANA_PAT
asana_api_client = asana.ApiClient(configuration)

# Optional: export all in a dict if you like
SETTINGS = {
    "google_api_key": GOOGLE_API_KEY,
    "asana_workspace_gid": ASANA_WORKSPACE_GID,
    "asana_project_gid": ASANA_PROJECT_GID,
    "asana_pat": ASANA_PAT,
}
