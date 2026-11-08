# IPS Auto Update for Air-Gapped Environments

I built this project to replace repetitive manual IPS updates across multiple Check Point management servers in an air-gapped environment.

The script distributes a locally available UPF package, runs the IPS update, publishes the changes, and prints a result for each server. It is intended to run once a week through Windows Task Scheduler.

## Workflow

1. Select the most recently modified `.upf` file in the configured local folder.
2. Process management servers sequentially.
3. Upload the package over SFTP and authenticate to the management API.
4. Start the IPS update and wait for its task to finish.
5. Publish the changes and wait for the publish task to finish.
6. Remove the uploaded package after both tasks succeed, attempt logout, and print a summary.

The script does not download packages or install policy on gateways. Package approval and transfer into the air-gapped environment happen separately.

## Prerequisites

- Windows automation host with Python, Requests, Paramiko, and the OpenSSH client tools used below.
- An approved UPF package available before the weekly run.
- Network access from the automation host to the management servers over SSH/SFTP and HTTPS.
- A Windows account for the scheduled task, a permitted SSH account on each management server, and a management API key for each server.
- SSH permissions to upload and remove the configured remote package, plus API permissions for the update and publish operations.

For an air-gapped host, obtain Python dependencies through your approved internal package source or transfer reviewed wheels matching your Python version and platform. Example installation from an approved local wheelhouse:

```powershell
python -m venv C:\Services\IPS_AutoUpdate\.venv
& C:\Services\IPS_AutoUpdate\.venv\Scripts\python.exe -m pip install --no-index --find-links C:\Approved\wheelhouse requests paramiko
```

These paths are examples. This project does not currently provide a dependency lock or wheelhouse.

## Configure the script

Keep the public copy populated with placeholders. Configure a separate private deployment copy:

| Setting | What to configure |
| --- | --- |
| `folder` | Local directory containing approved UPF packages |
| `REMOTE_SERVER_PATH` | Remote upload path accessible to the SSH account and update process |
| `API_KEY_1` through `API_KEY_6` | API keys for the corresponding management servers |
| `SERVERS` | Management addresses and corresponding keys; adjust the list as needed |
| `username` in both SFTP functions | SSH account on the management servers; currently `backup` |
| `key_filename` in both SFTP functions | Absolute path to the Windows task account's private SSH key |
| `wait_for_task(..., timeout=1800)` | Waiting period for each update or publish task; default 30 minutes |

## Configure SSH keys

The Windows task account holds the **private key**. The matching **public key** is authorized for the SSH account on each management server. These accounts can have different names. API keys are separate credentials used for HTTPS API authentication.

### 1. Generate a key on Windows

Run PowerShell as the account that will run the scheduled task. With Windows OpenSSH Client installed:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh" | Out-Null
ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\ips_update_ed25519" -C "IPS weekly update"
```

Do not overwrite an existing key. The resulting files are:

- `ips_update_ed25519`: private key; keep it on the automation host.
- `ips_update_ed25519.pub`: public key; authorize it on each server.

Check that your Gaia release and SSH policy support Ed25519. If they require RSA, generate a separate key with `ssh-keygen -t rsa -b 3072 -f "$env:USERPROFILE\.ssh\ips_update_rsa" -C "IPS weekly update"` and use its path instead. Do not weaken server algorithms to make a key work.

Microsoft documents key generation and protection in its [OpenSSH key-management guide](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement).

### 2. Plan unattended access

The script loads the file specified by `key_filename` and does not supply a key passphrase. A passphrase-protected key therefore needs a separately validated noninteractive loading arrangement or a code change before scheduling. An interactive terminal's agent setup must not be assumed to work in a scheduled task.

If your organization permits a dedicated automation key without a passphrase, select an empty passphrase when prompted and restrict the private key's Windows file permissions to the task account and authorized administrators. Never commit the private key or a populated deployment script.

### 3. Authorize the public key on every management server

The `backup` account used by this script must log in directly to `/bin/bash`, not Gaia Clish. Configure its default login shell on **each management server** before setting up unattended SFTP access. An authorized Gaia administrator can run these commands in **Clish** for the existing account:

```text
set user backup shell /bin/bash
save config
```

If the administrator is currently in Expert/Bash, enter `clish` first. These commands change the existing account's shell and persist the configuration; they do not create the account or grant file permissions. Keep an administrator session open, reconnect as `backup`, and confirm it opens Bash directly. In that new Bash session, `echo "$0"` should identify Bash. Then test SFTP as described below. Apply the change under your organization's access policy, since it grants direct shell access.

The default-shell setting and `save config` procedure follow the [official Gaia SSH key setup guide](https://sc1.checkpoint.com/documents/R82.10/WebAdminGuides/EN/CP_R82.10_Gaia_AdminGuide/Content/Topics-GAG/Configuring-SSH-Authentication-with-RSA-Key-Files.htm). If you use a different SSH username, substitute it for `backup` in both the commands and the script.

Display the public key on Windows:

```powershell
Get-Content "$env:USERPROFILE\.ssh\ips_update_ed25519.pub"
```

Have the Gaia administrator configure the target SSH account and authorize this public key for that account. The official [Gaia SSH key guide](https://sc1.checkpoint.com/documents/R82.10/WebAdminGuides/EN/CP_R82.10_Gaia_AdminGuide/Content/Topics-GAG/Configuring-SSH-Authentication-with-RSA-Key-Files.htm) describes account setup, shell requirements, key placement, and permissions. It targets R82.10 and RSA; consult the matching guide for your installed release.

For a configured account with an approved Bash/Expert shell on an ordinary management server, the key-file preparation commands, run **as the target SSH user**, are:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
vi ~/.ssh/authorized_keys
```

