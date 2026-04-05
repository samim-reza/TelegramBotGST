# GST Result Telegram Notifier

This project logs into the GST admission portal, checks the **Admission Test Result** section, and sends a Telegram message when the result appears to be published.

## Files

- `result_notifier.py` - Main checker and notifier script
- `.env` - Your credentials and settings (ignored by git)
- `.env.example` - Template for `.env`
- `requirements.txt` - Python dependencies

## 1) Create Telegram Bot + Chat ID

### Create bot token

1. Open Telegram and search for **BotFather**.
2. Run `/newbot` and follow prompts.
3. Copy your bot token.

### Get your chat ID

1. Open your bot chat and send any message (for example: `hello`).
2. In a browser, open:

   `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`

3. Find `"chat":{"id":...}` and copy that ID.

## 2) Configure environment

Edit `.env`:

```env
GST_APPLICANT_ID=1514527
GST_PASSWORD=WL4WJC
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
POLL_SECONDS=900
STATE_FILE=state.json
NOTIFY_ON_UNPUBLISHED=false
```

## 3) Install dependencies

```bash
python -m pip install -r requirements.txt
```

If you use a virtual environment, activate it first.

## 4) Run

Run one check:

```bash
python result_notifier.py --once --verbose
```

Send Telegram test message:

```bash
python result_notifier.py --send-test-message --verbose
```

Send custom test message:

```bash
python result_notifier.py --send-test-message --test-message-text "GST bot test from server" --verbose
```

Run continuously:

```bash
python result_notifier.py --verbose
```

## 5) Run automatically in background (systemd user service)

Create file:

`~/.config/systemd/user/gst-result-notifier.service`

Example content:

```ini
[Unit]
Description=GST Result Telegram Notifier
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/samim01/Code/TelegramBotGST
ExecStart=/home/samim01/Code/TelegramBotGST/.venv/bin/python /home/samim01/Code/TelegramBotGST/result_notifier.py --verbose
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable --now gst-result-notifier.service
systemctl --user status gst-result-notifier.service
```

View logs:

```bash
journalctl --user -u gst-result-notifier.service -f
```

## Notes on publish detection

The script currently treats the result as **not published** if the result section contains phrases like:

- `Will be available after exam`
- `not published`
- `coming soon`

If the portal text changes in future, update `UNPUBLISHED_MARKERS` in `result_notifier.py`.
