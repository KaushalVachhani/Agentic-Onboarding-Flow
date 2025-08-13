from dotenv import load_dotenv
import asana
from asana.rest import ApiException
from pprint import pprint

def invite_user_to_workspace(api_client, workspace_gid, new_member_email):
    """
    Invites (or adds) a user to an Asana workspace by email.
    """
    # create an instance of the API class
    workspaces_api_instance = asana.WorkspacesApi(api_client)
    body = {"data": {"user": new_member_email}} # dict | The user to add to the workspace.
    opts = {
        'opt_fields': "email,name,photo,photo.image_1024x1024,photo.image_128x128,photo.image_21x21,photo.image_27x27,photo.image_36x36,photo.image_60x60", # list[str] | This endpoint returns a resource which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
    }

    try:
        # Add a user to a workspace or organization
        api_response = workspaces_api_instance.add_user_for_workspace(body, workspace_gid, opts)
        pprint(api_response)
    except ApiException as e:
        print("Exception when calling WorkspacesApi->add_user_for_workspace: %s\n" % e)
    
    
def create_task(api_client, workspace_gid, project_gid, assignee_email, task_name):
    """
    Creates a new task in an Asana project.
    """
    tasks_api = asana.TasksApi(api_client)
    
    body = {
        "data": {
            "name": task_name,
            "assignee": assignee_email,
            "workspace": workspace_gid,
            "projects": [project_gid]
        }
    }
    opts = {
        'opt_fields': "name,assignee,assignee.name,projects,projects.name,workspace,workspace.name,created_at,permalink_url"
    }
    
    try:
        response = tasks_api.create_task(body, opts)
        pprint(response)
        return response
    except ApiException as e:
        print(f"Exception when calling TasksApi->create_task: {e}")
        return None

def create_onboarding_tasks(
    api_client,
    workspace_gid,
    project_gid,
    new_member_email,
    task_name
):
    """
    Invites a new member to an Asana workspace and creates an onboarding task for them.

    Args:
        api_client (asana.ApiClient): Authenticated Asana API client.
        workspace_gid (str): Workspace GID (from environment).
        project_gid (str): Project GID (from environment).
        new_member_email (str): Email of the new member to invite.
        task_name (str): Name of the onboarding task to create.

    Returns:
        dict or None: The created task response, or None if an error occurred.
    """
    # Step 1: Invite the new member
    invite_user_to_workspace(api_client, workspace_gid, new_member_email)

    # Step 2: Create a task for them
    return create_task(api_client, workspace_gid, project_gid, new_member_email, task_name)



