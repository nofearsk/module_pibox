# Deploy Relay Controller to Raspberry Pi

Deploy the relay web controller to a Raspberry Pi at the specified IP address.

## Arguments
- IP address of the target Raspberry Pi (required)
- Username defaults to `admin` unless specified as `user@ip`

## Instructions

Deploy the relay controller to the Pi at: $ARGUMENTS

Perform these steps in order:

1. **Copy files**: Use SCP to copy the `relay_web` folder to `/home/admin/` on the target Pi
   ```
   scp -o StrictHostKeyChecking=no -r "<project_root>" admin@<ip>:/home/admin/
   ```

2. **Run setup**: SSH into the Pi and run the setup script
   ```
   ssh -o StrictHostKeyChecking=no admin@<ip> "cd /home/admin/relay_web && chmod +x setup.sh && sudo ./setup.sh"
   ```

3. **Install service**: Copy the systemd service file and enable it
   ```
   ssh -o StrictHostKeyChecking=no admin@<ip> "sudo cp /home/admin/relay_web/relay-controller.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable relay-controller && sudo systemctl start relay-controller"
   ```

4. **Verify**: Check the service status
   ```
   ssh -o StrictHostKeyChecking=no admin@<ip> "sudo systemctl status relay-controller --no-pager"
   ```

5. **Report**: Tell the user the web interface URL: `http://<ip>:8080`

Use the TodoWrite tool to track progress through these steps.
