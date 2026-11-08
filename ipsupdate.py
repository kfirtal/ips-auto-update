"""
IPS Auto Update for Air-Gapped Environments

I built this project to replace repetitive manual IPS updates across
multiple Check Point management servers in an air-gapped environment.

The script uploads a locally available UPF package to each server,
runs the IPS update, publishes the changes, and reports the results.

It is intended to run automatically once a week through Windows Task
Scheduler. An approved update package must be placed in the configured
local folder before the scheduled run.

SSH key-based authentication must be configured between the Windows
account running the scheduled task and the SSH account on each
management server. The script also requires a management API key
for each server.

The script does not download update packages or install gateway policy.
"""

import requests
from pathlib import Path
import paramiko
import time
from datetime import datetime

now = datetime.now()
print("\n" * 5)
print(80 * "=")
print(f"Run date and time: {now}")
print(80 * "=")
# Select the most recently modified UPF file from the local update folder.
# This folder can also be shared over SMB so authorized users can upload
# approved UPF packages without logging in to the server running this script.
# Finish copying each package before the scheduled run starts.
folder = Path(r"C:/Services/IPS_AutoUpdate/IPS_FILE")
upf_files = [f for f in folder.glob("*.upf") if f.is_file()]

if not upf_files:
    raise SystemExit("No UPF files found in the update folder.")

latest_file = max(upf_files, key=lambda f: f.stat().st_mtime)
print(f"Latest File is: {latest_file}")


LOCAL_FILE_PATH = latest_file
# Configure the remote upload path and management servers.
REMOTE_SERVER_PATH = "/var/log/sd_updates_new.upf"


API_KEY_1 = "<API-KEY>"
API_KEY_2 = "<API-KEY>"
API_KEY_3 = "<API-KEY>"
API_KEY_4 = "<API-KEY>"
API_KEY_5 = "<API-KEY>"
API_KEY_6 = "<API-KEY>"

SERVERS = [
    {"ip": "<ServerManagement1_IP>", "api_key": API_KEY_1},
    {"ip": "<ServerManagement2_IP>", "api_key": API_KEY_2},
    {"ip": "<ServerManagement3_IP>", "api_key": API_KEY_3},
    {"ip": "<ServerManagement4_IP>", "api_key": API_KEY_4},
    {"ip": "<ServerManagement5_IP>", "api_key": API_KEY_5},
    {"ip": "<ServerManagement6_IP>", "api_key": API_KEY_6}
]


def sftp_upload(ip, local_path, remote_path):
    """Upload the local update package to a management server using SFTP.

    Return True if the upload succeeds, otherwise False.
    Always attempt to close any SFTP and SSH connections that were opened."""
    ssh = None
    sftp = None
    try:
        print(f"Connecting to {ip} with SFTP...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username="backup",
                    key_filename='C:/Users/svc_automation/.ssh/id_ed25519')
        sftp = ssh.open_sftp()
        sftp.put(local_path, remote_path)
        print(f"The file successfully transfer to {remote_path}")
        return True
    except Exception as e:
        print(f"Error on transfer: {e}")
        return False

    finally:
        if sftp is not None:
            try:
                sftp.close()
            except Exception as e:
                print(f"SFTP close failed: {e}")
        if ssh is not None:
            try:
                ssh.close()
            except Exception as e:
                print(f"SSH close failed: {e}")


def sftp_remove(ip, remote_path):
    """Remove the uploaded update package from a management server.

    Return True if removal succeeds, otherwise False.
    Always attempt to close any SFTP and SSH connections that were opened."""
    ssh = None
    sftp = None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username="backup",
                    key_filename='C:/Users/svc_automation/.ssh/id_ed25519')
        sftp = ssh.open_sftp()
        print(f"Delete {remote_path} from server {ip}")
        sftp.remove(remote_path)
        print(f"The file successfully remove from {remote_path}")
        return True
    except Exception as e:
        print(f"Error on removing: {e}")
        return False

    finally:
        if sftp is not None:
            try:
                sftp.close()
            except Exception as e:
                print(f"SFTP close failed: {e}")
        if ssh is not None:
            try:
                ssh.close()
            except Exception as e:
                print(f"SSH close failed: {e}")


