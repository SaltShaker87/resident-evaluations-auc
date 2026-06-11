# Exporting & Backups

This covers the three ways data leaves AUC, where each file ends up, and how
to send backups safely off the machine.

## 1. Export a summary as a PDF

On a resident's page, each **approved** summary has an **Export PDF** button.
It produces a clean one-page PDF (resident name, PGY year, date, and the
summary text).

**Where it goes:** your browser downloads it to that computer's normal
**Downloads** folder (on the Linux PC, `~/Downloads`). From there you can
print it, file it, or attach it to an email yourself.

## 2. Manual full backup (the button in Settings)

**Settings → Data Management → Download Full Backup (.zip)** downloads a
single `.zip` containing:

- `auc.db` — the whole database (residents, notes, follow-ups, summaries)
- `photos/` — every resident photo

**Where it goes:** the browser's **Downloads** folder. Drag it onto a USB
drive or into your OneDrive folder to keep it safe.

## 3. Automated daily backup (recommended — set this up once)

`setup.sh` installs a background job (a systemd *user timer*) that runs once a
day at 2 AM and writes a timestamped `auc-backup-YYYY-MM-DD_HHMMSS.zip` (same
contents as the manual backup) into a folder you choose. It keeps the last
**14 days** and deletes older ones automatically. It's safe to run while the
app is in use.

**Where it goes:** the folder named in `AUC_BACKUP_DIR`. By default that's
`~/auc-backups` on the Linux PC — which is *on the same disk as the app*, so
on its own it does **not** protect against that disk failing. The next step
fixes that.

### Send backups offsite with OneDrive

The point of a backup is that it survives the machine dying, so the backup
folder should be one that **syncs to your institutional OneDrive**. Microsoft
doesn't make a OneDrive app for Linux, but two well-supported tools do the
job. Pick one:

**Option A — the `onedrive` client (simplest day-to-day):**
```bash
sudo apt install onedrive        # or your distro's package
onedrive                         # first run prints a link to sign in
onedrive --synchronize           # do an initial sync
systemctl --user enable --now onedrive   # keep it syncing in the background
```
This creates a `~/OneDrive` folder that mirrors to the cloud.

**Option B — `rclone` (more control):**
```bash
sudo apt install rclone
rclone config                    # choose "onedrive", follow the prompts
# then mount or schedule a sync of a local folder to OneDrive
```

Once OneDrive is syncing a folder, point AUC's backups at it. Edit the backup
job:
```bash
nano ~/.config/systemd/user/auc-backup.service
```
Change the `AUC_BACKUP_DIR` line to a subfolder inside your synced OneDrive,
for example:
```
Environment=AUC_BACKUP_DIR=/home/youruser/OneDrive/auc-backups
```
Save, then reload:
```bash
systemctl --user daemon-reload
systemctl --user restart auc-backup.timer
```

Now every daily backup lands in OneDrive and is copied offsite automatically.

> **Privacy note:** institutional OneDrive is normally an approved place for
> this kind of data (it's usually covered by your organization's agreements).
> A personal Dropbox/Google Drive may not be. If unsure, check with your IT
> department before sending resident data to any cloud service.

## Checking & changing the schedule

- See when the next backup runs: `systemctl --user list-timers`
- Run a backup right now (good for testing): `systemctl --user start auc-backup.service`
- See the result/logs: `journalctl --user -u auc-backup.service`
- Change frequency: edit `OnCalendar=` in `~/.config/systemd/user/auc-backup.timer`
  (e.g. `OnCalendar=hourly`), then `systemctl --user daemon-reload`.
- Change how many days are kept: edit `AUC_BACKUP_KEEP_DAYS` in
  `~/.config/systemd/user/auc-backup.service`.

## Restoring from a backup

1. Stop the app: `systemctl --user stop auc`
2. Unzip the backup somewhere, e.g. `unzip auc-backup-2026-06-11_020000.zip -d restored`
3. Replace the live data: copy `restored/auc.db` to `auc/data/auc.db` and the
   `restored/photos/` files into `auc/data/photos/`.
4. Start the app: `systemctl --user start auc`

Your residents, notes, summaries, and photos will be exactly as they were when
that backup was taken.