Append the public key as one complete line, preserving existing keys. Ensure the directory and file belong to that account. These commands only prepare the key file; they do not create the user or configure Gaia SSH/SFTP access. Server configuration changes and service restarts belong to the administrator's approved procedure.

### 4. Validate access under the task account

Replace the example host and user, and verify the host fingerprint through a trusted source before accepting it:

```powershell
sftp -o BatchMode=yes -i "$env:USERPROFILE\.ssh\ips_update_ed25519" backup@management.example.invalid
```

This checks unattended key authentication and SFTP access without starting an IPS update. Check that the chosen account can access the configured upload directory. Update `key_filename` in **both** SFTP functions to the actual absolute private-key path. Paramiko is a different client from Windows OpenSSH, so a successful SFTP command is a prerequisite check, not proof that the full Python workflow will succeed.

## Configure the weekly Windows task

The script contains no scheduler. Configure scheduling separately on the automation host; no task is created by this repository.

1. Open **Task Scheduler** and select **Create Task**.
2. On **General**, name it `IPS Auto Update - Weekly`, select the intended Windows account, and choose **Run whether user is logged on or not**. Provide credentials through the Windows dialog if requested. Use elevated privileges only if required by the deployment.
3. On **Triggers**, create a **Weekly** trigger recurring every **1 week**, on your approved maintenance day and time. Confirm the host's timezone. The approved package must arrive before this time.
4. On **Actions**, choose **Start a program** and use the fields below.
5. On **Settings**, set **If the task is already running: Do not start a new instance**. Decide whether missed runs should start later based on your maintenance-window policy. Avoid automatic retries until remote task state has been checked.
6. Ensure the host remains available during the window. Set conditions and any overall execution limit to accommodate all servers, both task waits per server, and file transfers. Terminating Python does not cancel remote API tasks.
7. Save the task. In an authorized maintenance window, run it manually through Task Scheduler and verify the actual server results before relying on the weekly trigger.

Example action for a deployment at `C:\Services\IPS_AutoUpdate`:

| Task Scheduler field | Value |
| --- | --- |
| Program/script | `C:\Services\IPS_AutoUpdate\.venv\Scripts\python.exe` |
| Add arguments | `-u "C:\Services\IPS_AutoUpdate\ipsupdate.py"` |
| Start in | `C:\Services\IPS_AutoUpdate` |

Use absolute paths. The Windows account must be able to read the script, Python environment, package, and private key. See [Microsoft's Task Scheduler reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks-create) for scheduling and run-account options.

### Optional: save console output for scheduled runs

The script prints results but does not itself create a log file. Task Scheduler history does not capture this console output. To preserve it, create the following optional `run_weekly.cmd` **in your private deployment**, after creating a restricted `logs` directory:

```bat
@echo off
cd /d "C:\Services\IPS_AutoUpdate"
if errorlevel 1 exit /b 1
"C:\Services\IPS_AutoUpdate\.venv\Scripts\python.exe" -u "C:\Services\IPS_AutoUpdate\ipsupdate.py" >> "C:\Services\IPS_AutoUpdate\logs\weekly.log" 2>&1
exit /b %errorlevel%
```

For this option, set **Program/script** to `C:\Windows\System32\cmd.exe`, **Add arguments** to `/c "C:\Services\IPS_AutoUpdate\run_weekly.cmd"`, and keep the same **Start in** directory. Restrict log access and arrange log rotation; the file is appended on each run and may include internal addresses or API error responses.

## Read the results

The summary includes each server's status, last stage reached, package cleanup result, and logout result:

- `SUCCESS`: update, publish, package cleanup, and logout succeeded.
- `WARNING`: update and publish succeeded, but cleanup or logout failed.
- `FAILED`: the update/publish workflow did not complete successfully.

The current script does **not** translate per-server failures into a nonzero process exit code. Task Scheduler reporting `0x0` does not establish that all IPS updates succeeded. Review the printed or captured summary and server state.

## Current behavior and limits

- There is no dry-run mode. Running the configured script starts real uploads and update requests.
- It selects by file modification time, not package version, and can reapply the same package on later runs.
- Each task wait defaults to 30 minutes. A request in progress may extend the wait; a timeout does not cancel remote work. Check server state before retrying.
- Failed runs can leave an uploaded package on a server. There is no rollback or automatic recovery.
- HTTPS certificate verification is disabled, and unknown SSH host keys are automatically accepted in the current Python implementation. The OpenSSH test's host verification does not change Paramiko's behavior.
- The current reviewed changes were checked with simulated responses, not a live Check Point environment. Validate version compatibility and permissions in an authorized lab.
- Never publish real API keys, private SSH keys, internal addresses, UPF packages, or operational logs.

## Functions

| Function | Responsibility |
| --- | --- |
| `sftp_upload` | Upload the package and attempt to close SFTP/SSH connections |
| `sftp_remove` | Remove the staged package and attempt to close connections |
| `wait_for_task` | Poll for task completion within the waiting period |
| `api_call` | Send an HTTPS POST request with the optional session ID |
| `process_server_update` | Coordinate one server's update and return its result |