def wait_for_task(ip, sid, task_id, timeout=1800):
    """Poll a management API task every five seconds until it finishes or times out.

    Return True when the task succeeds.
    Return False when it fails, returns an empty task list, or exceeds the
    waiting period, which defaults to 30 minutes.

    A timeout does not cancel the remote task. API or response-processing
    errors may propagate to the calling function."""
    print(f"Wait for task {task_id}...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task_res = api_call(ip, "show-task", {"task-id": task_id}, sid)

        task_list = task_res.json()["tasks"]
        if not task_list:
            print("No data")
            return False
        task_info = task_list
        status = task_info[0]["status"]
        progress = task_info[0]["progress-percentage"]
        print(f"Upload Status: {status} ({progress}%)")
        if status == "succeeded":
            return True
        elif status == "failed":
            print(f"The task failed: {task_info[0]['comments']}")
            return False
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    print("Task wait timed out; remote task may still be running.")
    return False


def api_call(ip, command, payload, sid=None):
    """Send a POST request to a Check Point management API endpoint.

    Include the session ID when provided and return the HTTP response.
    Use a 10-second connection timeout and a 60-second read timeout.
    HTTPS certificate verification is disabled in this implementation."""
    url = f"https://{ip}/web_api/{command}"
    headers = {'Content-Type': 'application/json'}
    if sid:
        headers['X-chkp-sid'] = sid
    response = requests.post(url, json=payload, headers=headers, verify=False, timeout=(10, 60))
    return response


def process_server_update(server_info):
    """Run the upload, login, IPS update, publish, and cleanup workflow for one server.

    Wait for both the update and publish tasks before removing the package.
    Attempt API logout when leaving the update/publish section.

    Return a result dictionary containing the overall status, the last stage
    reached, and the package cleanup and logout results."""
    result = {'status': 'FAILED', 'stage': 'upload', 'cleanup': 'not attempted', 'logout': 'not attempted'}
    ip = server_info['ip']
    api_key = server_info['api_key']

    print(f"\n>>> Start process for server: {ip}")
    # Upload the update package before starting the API workflow.
    if not sftp_upload(ip, LOCAL_FILE_PATH, REMOTE_SERVER_PATH):
        return result


    # Authenticate and retrieve the API session ID.
    result['stage'] = 'login'
    try:
        login_res = api_call(ip, "login", {"api-key": api_key})
    except requests.exceptions.RequestException:
        print(f"Login connection failed for server: {ip}")
        return result
    if login_res.status_code != 200:
        print(f"Login failed to {ip}: {login_res.text}")
        return result
    sid = login_res.json().get("sid")

    if not isinstance(sid, str) or not sid.strip():
        print("Login failed: no valid session ID received.")
        return result

    print("Login successful.")
    try:


        # Start the offline IPS update and wait for completion.
        result['stage'] = 'IPS update'
        update_payload = {"package-path": REMOTE_SERVER_PATH}
        print(f"run update IPS Offline {ip}...")
        update_res = api_call(ip, "run-ips-update", update_payload, sid)
        if update_res.status_code == 200:
            task_id = update_res.json()["task-id"]
            print(f"The updated started.")
        else:
            print(f"Update command failed on {ip}: {update_res.text}")
            return result
        if not wait_for_task(ip, sid, task_id):
            return result

        print(f"Publish in process on : {ip}...")
        # Publish the changes and wait for completion.
        result['stage'] = 'publish'
        publish_res = api_call(ip, "publish", {}, sid)
        if publish_res.status_code != 200:
            print(f"Publish failed on : {ip}")
            return result
        publish_task_id = publish_res.json()["task-id"]
        if not wait_for_task(ip, sid, publish_task_id):
            return result
        result['status'] = 'SUCCESS'
        result['stage'] = 'completed'
        # Remove the uploaded package only after update and publish succeed.
        if sftp_remove(ip, REMOTE_SERVER_PATH):
            result['cleanup'] = 'success'
        else:
            result['cleanup'] = 'failed'
            result['status'] = 'WARNING'
            print(f"Update and publish succeeded, but package cleanup failed on {ip}")


    except Exception as e:
        print(f"occur Unexpected error {ip}: {e}")
    finally:

        try:
            # Attempt logout even if the update or publish operation fails.
            logout_res = api_call(ip, "logout", {}, sid)

            if logout_res.status_code == 200:
                result['logout'] = 'success'
                print(f"Logout complete {ip}.")
            else:
                result['logout'] = 'failed'
                print(f"Logout failed for server: {ip}")

        except requests.exceptions.RequestException:
            result['logout'] = 'failed'
            print(f"Logout connection failed for server: {ip}")

        if result['logout'] == 'failed' and result['status'] == 'SUCCESS':
            result['status'] = 'WARNING'
    return result


if __name__ == "__main__":

    requests.packages.urllib3.disable_warnings()

    # Process servers sequentially and collect their results.
    results = []
    for server in SERVERS:
        try:
            result = process_server_update(server)
        except Exception as e:
            print(f"Server processing failed: {e}")
            result = {'status': 'FAILED', 'stage': 'unexpected error',
                      'cleanup': 'unknown', 'logout': 'unknown'}
        results.append((server['ip'], result))
    # Print the final result for each management server.
    print("\n--- Run summary ---")
    for ip, result in results:
        print(f"{ip}: {result['status']} | Stage: {result['stage']} | "
              f"Cleanup: {result['cleanup']} | Logout: {result['logout']}")
    print("\n--- The script end ---")
